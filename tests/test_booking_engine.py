from datetime import date, datetime, timedelta
from io import BytesIO
import re
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import (
    Booking, BookingEvent, BookingQuoteLine, InventoryItem, Payment, Referral,
    ReferralAttribution, ReferralCampaign, ReferralCode, ReferralReward,
    RewardTransaction, ServicePricing, User, VehicleType,
)
from moving_company.services.booking_engine_service import (
    BULK_ITEM_OPTIONS, INVENTORY_CATALOG, accept_quote, apply_price_override, calculate_quote, create_booking_request,
    catalogue_items_for_service, confirm_inventory, inventory_requirements, preview_move_duration,
    replace_inventory, save_vehicle_selection, save_workflow_section, vehicle_options,
)
from moving_company.services import booking_engine_service
from moving_company.services import payment_provider_service
from moving_company.services.payment_provider_service import initialize_payment, process_stripe_webhook
from moving_company.services.referral_service import (
    ensure_referral_code, expire_referral_rewards, get_campaign,
    issue_referral_reward, reverse_referral_reward,
)


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


def login_customer(app, client):
    with app.app_context():
        customer = User(first_name='Booking', last_name='Customer', email='booking-engine-test@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True


def csrf_token(response):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def complete_quote(booking):
    save_workflow_section(booking, 'property', {
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 2,
        'service_answers': {
            'destination_property_type': 'Apartment',
            'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
        },
    })
    replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture', 'size': 'large'}])
    booking.workflow_data = {**booking.workflow_data, 'inventory_answers': {'heavy_or_oversized': 'no'}}
    save_workflow_section(booking, 'services', {'packing': 'partial'})
    booking.workflow_data = {
        **booking.workflow_data,
        'addon_answers': {'packing': {
            'property': {
                'packing_areas': 'Living room items',
                'packing_materials_needed': 'no',
                'packing_materials_available': 'yes',
            },
            'inventory': {},
        }},
    }
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


def test_manual_location_search_text_is_saved_as_address(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr('moving_company.routes.booking_engine.geocode_address', lambda address: None)
    client = app.test_client()
    login_customer(app, client)

    response = client.post('/booking/location/residential', data={
        'pickup_search': '12 Example Road, Abuja',
        'pickup_city': 'Abuja', 'pickup_state': 'FCT',
        'destination_search': '8 Sample Avenue, Abuja',
        'destination_city': 'Abuja', 'destination_state': 'FCT',
    })

    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.order_by(Booking.id.desc()).first()
        assert booking.pickup_address == '12 Example Road, Abuja'
        assert booking.destination_address == '8 Sample Avenue, Abuja'


def test_moving_location_requires_city_and_state_without_inventing_defaults(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr('moving_company.routes.booking_engine.geocode_address', lambda _address: None)
    client = app.test_client()
    login_customer(app, client)

    response = client.post('/booking/location/residential', data={
        'pickup_search': '12 Example Road',
        'destination_search': '8 Sample Avenue',
    })
    assert response.status_code == 200
    assert b'Confirm the city and state for each location' in response.data
    with app.app_context():
        assert Booking.query.count() == 0


def test_location_without_provider_admin_fields_does_not_invent_abujafct_or_nigeria(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr('moving_company.routes.booking_engine.geocode_address', lambda _address: None)
    client = app.test_client()
    login_customer(app, client)

    response = client.post('/booking/location/cleaning', data={
        'pickup_address': '1 Unknown Region Road',
    })
    assert response.status_code == 302

    with app.app_context():
        booking = Booking.query.order_by(Booking.id.desc()).first()
        assert booking.pickup_city is None
        assert booking.pickup_state is None
        assert booking.pickup_country is None


def test_new_booking_quote_rejects_missing_service_answers(app):
    with app.app_context():
        booking = create_request()
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Living Room'}])
        booking.move_date = date.today() + timedelta(days=7)
        booking.preferred_time = 'Morning'
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        db.session.commit()
        with pytest.raises(ValueError, match='Destination property type is required'):
            calculate_quote(booking)


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


def test_quote_uses_validated_service_answers_for_access_and_audit_snapshot(app):
    with app.app_context():
        booking = create_request()
        answers = {
            'destination_property_type': 'Apartment',
            'restricted_vehicle_access': 'yes',
            'long_carry_distance': 'yes',
        }
        save_workflow_section(booking, 'property', {
            'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1,
            'floors': 1, 'service_answers': answers,
        })
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture'}])
        booking.workflow_data = {**booking.workflow_data, 'inventory_answers': {'heavy_or_oversized': 'no'}}
        save_workflow_section(booking, 'services', {})
        save_workflow_section(booking, 'schedule', {
            'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        })
        calculation = calculate_quote(booking)
        access_line = BookingQuoteLine.query.filter_by(booking_id=booking.id, code='access').one()
        assert access_line.quantity == 2
        expected_answers = {**answers, 'destination_property_type': 'Flat / Apartment'}
        assert calculation['service_inputs']['property'] == expected_answers


def test_standalone_packing_scope_uses_existing_packing_rates(app):
    with app.app_context():
        booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'pickup_latitude': 9.05, 'pickup_longitude': 7.49,
                'pickup_formatted_address': '12 Yakubu Gowon Way, Abuja',
                'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'destination_latitude': 9.08, 'destination_longitude': 7.45,
                'destination_formatted_address': 'Plot 405 Constitution Avenue, Abuja',
                'distance_km': 20, 'pickup_floor': 'Ground', 'destination_floor': 'Ground',
            },
            service_type='packing',
        )
        save_workflow_section(booking, 'property', {
            'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1,
            'service_answers': {
                'packing_for': 'home', 'packing_scope': 'selected-rooms', 'packing_rooms': 2,
                'packing_room_list': 'Kitchen and living room', 'packing_already_packed': 'some',
                'packing_materials_needed': 'no', 'packing_materials_available': 'yes',
                'packing_service_level': 'full-service', 'unpacking_required': 'yes',
                'packing_dismantling': 'no', 'packing_reassembly': 'no',
            },
        })
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture'}])
        booking.workflow_data = {**booking.workflow_data, 'inventory_answers': {'packing_fragile_items': 'no'}}
        ServicePricing.query.filter_by(key='packing_partial').one().is_configured = True
        save_workflow_section(booking, 'services', {})
        save_workflow_section(booking, 'schedule', {
            'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        })
        calculation = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert calculation['indicative_total'] > 0
        assert any('packing-team roster' in flag for flag in calculation['manual_review_flags'])


def test_unpriced_addon_scope_is_flagged_for_operations_review(app):
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {
            'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1,
            'service_answers': {
                'destination_property_type': 'Apartment',
                'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
            },
        })
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture'}])
        save_workflow_section(booking, 'services', {'home_decor': 'yes', 'cleaning': 'move-out'})
        booking.workflow_data = {
            **booking.workflow_data,
            'inventory_answers': {'heavy_or_oversized': 'no'},
            'addon_answers': {
                'home_decor': {
                    'property': {'decor_at': 'destination', 'decor_setup_type': 'new-home-setup', 'decor_rooms': ['living-room'], 'decor_room_count': 2},
                    'inventory': {'decor_furniture_available': 'yes', 'decor_tasks': 'Furniture placement', 'decor_assembly_needed': 'no', 'decor_transport_needed': 'no'},
                },
                'cleaning': {
                    'property': {'cleaning_at': 'pickup', 'cleaning_property_type': 'Apartment', 'cleaning_bedrooms': 1, 'cleaning_bathrooms': 1, 'cleaning_occupancy': 'no'},
                    'inventory': {'cleaning_condition': 'normal', 'cleaning_areas': 'Kitchen'},
                },
            },
        }
        save_workflow_section(booking, 'schedule', {
            'date': (date.today() + timedelta(days=7)).isoformat(), 'time': 'Morning',
        })

        calculation = calculate_quote(booking)
        flags = calculation['manual_review_flags']
        assert any('Home Décor scope' in flag for flag in flags)
        assert any('Cleaning scope' in flag for flag in flags)
        assert calculation['manual_review_resolved'] is False
        with pytest.raises(ValueError, match='Operations must review'):
            accept_quote(booking, user_id=None)

        apply_price_override(booking, booking.estimated_total, 'Confirmed service scope and rate', user_id=None)
        assert booking.calculation_data['manual_review_resolved'] is True
        accept_quote(booking, user_id=None)


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
        assert booking.workflow_state == 'PROPERTY_COMPLETED'
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


def test_catalogue_and_custom_duplicate_inventory_lines_are_combined(app):
    with app.app_context():
        booking = create_request()
        catalogue_items_for_service('residential')
        wardrobe = InventoryItem.query.filter_by(name='Wardrobe', active=True).one()
        replace_inventory(booking, [
            {'inventory_item_id': wardrobe.id, 'name': wardrobe.name, 'quantity': 2},
            {'custom_name': 'wardrobe', 'name': 'wardrobe', 'quantity': 3, 'category': 'Custom item'},
        ])

        assert len(booking.inventory) == 1
        assert booking.inventory[0].inventory_item_id == wardrobe.id
        assert booking.inventory[0].quantity == 5


def test_vehicle_override_changes_server_quote_and_service_changes_duration(app):
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {
            'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1,
            'service_answers': {
                'destination_property_type': 'Apartment',
                'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
            },
        })
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'size': 'large'}])
        booking.workflow_data = {**booking.workflow_data, 'inventory_answers': {'heavy_or_oversized': 'no'}}
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
        booking.workflow_data = {
            **booking.workflow_data,
            'addon_answers': {'packing': {
                'property': {
                    'packing_areas': 'Entire home',
                    'packing_materials_needed': 'no',
                    'packing_materials_available': 'yes',
                },
                'inventory': {},
            }},
        }
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


def test_vehicle_options_and_selection_fail_closed_on_missing_capacity(app):
    with app.app_context():
        booking = create_request()
        replace_inventory(booking, [{
            'name': 'Large custom load', 'quantity': 1,
            'estimated_volume_m3': 4, 'estimated_weight_kg': 800,
        }])
        invalid_vehicle = VehicleType.query.filter_by(is_active=True).first()
        original_capacity = invalid_vehicle.capacity_m3
        original_payload = invalid_vehicle.max_payload_kg
        invalid_vehicle.capacity_m3 = 0
        invalid_vehicle.max_payload_kg = 0
        db.session.commit()

        options = vehicle_options(booking)
        assert invalid_vehicle.id not in {option['id'] for option in options}
        with pytest.raises(ValueError, match='missing verified capacity data'):
            save_vehicle_selection(booking, invalid_vehicle.id)

        invalid_vehicle.capacity_m3 = original_capacity
        invalid_vehicle.max_payload_kg = original_payload


def test_vehicle_selection_calculates_required_number_of_configured_units(app):
    with app.app_context():
        booking = create_request()
        replace_inventory(booking, [{
            'name': 'Multi-vehicle load', 'quantity': 1,
            'estimated_volume_m3': 50, 'estimated_weight_kg': 9000,
        }])
        vehicle = max(VehicleType.query.filter_by(is_active=True).all(), key=lambda row: row.capacity_m3)

        save_vehicle_selection(booking, vehicle.id)
        option = next(row for row in vehicle_options(booking) if row['id'] == vehicle.id)
        requirements = inventory_requirements(booking)
        assert option['quantity'] > 1
        assert option['quantity'] * option['capacity_m3'] >= requirements['volume_m3']
        assert option['quantity'] * option['max_payload_kg'] >= requirements['weight_kg']


def test_vehicle_selection_rejects_inactive_catalogue_entry(app):
    with app.app_context():
        booking = create_request()
        vehicle = VehicleType.query.filter_by(is_active=True).first()
        vehicle_id = vehicle.id
        vehicle.is_active = False
        db.session.commit()

        with pytest.raises(ValueError, match='no longer available'):
            save_vehicle_selection(booking, vehicle_id)


def test_services_post_rejects_manipulated_vehicle_with_missing_capacity(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        replace_inventory(booking, [{
            'name': 'Sofa', 'quantity': 1, 'estimated_volume_m3': 1.5, 'estimated_weight_kg': 50,
        }])
        workflow_data = dict(booking.workflow_data or {})
        workflow_data.update({
            'inventory_confirmed': True,
            'inventory_answers': {'heavy_or_oversized': 'no'},
            'property': {'service_answers': {
                'destination_property_type': 'Flat / Apartment',
                'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
            }},
        })
        booking.workflow_data = workflow_data
        booking.workflow_state = 'INVENTORY_COMPLETED'
        vehicle = VehicleType.query.filter_by(is_active=True).first()
        vehicle.capacity_m3 = 0
        vehicle_id = vehicle.id
        booking_id = booking.id
        request_id = booking.booking_request_id
        db.session.commit()
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    response = client.post(f'/booking/{request_id}/services', data={'vehicle_type_id': str(vehicle_id)})
    assert response.status_code == 200
    assert b'missing verified capacity data' in response.data
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.workflow_state == 'SERVICES_COMPLETED'
        assert 'vehicle' not in booking.workflow_data


def test_inventory_and_service_pages_have_previous_and_no_manual_service_hours(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {'property_type': 'Apartment', 'bedrooms': 1, 'bathrooms': 1, 'floors': 1})
        replace_inventory(booking, [{'name': 'Wardrobe', 'quantity': 1}])
        booking.workflow_data = {**booking.workflow_data, 'inventory_answers': {'heavy_or_oversized': 'no'}}
        booking.workflow_data = {**booking.workflow_data, 'inventory_confirmed': True}
        booking.workflow_state = 'INVENTORY_COMPLETED'
        db.session.commit()
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id
    inventory = client.get(f'/booking/{request_id}/inventory')
    assert inventory.status_code == 200
    inventory_html = inventory.get_data(as_text=True)
    assert 'Living Room' in inventory_html and 'Fragile / Valuable' in inventory_html
    assert 'static/css/booking.css' in inventory_html
    assert 'static/js/booking.js' in inventory_html
    assert 'inventory-category-group' in inventory_html
    assert '<summary' in inventory_html
    assert 'preservedSubmitter.name = submitter.name' in inventory_html
    assert 'preservedSubmitter.value = submitter.value' in inventory_html
    assert 'Vehicle recommendation' not in inventory_html
    assert 'Previous: Property' in inventory_html
    services = client.get(f'/booking/{request_id}/services')
    assert services.status_code == 200
    services_html = services.get_data(as_text=True)
    assert 'Packing &amp; Protection' in services_html
    assert 'Additional service hours' not in services_html
    assert 'Previous: Inventory' in services_html
    assert 'aria-label="Remove Wardrobe"' in inventory_html
    assert 'title="Delete item"' in inventory_html


def test_property_questions_use_accessible_native_choice_groups(app):
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        request_id = booking.booking_request_id

    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    response = client.get(f'/booking/{request_id}/property')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'booking-question-grid' in html
    assert 'role="radiogroup"' in html
    assert 'aria-labelledby="service_restricted_vehicle_access-label"' in html
    assert 'data-condition-key="property_type"' in html
    assert 'Floors or storeys at pickup' in html
    assert 'Enter 1 for a single-level property.' in html


def test_customer_can_add_catalogue_items_with_quantity_only(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
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
            'action': 'confirm_inventory',
            'selection_id[]': str(selection_id),
            'quantity[]': '2',
            'inventory_heavy_or_oversized': 'no',
        },
    )
    assert continued.status_code == 302
    assert continued.location.endswith(f'/booking/{request_id}/services')


def test_residential_inventory_is_saved_reviewed_then_vehicle_is_recommended(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        save_workflow_section(booking, 'property', {
            'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1, 'floors': 2,
            'service_answers': {
                'destination_property_type': 'Flat / Apartment',
                'destination_bedrooms': 3, 'destination_floors': 2,
                'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
            },
        })
        request_id = booking.booking_request_id
        catalogue_items_for_service('residential', property_type='Apartment')
        television = InventoryItem.query.filter_by(name='Small TV', active=True).first()
        refrigerator = InventoryItem.query.filter_by(name='Refrigerator', active=True).first()
        assert television and refrigerator
        television_id, refrigerator_id = television.id, refrigerator.id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    property_page = client.get(f'/booking/{request_id}/property')
    property_html = property_page.get_data(as_text=True)
    assert property_page.status_code == 200
    assert 'Bedrooms at destination' in property_html
    assert 'Destination property floors or storeys' in property_html
    assert 'name="service_destination_property_type"' in property_html
    assert '<select name="service_destination_property_type"' in property_html
    assert 'type="checkbox" name="service_destination_property_type"' not in property_html

    inventory_page = client.get(f'/booking/{request_id}/inventory')
    inventory_html = inventory_page.get_data(as_text=True)
    assert inventory_page.status_code == 200
    assert 'Vehicle recommendation' not in inventory_html
    assert 'television tv television set' in inventory_html
    assert 'data-category="Electronics"' in inventory_html
    assert 'Save selected items' in inventory_html

    saved_draft = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'add_catalogue',
        f'inventory_item_id_{television_id}': str(television_id),
        f'inventory_item_quantity_{television_id}': '2',
    })
    assert saved_draft.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert len(booking.inventory) == 1
        television_selection = booking.inventory[0]
        assert television_selection.quantity == 2
        assert booking.workflow_state == 'PROPERTY_COMPLETED'
        assert booking.workflow_data['inventory_confirmed'] is False
        television_selection_id = television_selection.id
    services_before_confirmation = client.get(f'/booking/{request_id}/services')
    assert services_before_confirmation.status_code == 302
    assert services_before_confirmation.location.endswith(f'/booking/{request_id}/inventory')

    second_draft = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'add_catalogue',
        f'inventory_item_id_{refrigerator_id}': str(refrigerator_id),
        f'inventory_item_quantity_{refrigerator_id}': '1',
    })
    assert second_draft.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert len(booking.inventory) == 2
        selection_ids = [str(item.id) for item in booking.inventory]
        quantities = [str(item.quantity) for item in booking.inventory]

    confirmed = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'confirm_inventory',
        'selection_id': selection_ids,
        'quantity[]': quantities,
        'inventory_heavy_or_oversized': 'no',
    })
    assert confirmed.status_code == 302
    assert confirmed.location.endswith(f'/booking/{request_id}/services')
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_state == 'INVENTORY_COMPLETED'
        assert booking.workflow_data['inventory_answers']['heavy_or_oversized'] == 'no'
        recommended_vehicle = next(option for option in vehicle_options(booking) if option['recommended'])
        assert recommended_vehicle['quantity'] >= 1

    services_page = client.get(confirmed.location)
    assert services_page.status_code == 200
    assert 'Vehicle recommendation from confirmed inventory' in services_page.get_data(as_text=True)
    assert 'type="radio" name="vehicle_type_id"' in services_page.get_data(as_text=True)
    saved_services = client.post(confirmed.location, data={
        'vehicle_type_id': str(recommended_vehicle['id']),
    })
    assert saved_services.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['vehicle']['selected_type_id'] == recommended_vehicle['id']

    inventory_again = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'add_catalogue',
        f'inventory_item_id_{television_id}': str(television_id),
        f'inventory_item_quantity_{television_id}': '4',
    })
    assert inventory_again.status_code == 302
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_state == 'PROPERTY_COMPLETED'
        assert booking.workflow_data['inventory_confirmed'] is False
        assert 'vehicle' not in booking.workflow_data
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id).count() == 0


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
    login_customer(app, client)
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
        'action': 'confirm_inventory', 'selection_id': str(selection_id), 'quantity[]': '50',
        'inventory_business_inventory_notes': 'Construction materials',
        'inventory_it_equipment_handling': 'no',
        'inventory_confidential_files': 'no',
        'inventory_specialist_equipment': 'no',
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
    login_customer(app, client)
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
        'inventory_heavy_or_oversized': 'yes',
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
    assert other_client.get(f'/booking/{request_id}/inventory/photos/{photo_path}').status_code == 302


