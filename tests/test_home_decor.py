from datetime import date, timedelta
from io import BytesIO

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, HomeDecorAttachment, HomeDecorRequest, User
from moving_company.services.home_decor_service import clean_decor_answers
from moving_company.services import home_decor_service


PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


@pytest.fixture

def app(tmp_path):
    app = create_app(testing=True)
    app.config.update(WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=str(tmp_path))
    yield app
    with app.app_context():
        db.session.remove()


def login_customer(app, client):
    with app.app_context():
        customer = User(first_name='Decor', last_name='Customer', email='decor-customer@example.com', phone='+2348012345678')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    return customer_id


def complete_draft(client):
    start = client.get('/booking/home-decor')
    assert start.status_code == 302
    request_url = start.headers['Location']
    request_id = int(request_url.split('/')[-3])
    step1 = client.post(request_url, data={
        'action': 'continue', 'full_name': 'Decor Customer', 'phone': '+2348012345678',
        'email': 'decor-customer@example.com', 'contact_method': 'WhatsApp',
    })
    assert step1.status_code == 302
    step2_url = step1.headers['Location']
    step2 = client.post(step2_url, data={
        'action': 'continue', 'property_type': 'Apartment', 'property_status': 'Newly acquired',
        'location': '12 Example Road, Abuja', 'location_search': '12 Example Road, Abuja',
        'location_address': '12 Example Road, Abuja', 'location_city': 'Abuja',
        'location_state': 'Federal Capital Territory', 'location_latitude': '9.07',
        'location_longitude': '7.49', 'location_provider': 'manual', 'bedroom_count': '2',
        'property_size': '90 m2', 'inspection_access': 'Available',
    })
    assert step2.status_code == 302
    step3 = client.post(step2.headers['Location'], data={
        'action': 'continue', 'decor_services': ['Living room makeover', 'Other'],
        'rooms': ['Living room', 'Bedroom'], 'other_service': 'Entryway styling',
    })
    assert step3.status_code == 302
    step4 = client.post(step3.headers['Location'], data={
        'action': 'continue', 'design_style': 'Modern', 'preferred_colours': 'Green and white',
        'desired_outcome': 'Create a calm, functional family space.', 'retain_items': 'Existing sofa',
        'items_to_source': 'Rug and side table', 'room_measurements': 'Living room 5m by 4m',
        'reference_links': ['https://example.com/inspiration'],
    })
    assert step4.status_code == 302
    step5 = client.post(step4.headers['Location'], data={
        'action': 'continue', 'budget_range': '250k_500k', 'project_scope': 'Design and styling',
        'source_materials': 'yes', 'material_requirements': 'Washable paint',
    })
    assert step5.status_code == 302
    return request_id, step5.headers['Location']


def final_payload():
    start = (date.today() + timedelta(days=14)).isoformat()
    completion = (date.today() + timedelta(days=30)).isoformat()
    return {
        'action': 'submit', 'preferred_start_date': start,
        'desired_completion_date': completion, 'timeline_flexible': 'yes',
        'preferred_consultation_date': (date.today() + timedelta(days=5)).isoformat(),
        'instructions': 'Please contact before arranging an inspection.', 'contact_consent': 'on',
    }


def test_customer_can_save_and_submit_decor_enquiry_with_existing_booking_reference(app, monkeypatch):
    client = app.test_client()
    customer_id = login_customer(app, client)
    monkeypatch.setattr(home_decor_service, 'configured_budget_ranges', lambda: [
        {'value': '250k_500k', 'label': '₦250,000–₦500,000'},
    ])
    monkeypatch.setattr('moving_company.routes.home_decor.geocode_address', lambda address: {
        'latitude': 9.07, 'longitude': 7.49, 'city': 'Abuja', 'state': 'Federal Capital Territory',
        'country': 'Nigeria', 'formatted_address': address, 'provider': 'test',
    })
    request_id, final_url = complete_draft(client)
    review = client.get(final_url)
    assert review.status_code == 200
    assert b'Review your request' in review.data
    submitted = client.post(final_url, data=final_payload())
    assert submitted.status_code == 302
    assert submitted.headers['Location'].endswith(f'/confirmation')
    with app.app_context():
        decor_request = HomeDecorRequest.query.filter_by(id=request_id).one()
        booking = Booking.query.filter_by(id=decor_request.booking_id).one()
        assert decor_request.customer_id == customer_id
        assert decor_request.status == 'New'
        assert decor_request.submitted_at is not None
        assert decor_request.public_reference.startswith('HD-')
        assert booking.primary_service == 'home_decor'
        assert booking.booking_request_id.startswith('BR-')
        assert booking.quote_status == 'Quote Required'
        assert booking.payment_status == 'Pending'
        assert booking.workflow_data['property']['service_answers']['design']['rooms'] == ['Living room', 'Bedroom']
    confirmation = client.get(submitted.headers['Location'])
    assert confirmation.status_code == 200
    assert decor_request.public_reference.encode() in confirmation.data
    repeated = client.post(final_url, data=final_payload())
    assert repeated.status_code == 302
    with app.app_context():
        assert HomeDecorRequest.query.filter_by(id=request_id).count() == 1
        assert Booking.query.filter_by(primary_service='home_decor').count() == 1


