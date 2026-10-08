from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, BookingPhoto, BookingItem, Truck, User, VehicleImage
from moving_company.services.booking_engine_service import create_booking_request


PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def make_image(name='photo.png', data=PNG_BYTES):
    return (BytesIO(data), name)


def create_user(app, email, role='customer'):
    with app.app_context():
        user = User(first_name='Test', last_name='User', email=email, phone='', role=role)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()
        return user.id


def create_booking(app, customer_id):
    with app.app_context():
        booking = create_booking_request(
            customer_id=customer_id,
            location={
                'pickup_address': '12 Yakubu Gowon Way',
                'pickup_city': 'Abuja',
                'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue',
                'destination_city': 'Abuja',
                'destination_state': 'FCT',
            },
        )
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
        return booking.id, booking.booking_request_id


def login(client, email):
    response = client.post('/auth/login', data={'email': email, 'password': 'password123'})
    assert response.status_code == 302, response.get_data(as_text=True)


@pytest.fixture
def app(tmp_path):
    app = create_app(testing=True)
    app.config.update(WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=str(tmp_path))
    yield app
    with app.app_context():
        db.session.remove()


def test_customer_attachment_lifecycle_is_scoped_to_booking_owner(app, tmp_path):
    owner_id = create_user(app, 'owner@example.com')
    create_user(app, 'other@example.com')
    booking_id, request_id = create_booking(app, owner_id)

    item = BookingItem(
        booking_id=booking_id,
        name='Glass table',
        category='Fragile / Valuable',
        quantity=1,
    )
    with app.app_context():
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    client = app.test_client()
    login(client, 'owner@example.com')
    upload = client.post(
        f'/customer/bookings/{booking_id}/attachments',
        data={
            'category': 'inventory',
            'booking_item_id': str(item_id),
            'attachments': make_image('table.png'),
        },
    )
    assert upload.status_code == 302

    with app.app_context():
        photo = BookingPhoto.query.filter_by(booking_id=booking_id).one()
        assert photo.booking_item_id == item_id
        assert photo.uploaded_by_id == owner_id
        assert photo.category == 'inventory'
        path = Path(tmp_path) / photo.file_name
        assert path.is_file()
        assert path.read_bytes() == PNG_BYTES

    assert client.get(f'/customer/bookings/{booking_id}/attachments/{photo.file_name.rsplit("/", 1)[-1]}').status_code == 200
    assert client.post(f'/customer/bookings/{booking_id}/attachments/{photo.id}/delete').status_code == 302
    assert not Path(tmp_path, photo.file_name).exists()
    with app.app_context():
        assert BookingPhoto.query.filter_by(id=photo.id).count() == 0

    other_client = app.test_client()
    login(other_client, 'other@example.com')
    assert other_client.get(f'/customer/bookings/{booking_id}/attachments/{photo.file_name.rsplit("/", 1)[-1]}').status_code == 404
    assert other_client.post(f'/customer/bookings/{booking_id}/attachments/{photo.id}/delete').status_code == 404


def test_invalid_attachment_is_rejected_without_persisting_or_writing_file(app, tmp_path):
    owner_id = create_user(app, 'owner@example.com')
    booking_id, _ = create_booking(app, owner_id)
    client = app.test_client()
    login(client, 'owner@example.com')

    response = client.post(
        f'/customer/bookings/{booking_id}/attachments',
        data={'category': 'property', 'attachments': make_image('fake.gif', b'not an image')},
    )
    assert response.status_code == 302
    with app.app_context():
        assert BookingPhoto.query.count() == 0
    assert not any(path.is_file() for path in tmp_path.rglob('*'))