def test_invalid_stage_transition_does_not_skip_required_information(app):
    with app.app_context():
        booking = create_request()
        from moving_company.services.booking_engine_service import transition_stage

        with pytest.raises(ValueError, match='Complete the property step'):
            transition_stage(booking, 'inventory')


def test_inventory_confirmation_is_idempotent_and_event_key_is_unique(app):
    with app.app_context():
        booking = create_request()
        replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'estimated_volume_m3': 0.5, 'estimated_weight_kg': 20}])
        workflow_data = dict(booking.workflow_data or {})
        workflow_data.update({'inventory_confirmed': False, 'inventory_answers': {'heavy_or_oversized': 'no'}})
        booking.workflow_data = workflow_data
        db.session.commit()

        assert confirm_inventory(booking) is True
        assert confirm_inventory(booking) is False
        event = BookingEvent.query.filter_by(booking_id=booking.id, action='inventory.confirmed').one()
        assert event.idempotency_key == f'inventory-confirmed-{booking.id}-1'
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='inventory.confirmed').count() == 1
        assert confirm_inventory(booking) is False

        duplicate = BookingEvent(
            booking_id=booking.id, action='inventory.confirmed',
            idempotency_key=event.idempotency_key,
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='inventory.confirmed').count() == 1


def test_api_identical_inventory_confirmation_retry_keeps_one_event(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
        booking_id = booking.id
        request_id = booking.booking_request_id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    payload = {
        'items': [{'name': 'Sofa', 'quantity': 1, 'estimated_volume_m3': 0.5, 'estimated_weight_kg': 20}],
        'inventory_answers': {'heavy_or_oversized': 'no'},
        'confirmed': True,
    }
    first = client.post(f'/api/bookings/{request_id}/inventory', json=payload)
    second = client.post(f'/api/bookings/{request_id}/inventory', json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.workflow_state == 'INVENTORY_COMPLETED'
        assert booking.workflow_data['inventory_confirmed'] is True
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='inventory.confirmed').count() == 1


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


def test_paid_booking_rejects_manual_quote_override_without_state_changes(app):
    with app.app_context():
        booking = create_request()
        booking.status = 'Confirmed'
        booking.payment_status = 'Paid'
        booking.quote_status = 'Accepted'
        booking.workflow_state = 'CONFIRMED'
        booking.estimated_total = 100000
        booking.deposit_amount = 30000
        db.session.commit()

        with pytest.raises(ValueError, match='paid or operational'):
            apply_price_override(booking, 50000, 'Attempted paid-booking repricing', user_id=None)

        assert booking.status == 'Confirmed'
        assert booking.payment_status == 'Paid'
        assert booking.quote_status == 'Accepted'
        assert booking.workflow_state == 'CONFIRMED'
        assert booking.estimated_total == 100000
        assert booking.deposit_amount == 30000


def test_api_payment_allows_an_accepted_unpaid_quote_but_not_a_paid_booking(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        booking = create_request()
        complete_quote(booking)
        accept_quote(booking, None)
        request_id = booking.booking_request_id
        booking_id = booking.id
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    initialized = []
    monkeypatch.setattr(
        'moving_company.routes.booking_engine.initialize_payment',
        lambda booking, _email: initialized.append(booking.id) or type('PaymentResult', (), {
            'payment_reference': 'test-reference', 'authorization_url': 'https://example.test/checkout',
            'amount': 30000, 'currency': 'NGN',
        })(),
    )
    response = client.post(f'/api/bookings/{request_id}/payment')
    assert response.status_code == 200
    assert initialized == [booking_id]

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        booking.payment_status = 'Paid'
        booking.workflow_state = 'CONFIRMED'
        booking.status = 'Confirmed'
        db.session.commit()
    blocked = client.post(f'/api/bookings/{request_id}/payment')
    assert blocked.status_code == 409


def test_payment_page_uses_the_booking_service_stages(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    with app.app_context():
        customer = User.query.filter_by(email='booking-engine-test@example.com').one()
        booking = create_booking_request(
            customer_id=customer.id, service_type='cleaning',
            location={'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT'},
        )
        booking.workflow_state = 'QUOTE_ACCEPTED'
        booking.quote_status = 'Accepted'
        db.session.commit()
        request_id = booking.booking_request_id
    response = client.get(f'/booking/{request_id}/payment')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Step 6 of 6' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuetext="Step 6 of 6: Payment"' in html


@pytest.mark.parametrize(
    ('path_suffix', 'method', 'json_payload'),
    [
        ('location', 'post', None),
        ('property', 'post', None),
        ('inventory', 'post', None),
        ('services', 'post', None),
        ('schedule', 'post', None),
        ('quote', 'post', None),
        ('api/bookings/{request_id}/property', 'put', {}),
        ('api/bookings/{request_id}/inventory', 'post', {'items': []}),
        ('api/bookings/{request_id}/services', 'put', {}),
        ('api/bookings/{request_id}/schedule', 'put', {}),
        ('api/bookings/{request_id}/calculate', 'post', {}),
        ('api/bookings/{request_id}/quote/accept', 'post', {'policy_acknowledged': True}),
        ('api/bookings/{request_id}/payment', 'post', {}),
        ('api/bookings/{request_id}/ai/suggest', 'post', {'external_processing_acknowledged': True, 'description': 'Move details'}),
    ],
)
def test_paid_confirmed_booking_rejects_customer_workflow_edits(app, path_suffix, method, json_payload):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    with app.app_context():
        owner = User(first_name='Paid', last_name='Booking', email=f'paid-{path_suffix.replace("/", "-")}@example.test', phone='')
        owner.set_password('password123')
        db.session.add(owner)
        db.session.flush()
        booking = create_request()
        booking.customer_id = owner.id
        booking.status = 'Confirmed'
        booking.payment_status = 'Paid'
        booking.quote_status = 'Accepted'
        booking.workflow_state = 'CONFIRMED'
        booking.estimated_total = 100000
        booking.deposit_amount = 30000
        booking_id = booking.id
        request_id = booking.booking_request_id
        owner_id = owner.id
        db.session.commit()

    with client.session_transaction() as session:
        session['_user_id'] = str(owner_id)
        session['_fresh'] = True
        session['active_booking_request_id'] = request_id

    path = path_suffix.format(request_id=request_id)
    url = f'/booking/{request_id}/{path}' if not path.startswith('api/') else f'/{path}'
    request_method = getattr(client, method)
    response = request_method(url, json=json_payload) if json_payload is not None else request_method(url, data={})
    assert response.status_code == 409

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.status == 'Confirmed'
        assert booking.payment_status == 'Paid'
        assert booking.quote_status == 'Accepted'
        assert booking.workflow_state == 'CONFIRMED'
        assert booking.estimated_total == 100000


def test_paid_confirmed_location_edit_does_not_invalidate_quote_or_reservation_state(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr('moving_company.routes.booking_engine.geocode_address', lambda _address: None)
    client = app.test_client()
    with app.app_context():
        owner = User(first_name='Paid', last_name='Location', email='paid-location@example.test', phone='')
        owner.set_password('password123')
        db.session.add(owner)
        db.session.flush()
        booking = create_request()
        booking.customer_id = owner.id
        booking.status = 'Confirmed'
        booking.payment_status = 'Paid'
        booking.quote_status = 'Accepted'
        booking.workflow_state = 'CONFIRMED'
        booking.estimated_total = 100000
        booking.deposit_amount = 30000
        booking_id = booking.id
        request_id = booking.booking_request_id
        owner_id = owner.id
        db.session.commit()

    with client.session_transaction() as session:
        session['_user_id'] = str(owner_id)
        session['_fresh'] = True
        session['active_booking_request_id'] = request_id

    response = client.post(f'/booking/{request_id}/location', data={
        'pickup_address': '1 Changed Road', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
        'destination_address': '2 Changed Road', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        'pickup_floor': 'Ground', 'destination_floor': 'Ground',
    })
    assert response.status_code == 409

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.pickup_address == '12 Yakubu Gowon Way'
        assert booking.status == 'Confirmed'
        assert booking.payment_status == 'Paid'
        assert booking.quote_status == 'Accepted'
        assert booking.workflow_state == 'CONFIRMED'
        assert booking.estimated_total == 100000


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
    assert 'Deposit due now: ₦' in html
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
        assert calls[0][0]['automatic_payment_methods'] == {'enabled': True}
        assert 'payment_method_types' not in calls[0][0]
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
    login_customer(app, client)
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
        'service_destination_property_type': 'Apartment',
        'service_restricted_vehicle_access': 'no',
        'service_long_carry_distance': 'no',
    })
    assert property_saved.location.endswith(f'/booking/{request_id}/inventory')
    inventory_page = client.get(property_saved.location)
    inventory_response = client.post(f'/booking/{request_id}/inventory', data={
        'csrf_token': csrf_token(inventory_page),
        'item_name[]': ['Sofa', 'Wardrobe'], 'item_quantity[]': ['1', '1'],
        'item_category[]': ['Furniture', 'Bedroom'], 'item_size[]': ['large', 'large'],
    })
    assert inventory_response.location.endswith(f'/booking/{request_id}/inventory')
    review_page = client.get(inventory_response.location)
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        selection_ids = [str(item.id) for item in booking.inventory]
    inventory_response = client.post(f'/booking/{request_id}/inventory', data={
        'csrf_token': csrf_token(review_page), 'action': 'confirm_inventory',
        'selection_id': selection_ids, 'quantity[]': ['1', '1'],
        'inventory_heavy_or_oversized': 'no',
    })
    assert inventory_response.location.endswith(f'/booking/{request_id}/services')
    assert inventory_response.location.endswith(f'/booking/{request_id}/services')
    services_page = client.get(inventory_response.location)
    services_response = client.post(f'/booking/{request_id}/services', data={
        'csrf_token': csrf_token(services_page), 'packing': 'partial',
        'addon_packing_property_packing_areas': 'Living room and bedroom items',
        'addon_packing_property_packing_materials_needed': 'no',
        'addon_packing_property_packing_materials_available': 'yes',
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
        'csrf_token': quote_token, 'action': 'accept_quote', 'policy_acknowledged': 'on',
    })
    assert accepted.location.endswith(f'/booking/{request_id}/payment')
    with app.app_context():
        accepted_booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert accepted_booking.calculation_data['policy_acceptance']['policy_version'] == 'chigo-terms-2026-10-16-v2'
    with app.app_context():
        persisted = Booking.query.filter_by(booking_request_id=request_id).one()
        assert persisted.workflow_state == 'QUOTE_ACCEPTED'
        assert persisted.quote_status == 'Accepted'
        assert persisted.booking_number == request_id
        assert len(persisted.items) == 2


def test_property_csrf_retry_preserves_booking_and_advances_from_database(app):
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()
    login_customer(app, client)
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
        'service_destination_property_type': 'Apartment',
        'service_restricted_vehicle_access': 'no',
        'service_long_carry_distance': 'no',
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
    assert client.get(f'/booking/{request_id}/property').status_code == 302
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
    login_customer(app, client)
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
        'service_answers': {
            'destination_property_type': 'Apartment',
            'restricted_vehicle_access': 'no',
            'long_carry_distance': 'no',
        },
    })
    assert updated.status_code == 200
    item_response = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Sofa', 'quantity': 1, 'category': 'Furniture', 'size': 'large'}],
        'inventory_answers': {'heavy_or_oversized': 'no'},
        'confirmed': True,
    })
    assert item_response.status_code == 200
    snapshot = client.get(f'/api/bookings/{request_id}').get_json()
    assert snapshot['booking_request_id'] == request_id
    assert snapshot['state'] == 'INVENTORY_COMPLETED'
    assert snapshot['inventory'][0]['name'] == 'Sofa'