def test_invalid_service_options_dates_and_consent_are_rejected(app, monkeypatch):
    client = app.test_client()
    login_customer(app, client)
    _, final_url = complete_draft(client)
    bad = final_payload() | {'desired_completion_date': (date.today() - timedelta(days=1)).isoformat()}
    rejected = client.post(final_url, data=bad)
    assert rejected.status_code == 200
    assert b'cannot precede' in rejected.data
    with app.app_context():
        assert Booking.query.filter_by(primary_service='home_decor').count() == 0
    bad_service = {
        'decor_services': ['Not a real service'], 'rooms': ['Bedroom'],
        'full_name': 'Decor Customer', 'phone': '+2348012345678', 'email': '', 'contact_method': 'Phone call',
        'property_type': 'Apartment', 'property_status': 'Vacant', 'inspection_access': 'Available',
        'location': 'Abuja', 'bedroom_count': '2', 'budget_range': '250k_500k', 'project_scope': 'Complete project',
        'design_style': 'Modern', 'desired_outcome': 'Ready room', 'source_materials': 'no',
        'timeline_flexible': 'no', 'contact_consent': 'on',
    }
    from werkzeug.datastructures import MultiDict
    with app.app_context(), pytest.raises(ValueError, match='valid décor service'):
        clean_decor_answers(MultiDict(bad_service), None)


