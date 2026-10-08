from datetime import date, timedelta
from io import BytesIO
import re
from types import SimpleNamespace

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, BookingEvent, BookingQuoteLine, Payment, User, VehicleType
from moving_company.services.booking_engine_service import (
    BULK_ITEM_OPTIONS, INVENTORY_CATALOG, accept_quote, calculate_quote, create_booking_request,
    catalogue_items_for_service, inventory_requirements, preview_move_duration,
    replace_inventory, save_vehicle_selection, save_workflow_section, vehicle_options,
)
from moving_company.services import booking_engine_service
from moving_company.services import payment_provider_service
from moving_company.services.payment_provider_service import initialize_payment, process_stripe_webhook


@pytest.fixture
def app():
    app = create_app(testing=True)
    yield app
    with app.app_context():
        db.session.remove()


def create_request():
    return create_booking_request(location={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
        'pickup_latitude': 9.05, 'pickup_longitude': 7.49, 'pickup_formatted_address': '12 Yakubu Gowon Way, Abuja',
        'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        'destination_latitude': 9.08, 'destination_longitude': 7.45, 'destination_formatted_address': 'Plot 405 Constitution Avenue, Abuja',
        'distance_km': 20, 'pickup_floor': 'Ground', 'destination_floor': 'Ground',
    })


def csrf_token(response):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def complete_quote(booking):
    save_workflow_section(booking, 'property', {
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 2, 'floors': 1,
    })
    replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture', 'size': 'large'}])
    save_workflow_section(booking, 'services', {'packing': 'partial'})
    save_workflow_section(booking, 'schedule', {
        'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        'flexible_date': False, 'flexible_time': False,
    })
    return calculate_quote(booking)


def test_request_has_one_durable_identifier_and_audit_trail(app):
    with app.app_context():
        booking = create_request()
        assert booking.booking_request_id.startswith('BR-')
        assert booking.booking_number == booking.booking_request_id
        assert booking.workflow_state == 'LOCATION_COMPLETED'
        assert [event.action for event in booking.events] == ['location.completed']
        assert booking.distance_km == 20


def test_quote_calculates_operational_requirements_from_persisted_inventory(app):
    with app.app_context():
        booking = create_request()
        calculation = complete_quote(booking)

        assert booking.workflow_state == 'QUOTE_READY'
        assert booking.quote_status == 'Ready'
        assert calculation['inventory']['item_count'] == 1
        assert calculation['inventory']['volume_m3'] > 0
        assert calculation['inventory']['weight_kg'] > 0
        assert calculation['vehicle']['name']
        assert calculation['workforce']['movers'] >= 2
        assert calculation['duration_minutes'] > 0
        assert booking.estimated_total > 0
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id).count() > 0
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='quote.calculated').count() == 1


def test_inventory_update_invalidates_quote_and_recalculates_vehicle_and_total(app):
    with app.app_context():
        booking = create_request()
        complete_quote(booking)
        original_total = booking.estimated_total
        original_vehicle = booking.recommended_vehicle_type_id

        replace_inventory(booking, [
            {'name': 'Sofa', 'quantity': 1, 'size': 'large'},
            {'name': 'Wardrobe', 'quantity': 3, 'size': 'large', 'large': True},
        ])
        assert booking.workflow_state == 'INVENTORY_COMPLETED'
        assert booking.quote_status == 'Draft'
        assert booking.estimated_total == 0
        assert booking.calculation_data == {}
        assert booking.estimated_duration_minutes is None
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id).count() == 0

        booking.workflow_state = 'SCHEDULE_COMPLETED'
        revised = calculate_quote(booking)
        assert booking.estimated_total != original_total
        assert revised['inventory']['item_count'] == 4
        assert booking.recommended_vehicle_type_id >= original_vehicle