def test_referral_link_attribution_survives_new_customer_registration(app):
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        campaign = get_campaign(create=True)
        campaign.active = True
        referrer = User(first_name='Referral', last_name='Owner', email='referrer@example.com', phone='', role='customer')
        referrer.set_password('password123')
        db.session.add(referrer)
        db.session.flush()
        code = ensure_referral_code(referrer)
        code_text = code.code
        db.session.commit()

    client = app.test_client()
    assert client.get(f'/?ref={code_text.lower()}').status_code == 200
    with client.session_transaction() as browser_session:
        assert browser_session.get('referral_attribution_token')
    registered = client.post('/auth/register', data={
        'full_name': 'New Customer', 'email': 'new-customer@example.com',
        'password': 'password123',
    })
    assert registered.status_code == 302
    with app.app_context():
        customer = User.query.filter_by(email='new-customer@example.com').one()
        referral = Referral.query.filter_by(referred_user_id=customer.id).one()
        assert referral.attribution_source == 'link'
        assert referral.status == 'registered'
        assert referral.referrer_user_id != customer.id
        assert ReferralAttribution.query.count() == 1


def test_new_customer_can_apply_a_manual_referral_code_once(app):
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        campaign = get_campaign(create=True)
        campaign.active = True
        referrer = User(first_name='Referral', last_name='Owner', email='manual-referrer@example.com', phone='')
        customer = User(first_name='Manual', last_name='Customer', email='manual-customer@example.com', phone='')
        referrer.set_password('password123')
        customer.set_password('password123')
        db.session.add_all([referrer, customer])
        db.session.flush()
        code = ensure_referral_code(referrer)
        code_text, customer_id = code.code, customer.id
        db.session.commit()

    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(customer_id)
        browser_session['_fresh'] = True
    response = client.post('/customer/referrals', data={'referral_code': code_text.lower()})
    assert response.status_code == 302
    with app.app_context():
        referral = Referral.query.filter_by(referred_user_id=customer_id).one()
        assert referral.attribution_source == 'manual'
        assert referral.status == 'registered'