def test_decor_upload_types_owner_access_and_request_association(app, monkeypatch):
    client = app.test_client()
    owner_id = login_customer(app, client)
    other = app.test_client()
    with app.app_context():
        another = User(first_name='Other', last_name='Person', email='other-decor@example.com', phone='')
        another.set_password('password123')
        db.session.add(another)
        db.session.commit()
        other_id = another.id
    with other.session_transaction() as session:
        session['_user_id'] = str(other_id)
        session['_fresh'] = True
    request_id, _ = complete_draft(client)
    step4_url = f'/booking/home-decor/{request_id}/step/4'
    upload = client.post(step4_url, data={
        'action': 'upload', 'design_style': 'Modern', 'desired_outcome': 'Comfortable room',
        'current_space_files': (BytesIO(PNG_BYTES), 'room.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert upload.status_code == 302
    with app.app_context():
        attachment = HomeDecorAttachment.query.filter_by(request_id=request_id).one()
        assert attachment.uploaded_by_id == owner_id
        assert attachment.category == 'current_space'
        attachment_url = f'/booking/home-decor/{request_id}/attachments/{attachment.id}'
    assert client.get(attachment_url).status_code == 200
    assert other.get(attachment_url).status_code == 404
    rejected = client.post(step4_url, data={
        'action': 'upload', 'floor_plan_files': (BytesIO(b'not a pdf'), 'plan.pdf', 'application/pdf'),
    }, content_type='multipart/form-data')
    assert rejected.status_code == 200
    with app.app_context():
        assert HomeDecorAttachment.query.filter_by(request_id=request_id).count() == 1
    wrong_category = client.post(step4_url, data={
        'action': 'upload', 'inspiration_files': (BytesIO(b'%PDF-1.7\nbody'), 'plan.pdf', 'application/pdf'),
    }, content_type='multipart/form-data')
    assert wrong_category.status_code == 200
    assert b'only be uploaded as floor plans' in wrong_category.data
    with app.app_context():
        assert HomeDecorAttachment.query.filter_by(request_id=request_id).count() == 1


def test_admin_decor_workflow_requires_quote_before_approval(app, monkeypatch):
    admin = User(first_name='Operations', last_name='Admin', email='decor-admin@example.com', phone='', role='admin')
    admin.set_password('password123')
    with app.app_context():
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    with app.app_context():
        customer = User(first_name='Decor', last_name='Customer', email='admin-test-customer@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id
    customer_client = app.test_client()
    with customer_client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    monkeypatch.setattr('moving_company.routes.home_decor.geocode_address', lambda address: {
        'latitude': 9.07, 'longitude': 7.49, 'city': 'Abuja', 'state': 'Federal Capital Territory',
        'country': 'Nigeria', 'formatted_address': address, 'provider': 'test',
    })
    request_id, review_url = complete_draft(customer_client)
    customer_client.post(review_url, data=final_payload())
    queue = client.get('/admin/home-decor')
    assert queue.status_code == 200
    detail = client.get(f'/admin/home-decor/{request_id}')
    assert detail.status_code == 200
    assert b'Assignment and internal notes' in detail.data
    with app.app_context():
        decor_request = HomeDecorRequest.query.filter_by(id=request_id).one()
        status_url = f'/admin/home-decor/{request_id}/status'
    invalid = client.post(status_url, data={'status': 'Approved'})
    assert invalid.status_code == 302
    with app.app_context():
        assert HomeDecorRequest.query.filter_by(id=request_id).one().status == 'New'
        attachment = HomeDecorAttachment(
            request_id=request_id, uploaded_by_id=customer_id,
            storage_key='home-decor/private/test.png', original_filename='test.png',
            mime_type='image/png', file_size=len(PNG_BYTES), category='current_space',
        )
        db.session.add(attachment)
        db.session.commit()
        attachment_id = attachment.id
    private_url = f'/admin/home-decor/{request_id}/attachments/{attachment_id}'
    assert client.get(private_url).status_code == 200
    customer_client2 = app.test_client()
    with customer_client2.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    assert customer_client2.get(f'/admin/home-decor').status_code == 403


def test_decor_operations_quote_requires_availability_and_customer_acceptance(app, monkeypatch):
    admin = User(first_name='Operations', last_name='Admin', email='decor-quote-admin@example.com', phone='', role='admin')
    admin.set_password('password123')
    with app.app_context():
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    admin_client = app.test_client()
    with admin_client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    customer_client = app.test_client()
    customer_id = login_customer(app, customer_client)
    monkeypatch.setattr('moving_company.routes.home_decor.geocode_address', lambda address: {
        'latitude': 9.07, 'longitude': 7.49, 'city': 'Abuja', 'state': 'Federal Capital Territory',
        'country': 'Nigeria', 'formatted_address': address, 'provider': 'test',
    })
    request_id, final_url = complete_draft(customer_client)
    customer_client.post(final_url, data=final_payload())
    status_url = f'/admin/home-decor/{request_id}/status'
    admin_client.post(status_url, data={'status': 'Under Review'})
    admin_client.post(status_url, data={'status': 'Inspection/Consultation'})
    quote_url = f'/admin/home-decor/{request_id}/quote'
    rejected_quote = admin_client.post(quote_url, data={
        'amount': '400000', 'reason': 'Design and styling scope',
        'availability_note': 'Operations availability check recorded.',
    })
    assert rejected_quote.status_code == 302
    with app.app_context():
        decor_request = HomeDecorRequest.query.filter_by(id=request_id).one()
        assert decor_request.booking.quote_status == 'Ready'
        assert decor_request.booking.payment_status == 'Pending'
        assert decor_request.status == 'Quotation Prepared'
        booking_request_id = decor_request.booking.booking_request_id
    quote_page = customer_client.get(f'/booking/{booking_request_id}/quote')
    assert quote_page.status_code == 200
    with app.app_context():
        assert HomeDecorRequest.query.filter_by(id=request_id).one().status == 'Awaiting Client Approval'
    accepted = customer_client.post(f'/booking/{booking_request_id}/quote', data={'action': 'accept_quote'})
    assert accepted.status_code == 302
    assert accepted.headers['Location'].endswith(f'/booking/{booking_request_id}/payment')
    with app.app_context():
        decor_request = HomeDecorRequest.query.filter_by(id=request_id).one()
        assert decor_request.status == 'Approved'
        assert decor_request.booking.quote_status == 'Accepted'
        assert decor_request.booking.payment_status == 'Pending'