def test_inventory_catalog_estimates_fragile_items_and_optional_overrides(app):
    with app.app_context():
        booking = create_request()
        assert {'Living Room', 'Bedroom', 'Dining', 'Kitchen', 'Electronics', 'Office Furniture', 'Bathroom', 'Outdoor / Balcony', 'Garage / Storage', 'Children', 'Fitness', 'Fragile / Valuable', 'Materials'} <= set(INVENTORY_CATALOG)
        replace_inventory(booking, [
            {'name': 'Glass table', 'category': 'Fragile / Valuable', 'quantity': 1},
            {'name': 'Piano', 'category': 'Fragile / Valuable', 'quantity': 1, 'details': {'piano_type': 'grand'}, 'estimated_weight_kg': 310},
            {'name': 'Custom crate', 'quantity': 2, 'estimated_volume_m3': 1.25, 'estimated_weight_kg': 40},
        ])
        glass, piano, custom = booking.items
        assert glass.fragile is True
        assert piano.special_handling is True
        assert piano.details['piano_type'] == 'grand'
        assert custom.details['estimated_volume_m3'] == 1.25
        requirements = inventory_requirements(booking)
        assert requirements['item_count'] == 4
        assert requirements['fragile_items'] == 1
        assert requirements['special_items'] == 1
        assert requirements['weight_kg'] > 300