def test_referral_discount_uses_server_quote_and_is_ledgered_once(app):
    with app.app_context():
        campaign = get_campaign(create=True)
        campaign.active = True
        campaign.minimum_booking_value_kobo = 1
        campaign.minimum_payable_kobo = 100000
        referrer = User(first_name='Referral', last_name='Owner', email='discount-referrer@example.com', phone='')
        referred = User(first_name='Referred', last_name='Customer', email='discount-customer@example.com', phone='')
        referrer.set_password('password123')
        referred.set_password('password123')
        db.session.add_all([referrer, referred])
        db.session.flush()
        code = ensure_referral_code(referrer)
        referral = Referral(
            referral_code_id=code.id, campaign_id=campaign.id,
            referrer_user_id=referrer.id, referred_user_id=referred.id,
            attributed_at=datetime.utcnow(), status='registered',
            terms_snapshot={
                'reward_amount_kobo': 200000, 'discount_amount_kobo': 200000,
                'minimum_booking_value_kobo': 1, 'minimum_payable_kobo': 100000,
                'eligible_services': [], 'reward_expiration_days': 365,
            },
        )
        db.session.add(referral)
        db.session.commit()
        booking = create_request()
        booking.customer_id = referred.id
        complete_quote(booking)
        calculation = calculate_quote(booking, user_id=referred.id)
        assert booking.referral_id == referral.id
        assert booking.referral_discount_amount == 2000
        assert calculation['original_total'] - calculation['total'] == 2000
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id, code='referral_discount').one().amount == -2000
        assert RewardTransaction.query.filter_by(referral_id=referral.id, transaction_type='discount_applied').count() == 1

        calculate_quote(booking, user_id=referred.id)
        assert RewardTransaction.query.filter_by(referral_id=referral.id, transaction_type='discount_applied').count() == 1
        apply_price_override(booking, 18000, 'Approved quote adjustment', referrer.id)
        assert booking.referral_discount_amount == 2000
        assert booking.estimated_total == 16000
        assert booking.deposit_amount == booking.calculation_data['deposit']
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id, code='referral_discount').one().amount == -2000
        assert BookingQuoteLine.query.filter_by(booking_id=booking.id, code='manual_adjustment').one().amount == round(18000 - (calculation['original_total'] - 0), 2)