def test_admin_attachment_access_is_admin_only_and_truck_gallery_lifecycle_is_complete(app, tmp_path):
    create_user(app, 'admin@example.com', role='admin')
    owner_id = create_user(app, 'owner@example.com')
    booking_id, _ = create_booking(app, owner_id)
    with app.app_context():
        booking = Booking.query.get(booking_id)
        photo = BookingPhoto(
            booking_id=booking.id,
            file_name='booking/admin-photo.png',
            original_filename='admin-photo.png',
            mime_type='image/png',
            file_size=len(PNG_BYTES),
            category='booking',
        )
        db.session.add(photo)
        db.session.flush()
        Path(tmp_path, 'booking').mkdir()
        Path(tmp_path, photo.file_name).write_bytes(PNG_BYTES)
        truck = Truck(
            registration_number='KJA-001',
            vehicle_type='Canter',
            capacity='20 m3',
            status='Available',
            rate=100000,
        )
        unpictured_truck = Truck(
            registration_number='KJA-002',
            vehicle_type='Pickup',
            capacity='1 Ton',
            status='Available',
            rate=150000,
        )
        db.session.add_all([truck, unpictured_truck])
        db.session.commit()
        truck_id = truck.id
        unpictured_truck_id = unpictured_truck.id
        attachment_filename = photo.file_name.rsplit('/', 1)[-1]

    admin_client = app.test_client()
    login(admin_client, 'admin@example.com')
    upload = admin_client.post(
        f'/admin/trucks/{truck_id}/images',
        data=MultiDict([
            ('images', make_image('first.png')),
            ('images', make_image('second.png')),
        ]),
    )
    assert upload.status_code == 302

    with app.app_context():
        images = list(
            VehicleImage.query.filter_by(truck_id=truck_id).order_by(VehicleImage.sort_order, VehicleImage.id).all()
        )
        assert len(images) == 2
        assert [image.sort_order for image in images] == [0, 1]
        first, second = images
        assert first.is_primary is False
        assert second.is_primary is False

    primary = admin_client.post(
        f'/admin/trucks/{truck_id}/images/{first.id}',
        data={'action': 'primary'},
    )
    assert primary.status_code == 302
    with app.app_context():
        assert VehicleImage.query.filter_by(truck_id=truck_id, is_primary=True).count() == 1
        assert VehicleImage.query.filter_by(truck_id=truck_id, id=first.id).one().is_primary

    reorder = admin_client.post(
        f'/admin/trucks/{truck_id}/images/{second.id}',
        data={'action': 'move', 'direction': 'up'},
    )
    assert reorder.status_code == 302
    with app.app_context():
        images = list(
            VehicleImage.query.filter_by(truck_id=truck_id).order_by(VehicleImage.sort_order, VehicleImage.id).all()
        )
        assert [image.id for image in images] == [second.id, first.id]

    assert admin_client.get(f'/admin/bookings/{booking_id}/attachments/{attachment_filename}').status_code == 200
    assert admin_client.get(f'/admin/trucks/{truck_id}/images/{second.file_name.rsplit("/", 1)[-1]}').status_code == 200

    gallery = admin_client.get('/admin/trucks')
    assert gallery.status_code == 200
    gallery_html = gallery.get_data(as_text=True)
    assert 'KJA-001' in gallery_html
    assert 'KJA-002' in gallery_html
    assert 'Add vehicle images' in gallery_html
    assert second.original_filename in gallery_html
    assert first.original_filename in gallery_html
    assert 'truck-demo.svg' in gallery_html
    assert f'/admin/trucks/{unpictured_truck_id}/images' in gallery_html

    delete = admin_client.post(f'/admin/trucks/{truck_id}/images/{second.id}', data={'action': 'delete'})
    assert delete.status_code == 302
    with app.app_context():
        assert VehicleImage.query.filter_by(id=second.id).count() == 0
        assert not Path(tmp_path, second.file_name).exists()

    customer_client = app.test_client()
    login(customer_client, 'owner@example.com')
    assert customer_client.get(f'/admin/trucks/{truck_id}/images/{first.file_name.rsplit("/", 1)[-1]}').status_code == 403
    assert customer_client.get(f'/admin/bookings/{booking_id}/attachments/{attachment_filename}').status_code == 403