def test_vehicle_override_changes_server_quote_and_service_changes_duration(app):
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1})
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'size': 'large'}])
        save_workflow_section(booking, 'services', {})
        save_workflow_section(booking, 'schedule', {
            'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        })
        no_service_duration = preview_move_duration(booking)['duration_minutes']
        options = vehicle_options(booking)
        recommended = next(option for option in options if option['recommended'])
        save_vehicle_selection(booking, recommended['id'])
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        save_workflow_section(booking, 'services', {'packing': 'full', 'additional_hours': 48})
        assert 'additional_hours' not in booking.workflow_data['services']
        save_workflow_section(booking, 'schedule', {
            'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        })
        service_duration = preview_move_duration(booking)['duration_minutes']
        assert service_duration > no_service_duration
        calculate_quote(booking)
        original_vehicle_id = booking.recommended_vehicle_type_id
        original_total = booking.estimated_total
        larger_vehicle = max(VehicleType.query.filter_by(is_active=True).all(), key=lambda vehicle: vehicle.capacity_m3)
        save_vehicle_selection(booking, larger_vehicle.id)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        calculate_quote(booking)
        assert booking.recommended_vehicle_type_id == larger_vehicle.id
        assert booking.estimated_total != original_total or larger_vehicle.id == original_vehicle_id


def test_vehicle_options_recover_empty_active_catalogue_and_allow_multiple_units(app):
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {
            'property_type': 'Warehouse', 'floors': 1,
        })
        replace_inventory(booking, [{
            'name': 'Warehouse pallet load', 'quantity': 1,
            'estimated_volume_m3': 50, 'estimated_weight_kg': 9000,
        }])
        for vehicle in VehicleType.query.all():
            vehicle.is_active = False
        db.session.commit()

        options = vehicle_options(booking)
        recommended = next(option for option in options if option['recommended'])
        assert options
        assert recommended['selected'] is True
        assert recommended['quantity'] > 1
        assert recommended['capacity_exceeded'] is True

        save_vehicle_selection(booking, recommended['id'])
        assert booking.workflow_data['vehicle']['selected_type_id'] == recommended['id']


def test_inventory_and_service_pages_have_previous_and_no_manual_service_hours(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1})
        replace_inventory(booking, [{'name': 'Wardrobe', 'quantity': 1}])
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id
    inventory = client.get(f'/booking/{request_id}/inventory')
    assert inventory.status_code == 200
    inventory_html = inventory.get_data(as_text=True)
    assert 'Living Room' in inventory_html and 'Fragile / Valuable' in inventory_html
    assert 'Vehicle recommendation' in inventory_html
    assert 'Previous: Property' in inventory_html
    services = client.get(f'/booking/{request_id}/services')
    assert services.status_code == 200
    services_html = services.get_data(as_text=True)
    assert 'Packing &amp; Protection' in services_html
    assert 'Additional service hours' not in services_html
    assert 'Previous: Inventory' in services_html


def test_customer_can_add_catalogue_items_with_quantity_only(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1})
        request_id = booking.booking_request_id

    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    with app.app_context():
        catalogue_item = catalogue_items_for_service('residential')[0]['catalog_items'][0]
    response = client.post(
        f'/booking/{request_id}/inventory',
        data={
            'action': 'add_catalogue',
            f"inventory_item_id_{catalogue_item.id}": str(catalogue_item.id),
            f"inventory_item_quantity_{catalogue_item.id}": '2',
        },
    )
    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert len(booking.inventory) == 1
        assert booking.inventory[0].quantity == 2
        selection_id = booking.inventory[0].id

    invalid_response = client.post(
        f'/booking/{request_id}/inventory',
        data={
            'action': 'add_catalogue',
            f"inventory_item_id_{catalogue_item.id}": str(catalogue_item.id),
            f"inventory_item_quantity_{catalogue_item.id}": '501',
        },
    )
    assert invalid_response.status_code == 200
    assert b'Inventory quantity must be between 1 and 500.' in invalid_response.data
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.inventory[0].quantity == 2

    continued = client.post(
        f'/booking/{request_id}/inventory',
        data={
            'action': 'save_and_continue',
            'selection_id[]': str(selection_id),
            'quantity[]': '2',
        },
    )
    assert continued.status_code == 302
    assert continued.location.endswith(f'/booking/{request_id}/services')


def test_catalogue_is_scoped_to_selected_property_and_packing_scope(app):
    with app.app_context():
        apartment = catalogue_items_for_service('residential', property_type='Apartment')
        house = catalogue_items_for_service('residential', property_type='House')
        office = catalogue_items_for_service('commercial', property_type='Office')
        shop = catalogue_items_for_service('commercial', property_type='Shop')
        warehouse = catalogue_items_for_service('commercial', property_type='Warehouse')
        hotel = catalogue_items_for_service('commercial', property_type='Hotel')
        estate_unit = catalogue_items_for_service('commercial', property_type='Estate', unit_type='Duplex')
        construction = catalogue_items_for_service('commercial', property_type='Construction Site')
        packing_office = catalogue_items_for_service('packing', property_type='Office')

        def names(groups):
            return {item.name for group in groups for item in group['catalog_items']}

        apartment_names = names(apartment)
        assert 'Single bed' in apartment_names
        assert 'Lawn mower' not in apartment_names
        assert 'Lawn mower' in names(house)
        assert 'Executive desk' in names(office) and 'Single bed' not in names(office)
        assert 'Gondola shelf' in names(shop) and 'Executive desk' not in names(shop)
        assert 'Pallet jack' in names(warehouse) and 'Stock / Cartons' in names(warehouse)
        assert 'Linen carts' in names(hotel)
        assert 'Single bed' in names(estate_unit) and 'Security desk' in names(estate_unit)
        assert 'Cement bags' in names(construction) and 'Steel rods' in names(construction)
        assert 'Executive desk' in names(packing_office) and 'Single bed' not in names(packing_office)
        assert BULK_ITEM_OPTIONS['Warehouse']['Stock / Cartons'] == 'cartons'
        assert BULK_ITEM_OPTIONS['Construction Site']['Cement bags'] == 'bags'


def test_bulk_material_submission_saves_unit_estimates_and_flags(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        booking.primary_service = 'commercial'
        save_workflow_section(booking, 'property', {
            'property_type': 'Construction Site', 'floors': 1,
        })
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    response = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'add_bulk', 'bulk_item_type': 'Cement bags', 'bulk_unit': 'bags',
        'bulk_quantity': '50', 'bulk_volume_m3': '0.02', 'bulk_weight_kg': '50',
        'bulk_fragile': 'on', 'bulk_special_handling': 'on',
    })
    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        line = booking.inventory[0]
        selection_id = line.id
        assert line.display_name == 'Cement bags (bags)'
        assert line.quantity == 50
        assert line.notes == 'Unit: bags'
        assert line.estimated_volume_m3 == 0.02
        assert line.estimated_weight_kg == 50
        assert line.fragile is True and line.special_handling is True

    continued = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'save_and_continue', 'selection_id[]': str(selection_id), 'quantity[]': '50',
    })
    assert continued.status_code == 302
    assert continued.location.endswith(f'/booking/{request_id}/services')
    with app.app_context():
        line = Booking.query.filter_by(booking_request_id=request_id).one().inventory[0]
        assert line.display_name == 'Cement bags (bags)'
        assert line.notes == 'Unit: bags'
        assert line.estimated_volume_m3 == 0.02
        assert line.estimated_weight_kg == 50
        assert line.fragile is True and line.special_handling is True