def test_referral_reward_requires_paid_completion_and_is_idempotent(app):
    with app.app_context():
        campaign = get_campaign(create=True)
        referrer = User(first_name='Referral', last_name='Owner', email='reward-referrer@example.com', phone='')
        referred = User(first_name='Referred', last_name='Customer', email='reward-customer@example.com', phone='')
        referrer.set_password('password123')
        referred.set_password('password123')
        db.session.add_all([referrer, referred])
        db.session.flush()
        code = ensure_referral_code(referrer)
        referral = Referral(
            referral_code_id=code.id, campaign_id=campaign.id,
            referrer_user_id=referrer.id, referred_user_id=referred.id,
            attributed_at=datetime.utcnow(), status='booking_qualified',
            terms_snapshot={'reward_amount_kobo': 200000, 'reward_expiration_days': 365},
        )
        db.session.add(referral)
        db.session.flush()
        booking = create_request()
        booking.customer_id = referred.id
        booking.status = 'Completed'
        booking.workflow_state = 'COMPLETED'
        booking.payment_status = 'Paid'
        booking.referral_id = referral.id
        booking.referral_discount_amount = 2000
        referral.qualifying_booking_id = booking.id
        db.session.commit()

        assert issue_referral_reward(booking) is None
        payment = Payment(
            booking_id=booking.id, amount=1000, status='Successful',
            payment_reference='verified-referral-payment', gateway='stripe', currency='NGN',
        )
        db.session.add(payment)
        db.session.flush()
        reward = issue_referral_reward(booking, actor_user_id=referrer.id)
        db.session.flush()
        repeated = issue_referral_reward(booking, actor_user_id=referrer.id)
        assert repeated.id == reward.id
        assert reward.amount_kobo == 200000
        assert reward.status == 'available'
        assert ReferralReward.query.filter_by(referral_id=referral.id).count() == 1
        assert RewardTransaction.query.filter_by(reward_id=reward.id, transaction_type='credit_issued').count() == 1
        expiration_time = datetime.utcnow() + timedelta(days=366)
        assert expire_referral_rewards(expiration_time) == 1
        assert expire_referral_rewards(expiration_time) == 0
        assert reward.status == 'expired'
        assert RewardTransaction.query.filter_by(reward_id=reward.id, transaction_type='credit_expired').count() == 1