def test_inventory_photo_upload_is_saved_with_booking_and_owner_checked(app, tmp_path):
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['UPLOAD_FOLDER'] = tmp_path
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    png_payload = bytes.fromhex(
        '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
        '0000000a49444154789c63600000020001e2a9c93c0000000049454e44ae426082'
    )
    response = client.post(f'/booking/{request_id}/inventory', data={
        'item_name[]': 'Glass table', 'item_quantity[]': '1',
        'item_category[]': 'Fragile / Valuable', 'item_size[]': 'medium',
        'item_photo_0': (BytesIO(png_payload), 'table.png'),
    })
    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        photo_path = booking.items[0].photo_path
        assert photo_path.startswith('inventory/')
    photo = client.get(f'/booking/{request_id}/inventory/photos/{photo_path}')
    assert photo.status_code == 200
    assert photo.data == png_payload

    other_client = app.test_client()
    assert other_client.get(f'/booking/{request_id}/inventory/photos/{photo_path}').status_code == 404


def test_invalid_stage_transition_does_not_skip_required_information(app):
    with app.app_context():
        booking = create_request()
        from moving_company.services.booking_engine_service import transition_stage

        with pytest.raises(ValueError, match='Complete the property step'):
            transition_stage(booking, 'inventory')


def test_payment_is_not_faked_when_provider_is_unconfigured(app, monkeypatch):
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    monkeypatch.setitem(app.config, 'STRIPE_SECRET_KEY', None)
    with app.app_context():
        booking = create_request()
        complete_quote(booking)
        accept_quote(booking, user_id=None)

        with pytest.raises(ValueError, match='not configured'):
            initialize_payment(booking, 'customer@example.com')

        assert Payment.query.filter_by(booking_id=booking.id).count() == 0
        assert booking.workflow_state == 'QUOTE_ACCEPTED'
        assert Booking.query.filter_by(booking_request_id=booking.booking_request_id).count() == 1


def test_payment_stage_renders_deposit_and_secure_payment_action(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    monkeypatch.setitem(app.config, 'STRIPE_SECRET_KEY', None)
    client = app.test_client()
    with app.app_context():
        owner = User(first_name='Payment', last_name='Customer', email='payment-customer@example.com', phone='')
        owner.set_password('password123')
        db.session.add(owner)
        db.session.flush()
        booking = create_request()
        booking.customer_id = owner.id
        complete_quote(booking)
        accept_quote(booking, owner.id)
        request_id = booking.booking_request_id

    login = client.post('/auth/login', data={
        'email': 'payment-customer@example.com', 'password': 'password123',
    })
    assert login.status_code == 302
    response = client.get(f'/booking/{request_id}/payment')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Secure your moving date' in html
    assert 'Deposit due now' in html
    assert 'Continue to secure payment' in html

    retry = client.post(f'/booking/{request_id}/payment')
    assert retry.status_code == 200
    assert b'Online payments are not configured yet' in retry.data


def test_stripe_checkout_creates_and_reuses_deposit_session(app, monkeypatch):
    app.config['STRIPE_SECRET_KEY'] = 'sk_test_unit_test'
    with app.app_context():
        booking = create_request()
        complete_quote(booking)
        accept_quote(booking, user_id=None)
        request_id = booking.booking_request_id
        expected_deposit = booking.deposit_amount

        calls = []
        session = SimpleNamespace(id='cs_test_checkout_1', url='https://checkout.stripe.test/session', status='open')

        class FakeSessions:
            def create(self, params, options=None):
                calls.append((params, options))
                return session

            def retrieve(self, _session_id):
                return session

        fake_client = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=FakeSessions())))
        monkeypatch.setattr(payment_provider_service, '_stripe_client', lambda: fake_client)
        monkeypatch.setattr(
            payment_provider_service, 'url_for',
            lambda endpoint, **_values: f'https://chigo.example/{endpoint}',
        )

        payment = initialize_payment(booking, 'customer@example.com')
        assert payment.gateway == 'stripe'
        assert payment.payment_reference == 'cs_test_checkout_1'
        assert payment.amount == expected_deposit
        assert payment.authorization_url == session.url
        assert calls[0][0]['line_items'][0]['price_data']['currency'] == 'ngn'
        assert calls[0][0]['line_items'][0]['price_data']['unit_amount'] == round(expected_deposit * 100)
        assert '{CHECKOUT_SESSION_ID}' in calls[0][0]['success_url']

        reused = initialize_payment(booking, 'customer@example.com')
        assert reused.id == payment.id
        assert len(calls) == 1


def test_stripe_webhook_marks_matching_paid_session_confirmed(app, monkeypatch):
    app.config['STRIPE_SECRET_KEY'] = 'sk_test_unit_test'
    app.config['STRIPE_WEBHOOK_SECRET'] = 'whsec_unit_test'
    with app.app_context():
        owner = User(first_name='Stripe', last_name='Customer', email='stripe-customer@example.com', phone='')
        owner.set_password('password123')
        db.session.add(owner)
        db.session.flush()
        booking = create_request()
        booking.customer_id = owner.id
        complete_quote(booking)
        accept_quote(booking, owner.id)

        checkout_session = {
            'id': 'cs_test_webhook_1',
            'mode': 'payment',
            'payment_status': 'paid',
            'amount_total': round(booking.deposit_amount * 100),
            'currency': 'ngn',
            'metadata': {'booking_request_id': booking.booking_request_id},
        }
        payment = Payment(
            booking_id=booking.id,
            amount=booking.deposit_amount,
            status='Pending',
            payment_reference=checkout_session['id'],
            gateway='stripe',
            currency='NGN',
        )
        db.session.add(payment)
        booking.workflow_state = 'PAYMENT_PENDING'
        booking.payment_status = 'Pending'
        db.session.commit()
        booking_id = booking.id

        event = {'type': 'checkout.session.completed', 'data': {'object': checkout_session}}
        monkeypatch.setattr(payment_provider_service.stripe.Webhook, 'construct_event', lambda *_args: event)
        assert process_stripe_webhook(b'{}', 'stripe-signature') is True
        assert process_stripe_webhook(b'{}', 'stripe-signature') is True

        booking = db.session.get(Booking, booking_id)
        assert booking.payment_status == 'Paid'
        assert booking.workflow_state == 'CONFIRMED'
        assert Payment.query.filter_by(booking_id=booking_id, status='Successful').count() == 1


def test_stripe_webhook_rejects_missing_signature(app):
    app.config['STRIPE_WEBHOOK_SECRET'] = 'whsec_unit_test'
    with app.app_context(), pytest.raises(ValueError, match='signature header'):
        process_stripe_webhook(b'{}', '')


def test_stripe_webhook_route_rejects_invalid_signature(app):
    app.config['STRIPE_WEBHOOK_SECRET'] = 'whsec_unit_test'
    response = app.test_client().post(
        '/booking/payment/webhook',
        data=b'{}',
        headers={'Stripe-Signature': 'invalid'},
    )
    assert response.status_code == 400
    assert response.json['received'] is False


def test_quote_retries_pending_road_route_and_uses_server_distance(app, monkeypatch):
    monkeypatch.setattr(booking_engine_service, 'route_distance', lambda *coords: {
        'provider': 'osrm', 'distance_km': 37.4, 'duration_minutes': 48,
    })
    with app.app_context():
        booking = create_request()
        booking.distance_km = 0
        booking.route_status = 'PENDING'
        booking.route_provider = None
        booking.route_calculated_at = None
        calculation = complete_quote(booking)
        assert booking.distance_km == 37.4
        assert booking.route_duration_minutes == 48
        assert booking.route_provider == 'osrm'
        assert booking.route_status == 'CALCULATED'
        assert booking.route_calculated_at is not None
        assert calculation['route']['distance_km'] == 37.4