def test_referral_credit_reversal_is_append_only_and_requires_a_reason(app):
    with app.app_context():
        campaign = get_campaign(create=True)
        referrer = User(first_name='Referral', last_name='Owner', email='reversal-referrer@example.com', phone='')
        referred = User(first_name='Referred', last_name='Customer', email='reversal-customer@example.com', phone='')
        referrer.set_password('password123')
        referred.set_password('password123')
        db.session.add_all([referrer, referred])
        db.session.flush()
        code = ensure_referral_code(referrer)
        booking = create_request()
        referral = Referral(
            referral_code_id=code.id, campaign_id=campaign.id,
            referrer_user_id=referrer.id, referred_user_id=referred.id,
            qualifying_booking_id=booking.id, status='rewarded',
            attributed_at=datetime.utcnow(), terms_snapshot={},
        )
        db.session.add(referral)
        db.session.flush()
        reward = ReferralReward(
            referral_id=referral.id, beneficiary_user_id=referrer.id,
            booking_id=booking.id, reward_type='referrer_credit',
            amount_kobo=200000, status='available',
            idempotency_key=f'referral:{referral.id}:test-credit',
        )
        db.session.add(reward)
        db.session.commit()

        with pytest.raises(ValueError, match='reason is required'):
            reverse_referral_reward(reward, referrer.id, '')
        reverse_referral_reward(reward, referrer.id, 'Confirmed duplicate account after review')
        db.session.commit()
        assert reward.status == 'reversed'
        assert ReferralReward.query.filter_by(id=reward.id).count() == 1
        transaction = RewardTransaction.query.filter_by(reward_id=reward.id, transaction_type='credit_reversed').one()
        assert transaction.amount_kobo == -200000
        assert transaction.description == 'Confirmed duplicate account after review'


def test_referral_dashboards_are_authorized_and_campaign_changes_are_audited(app):
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        get_campaign(create=True)
        customer = User(first_name='Dashboard', last_name='Customer', email='referral-dashboard@example.com', phone='')
        admin = User(first_name='Referral', last_name='Admin', email='referral-admin@example.com', phone='', role='super_admin')
        customer.set_password('password123')
        admin.set_password('password123')
        db.session.add_all([customer, admin])
        db.session.commit()
        customer_id, admin_id = customer.id, admin.id

    customer_client = app.test_client()
    with customer_client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(customer_id)
        browser_session['_fresh'] = True
    dashboard = customer_client.get('/customer/referrals')
    assert dashboard.status_code == 200
    assert b'Your referral code' in dashboard.data
    with app.app_context():
        assert ReferralCode.query.filter_by(user_id=customer_id).count() == 1

    forbidden_client = app.test_client()
    with forbidden_client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(customer_id)
        browser_session['_fresh'] = True
    assert forbidden_client.get('/admin/referrals').status_code == 403

    admin_client = app.test_client()
    with admin_client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(admin_id)
        browser_session['_fresh'] = True
    page = admin_client.get('/admin/referrals')
    assert page.status_code == 200
    assert b'Programme configuration' in page.data
    saved = admin_client.post('/admin/referrals', data={
        'action': 'configure', 'active': 'on',
        'reward_amount_naira': '2000', 'discount_amount_naira': '2000',
        'minimum_booking_naira': '10000', 'minimum_payable_naira': '1000',
        'max_referrals_per_customer': '20', 'max_referrals_total': '10000',
        'attribution_window_days': '30', 'reward_expiration_days': '365',
        'eligible_services': 'residential, commercial',
        'starts_at': '', 'ends_at': '',
    })
    assert saved.status_code == 302
    with app.app_context():
        campaign = get_campaign()
        assert campaign.active is True
        assert campaign.reward_amount_kobo == 200000
        assert campaign.eligible_services == ['commercial', 'residential']
        from moving_company.models import AuditLog
        assert AuditLog.query.filter_by(action='referral.campaign_updated').count() == 1