def test_customer_routes_complete_one_request_through_quote_acceptance(app):
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    property_url = f'/booking/{request_id}/property'
    property_page = client.get(property_url)
    assert property_page.status_code == 200
    chosen = client.post(property_url, data={
        'csrf_token': csrf_token(property_page),
        'action': 'choose_property_type', 'property_type': 'Apartment',
    })
    assert chosen.status_code == 302
    property_response = client.get(property_url)
    assert b'Bedrooms' in property_response.data
    assert b'Workstations' not in property_response.data

    property_saved = client.post(property_url, data={
        'csrf_token': csrf_token(property_response),
        'property_type': 'Apartment', 'bedrooms': '2', 'bathrooms': '2',
        'floors': '1', 'approximate_size_m2': '90',
    })
    assert property_saved.location.endswith(f'/booking/{request_id}/inventory')
    inventory_page = client.get(property_saved.location)
    inventory_response = client.post(f'/booking/{request_id}/inventory', data={
        'csrf_token': csrf_token(inventory_page),
        'item_name[]': ['Sofa', 'Wardrobe'], 'item_quantity[]': ['1', '1'],
        'item_category[]': ['Furniture', 'Bedroom'], 'item_size[]': ['large', 'large'],
    })
    assert inventory_response.location.endswith(f'/booking/{request_id}/services')
    services_page = client.get(inventory_response.location)
    services_response = client.post(f'/booking/{request_id}/services', data={
        'csrf_token': csrf_token(services_page), 'packing': 'partial',
    })
    assert services_response.location.endswith(f'/booking/{request_id}/schedule')
    schedule_page = client.get(services_response.location)
    schedule_html = schedule_page.get_data(as_text=True)
    assert 'Current move-time estimate' in schedule_html
    assert 'data-slot-warning' in schedule_html
    assert 'images/demo/' in schedule_html
    assert 'Estimated vehicle charge' not in schedule_html
    with app.app_context():
        selected_vehicle = VehicleType.query.filter_by(slug='large-truck').one()
        selected_vehicle_id = selected_vehicle.id
    schedule_response = client.post(f'/booking/{request_id}/schedule', data={
        'csrf_token': csrf_token(schedule_page),
        'move_date': (date.today() + timedelta(days=7)).isoformat(),
        'preferred_time': 'Morning',
        'vehicle_type_id': str(selected_vehicle_id),
    })
    assert schedule_response.location.endswith(f'/booking/{request_id}/quote')
    with app.app_context():
        scheduled = Booking.query.filter_by(booking_request_id=request_id).one()
        assert scheduled.workflow_state == 'SCHEDULE_COMPLETED'
        assert scheduled.distance_km == 20
        assert scheduled.workflow_data['services']['packing'] == 'partial'
        assert scheduled.workflow_data['vehicle']['selected_type_id'] == selected_vehicle_id
    quote_response = client.get(schedule_response.location)
    assert quote_response.status_code == 200
    with app.app_context():
        quoted = Booking.query.filter_by(booking_request_id=request_id).one()
        assert quoted.workflow_state == 'QUOTE_READY'
        assert quoted.estimated_total > 0
        assert quoted.calculation_data['vehicle']['name']
        assert quoted.calculation_data['vehicle']['id'] == selected_vehicle_id
        vehicle_line = BookingQuoteLine.query.filter_by(booking_id=quoted.id, code='vehicle').one()
        assert 'Large Truck' in vehicle_line.label

    quote_token = csrf_token(quote_response)
    accepted = client.post(f'/booking/{request_id}/quote', data={
        'csrf_token': quote_token, 'action': 'accept_quote',
    })
    assert accepted.location.endswith(f'/booking/{request_id}/payment')
    with app.app_context():
        persisted = Booking.query.filter_by(booking_request_id=request_id).one()
        assert persisted.workflow_state == 'QUOTE_ACCEPTED'
        assert persisted.quote_status == 'Accepted'
        assert persisted.booking_number == request_id
        assert len(persisted.items) == 2


def test_property_csrf_retry_preserves_booking_and_advances_from_database(app):
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        request_id = booking.booking_request_id

    property_url = f'/booking/{request_id}/property'
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    type_page = client.get(property_url)
    type_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', type_page.get_data(as_text=True))
    assert type_page.status_code == 200
    assert type_token
    chose_property = client.post(property_url, data={
        'csrf_token': type_token.group(1), 'action': 'choose_property_type', 'property_type': 'Apartment',
    })
    assert chose_property.status_code == 302
    property_page = client.get(property_url)
    property_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', property_page.get_data(as_text=True))
    assert property_page.status_code == 200
    assert property_token

    rejected = client.post(property_url, data={
        'property_type': 'Apartment', 'bedrooms': '2', 'bathrooms': '2', 'floors': '1',
    })
    assert rejected.status_code == 302
    assert rejected.location.endswith(property_url)
    with client.session_transaction() as session:
        assert session['active_booking_request_id'] == request_id

    refreshed_page = client.get(property_url)
    refreshed_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', refreshed_page.get_data(as_text=True))
    assert refreshed_page.status_code == 200
    assert refreshed_token
    saved_property = client.post(property_url, data={
        'csrf_token': refreshed_token.group(1), 'property_type': 'Apartment',
        'bedrooms': '2', 'bathrooms': '2', 'floors': '1',
    })
    assert saved_property.status_code == 302
    assert saved_property.location.endswith(f'/booking/{request_id}/inventory')

    with app.app_context():
        persisted = Booking.query.filter_by(booking_request_id=request_id).one()
        assert persisted.workflow_state == 'PROPERTY_COMPLETED'
        assert persisted.workflow_data['property']['bedrooms'] == 2


def test_booking_reference_survives_relogin_and_is_scoped_to_database_owner(app):
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        owner = User(first_name='Owner', last_name='One', email='owner@example.com', phone='')
        other_user = User(first_name='Other', last_name='Two', email='other@example.com', phone='')
        owner.set_password('password123')
        other_user.set_password('password123')
        db.session.add_all([owner, other_user])
        db.session.flush()
        booking = create_request()
        booking.customer_id = owner.id
        db.session.commit()
        request_id, other_id = booking.booking_request_id, other_user.id

    client = app.test_client()
    login = client.post('/auth/login', data={'email': 'owner@example.com', 'password': 'password123'})
    assert login.status_code == 302
    assert client.get(f'/booking/{request_id}/property').status_code == 200
    assert client.get('/auth/logout').status_code == 302
    assert client.get(f'/booking/{request_id}/property').status_code == 404
    relogin = client.post('/auth/login', data={'email': 'owner@example.com', 'password': 'password123'})
    assert relogin.status_code == 302
    assert client.get(f'/booking/{request_id}/property').status_code == 200

    other_client = app.test_client()
    other_login = other_client.post('/auth/login', data={'email': 'other@example.com', 'password': 'password123'})
    assert other_login.status_code == 302
    assert other_client.get(f'/booking/{request_id}/property').status_code == 404
    assert other_client.get(f'/api/bookings/{request_id}').status_code == 404


def test_json_api_updates_same_request_and_rejects_stage_skips(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    with app.app_context():
        booking = create_request()
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    skipped = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Sofa', 'quantity': 1}],
    })
    assert skipped.status_code == 409
    assert 'Complete property' in skipped.get_json()['error']

    updated = client.put(f'/api/bookings/{request_id}/property', json={
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1, 'floors': 1,
    })
    assert updated.status_code == 200
    item_response = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture', 'size': 'large'}],
    })
    assert item_response.status_code == 200
    snapshot = client.get(f'/api/bookings/{request_id}').get_json()
    assert snapshot['booking_request_id'] == request_id
    assert snapshot['state'] == 'INVENTORY_COMPLETED'
    assert snapshot['inventory'][0]['name'] == 'Sofa'