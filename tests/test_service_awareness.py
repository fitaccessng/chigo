from datetime import date, timedelta

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, ServicePricing, SupportConversation, SupportRequest, User
from moving_company.routes.booking_engine import service_stages
from moving_company.services.booking_engine_service import (
    calculate_quote,
    create_booking_request,
    get_service_workflow,
    inventory_requirements,
    recommendations_for_booking,
    validate_service_bundle,
    vehicle_options,
)
from moving_company.services.service_workflow import (
    ADDON_QUESTION_SCHEMAS,
    SERVICE_QUESTION_SCHEMAS,
    property_types_for_service,
    service_question_schemas_for,
    service_questions_for,
    validate_property_type_for_service,
    validate_booking_question_data,
    validate_question_answers,
)


@pytest.fixture
def app():
    app = create_app(testing=True)
    yield app
    with app.app_context():
        db.session.remove()


def make_booking(app, service_type='residential'):
    with app.app_context():
        booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': '12 Yakubu Gowon Way',
                'pickup_city': 'Abuja',
                'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue',
                'destination_city': 'Abuja',
                'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type=service_type,
        )
        return booking.booking_request_id


def login_customer(app, client):
    with app.app_context():
        customer = User(first_name='Service', last_name='Customer', email='service-test@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True


def test_service_intent_is_persisted_and_validated(app):
    with app.app_context():
        booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='commercial',
        )
        assert booking.primary_service == 'commercial'
        assert booking.workflow_data['service_intent'] == 'commercial'
        assert get_service_workflow(booking)['questions']['property'] == tuple(
            field['key'] for field in service_questions_for('commercial', 'property')
        )
        booking.workflow_data['services'] = {'cleaning': 'move-out', 'home_decor': 'yes'}
        assert validate_service_bundle(booking) == ('cleaning', 'commercial', 'home_decor')
        with pytest.raises(ValueError, match='Unsupported service type'):
            create_booking_request(
                location={
                    'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                    'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                },
                service_type='unknown',
            )


@pytest.mark.parametrize('service_type', ['residential', 'commercial', 'packing', 'storage', 'logistics'])
def test_each_service_type_enters_unified_booking_engine_with_service_context(app, service_type):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)

    start = client.get(f'/booking/start/{service_type}')
    if service_type == 'storage':
        assert start.status_code == 200
        start = client.post('/booking/start/storage', data={
            'storage_collection': 'yes', 'storage_retrieval': 'yes', 'storage_return_known': 'no',
        })
    assert start.status_code == 302
    assert start.headers['Location'].endswith(f'/booking/location/{service_type}')

    location = client.post(
        f'/booking/location/{service_type}',
        data={
            'pickup_address': '12 Yakubu Gowon Way',
            'pickup_city': 'Abuja',
            'pickup_state': 'FCT',
            'destination_address': 'Plot 405 Constitution Avenue',
            'destination_city': 'Abuja',
            'destination_state': 'FCT',
        },
    )
    assert location.status_code == 302
    request_id = location.headers['Location'].split('/')[2]

    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.primary_service == service_type
        assert booking.workflow_data['service_intent'] == service_type
        assert booking.workflow_state == 'LOCATION_COMPLETED'


def test_cleaning_starts_with_three_options_and_saves_one_service_location(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)

    chooser = client.get('/booking/start/cleaning')
    html = chooser.get_data(as_text=True)
    assert chooser.status_code == 200
    assert html.count('name="cleaning_subtype"') == 3
    assert 'Cleaning at home' in html
    assert 'Office cleaning' in html
    assert 'Move-in / move-out' in html
    assert 'aria-label="Back to services"' in html

    chosen = client.post('/booking/start/cleaning', data={'cleaning_subtype': 'home'})
    assert chosen.status_code == 302
    assert chosen.headers['Location'].endswith('/booking/location/cleaning')
    location_page = client.get(chosen.headers['Location']).get_data(as_text=True)
    assert 'Service Location' in location_page
    assert 'Destination Location' not in location_page

    response = client.post('/booking/location/cleaning', data={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
    })
    assert response.status_code == 302
    request_id = response.headers['Location'].split('/')[2]
    repeated = client.post('/booking/location/cleaning', data={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
    })
    assert repeated.status_code == 302
    assert repeated.headers['Location'].split('/')[2] == request_id
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.primary_service == 'cleaning'
        assert booking.pickup_address == '12 Yakubu Gowon Way'
        assert booking.destination_address is None
        assert booking.workflow_data['property']['service_answers'] == {'cleaning_subtype': 'home'}


def test_support_starts_without_collecting_an_address(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)

    page = client.get('/booking/location/support')
    assert page.status_code == 200
    assert b'No address needed to start' in page.data
    assert b'pickup_search' not in page.data
    response = client.post('/booking/location/support', data={})
    assert response.status_code == 302
    request_id = response.headers['Location'].split('/')[2]
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.primary_service == 'support'
        assert booking.pickup_address is None
        assert booking.destination_address is None


def test_storage_can_start_without_collection_when_customer_delivers_items(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    page = client.get('/booking/start/storage')
    assert page.status_code == 200
    setup = client.post('/booking/start/storage', data={
        'storage_collection': 'no', 'storage_retrieval': 'no',
    })
    assert setup.status_code == 302
    page = client.get(setup.headers['Location']).get_data(as_text=True)
    assert 'Tell us what needs storage' in page
    assert 'Service Location' not in page
    response = client.post(setup.headers['Location'], data={})
    assert response.status_code == 302
    request_id = response.headers['Location'].split('/')[2]
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.primary_service == 'storage'
        assert booking.pickup_address is None
        answers = validate_question_answers(service_questions_for('storage', 'property'), {
            'storage_duration': '1 month', 'storage_collection': 'no',
            'storage_retrieval': 'no',
        })
        assert 'storage_collection_location' not in answers


def test_storage_collection_and_known_return_use_standard_location_records(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    setup = client.post('/booking/start/storage', data={
        'storage_collection': 'yes', 'storage_retrieval': 'yes', 'storage_return_known': 'yes',
    })
    location_url = setup.headers['Location']
    page = client.get(location_url).get_data(as_text=True)
    assert 'Collection Location' in page
    assert 'Return / Delivery Location' in page
    created = client.post(location_url, data={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
        'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
    })
    assert created.status_code == 302
    request_id = created.headers['Location'].split('/')[2]
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        answers = booking.workflow_data['property']['service_answers']
        assert answers['storage_collection'] == 'yes'
        assert answers['storage_retrieval'] == 'yes'
        assert booking.pickup_address == '12 Yakubu Gowon Way'
        assert booking.destination_address == 'Plot 405 Constitution Avenue'
        assert booking.distance_km == 0
        assert booking.route_status == 'NOT_REQUIRED'


def test_legacy_packing_answers_remain_valid_under_schema_v2(app):
    with app.app_context():
        booking = create_booking_request(
            location={'pickup_address': 'Packing address'}, service_type='packing',
        )
        booking.workflow_data = {
            'service_intent': 'packing', 'question_schema_version': 2,
            'property': {'service_answers': {
                'packing_for': 'home', 'packing_scope': 'selected-rooms', 'packing_rooms': 2,
                'packing_materials_needed': 'no', 'packing_materials_available': 'yes',
            }},
            'inventory_answers': {}, 'services': {}, 'addon_answers': {},
        }
        normalized = validate_booking_question_data(booking)
        assert normalized['property']['service_answers']['packing_scope'] == 'selected-rooms'
        assert 'packing_room_list' not in normalized['property']['service_answers']


def test_packing_payment_step_unlocks_only_after_quote_acceptance(app):
    with app.app_context():
        booking = create_booking_request(
            location={'pickup_address': 'Packing address'}, service_type='packing',
        )
        assert 'payment' not in service_stages('packing', booking)
        booking.workflow_state = 'QUOTE_ACCEPTED'
        assert service_stages('packing', booking)[-1] == 'payment'


@pytest.mark.parametrize('service_type', ['cleaning', 'storage', 'logistics'])
def test_quote_required_services_unlock_payment_only_after_quote_acceptance(app, service_type):
    with app.app_context():
        booking = create_booking_request(
            location={
                'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
            }, service_type=service_type,
        )
        assert 'payment' not in service_stages(service_type, booking)
        booking.workflow_state = 'QUOTE_ACCEPTED'
        assert service_stages(service_type, booking)[-1] == 'payment'


def _configure_rates(keys, values):
    for key in keys:
        row = ServicePricing.query.filter_by(key=key).first()
        if row is None:
            row = ServicePricing(key=key, label=key.replace('_', ' ').title(), value=values[key])
            db.session.add(row)
        row.value = values[key]
        row.is_configured = True


def test_cleaning_price_needs_approval_and_is_only_indicative_when_configured(app):
    with app.app_context():
        booking = create_booking_request(
            location={'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT'},
            service_type='cleaning', initial_service_answers={'cleaning_subtype': 'home'},
        )
        answers = {
            'cleaning_subtype': 'home', 'cleaning_property_type': 'flat',
            'cleaning_bedrooms': 2, 'cleaning_bathrooms': 1, 'cleaning_floors': 1,
            'cleaning_occupied': 'no', 'cleaning_type': 'deep',
            'cleaning_areas': ['kitchen', 'bathrooms'], 'cleaning_frequency': 'one-time',
        }
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'service_answers': answers},
            'inventory_answers': {'cleaning_heavy_buildup': 'no'},
            'schedule': {'date': (date.today() + timedelta(days=3)).isoformat(), 'time': 'Morning'},
        }
        booking.move_date = date.today() + timedelta(days=3)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        db.session.commit()

        unapproved = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert unapproved['indicative_total'] is None

        cleaning_keys = (
            'cleaning_home_base', 'cleaning_home_per_bedroom', 'cleaning_home_per_bathroom',
            'cleaning_office_base', 'cleaning_office_per_m2', 'cleaning_move_base',
            'cleaning_move_per_room', 'cleaning_move_per_bathroom', 'cleaning_deep_surcharge',
            'cleaning_post_renovation_surcharge',
        )
        _configure_rates(cleaning_keys, {
            'cleaning_home_base': 200, 'cleaning_home_per_bedroom': 10,
            'cleaning_home_per_bathroom': 5, 'cleaning_office_base': 100,
            'cleaning_office_per_m2': 1, 'cleaning_move_base': 150,
            'cleaning_move_per_room': 20, 'cleaning_move_per_bathroom': 10,
            'cleaning_deep_surcharge': 40, 'cleaning_post_renovation_surcharge': 100,
        })
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        indicative = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert indicative['indicative_total'] == 265
        assert sum(line['amount'] for line in indicative['estimate_details']['lines']) == 265
        with pytest.raises(ValueError, match='Only a ready quote'):
            from moving_company.services.booking_engine_service import accept_quote
            accept_quote(booking, None)


def test_recurring_cleaning_stays_quote_required_without_subscription_billing(app):
    with app.app_context():
        booking = create_booking_request(
            location={'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT'},
            service_type='cleaning', initial_service_answers={'cleaning_subtype': 'home'},
        )
        answers = {
            'cleaning_subtype': 'home', 'cleaning_property_type': 'flat',
            'cleaning_bedrooms': 1, 'cleaning_bathrooms': 1, 'cleaning_floors': 1,
            'cleaning_occupied': 'no', 'cleaning_type': 'regular',
            'cleaning_areas': ['kitchen'], 'cleaning_frequency': 'weekly',
        }
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'service_answers': answers},
            'inventory_answers': {'cleaning_heavy_buildup': 'no'},
            'schedule': {'date': (date.today() + timedelta(days=3)).isoformat(), 'time': 'Morning'},
        }
        booking.move_date = date.today() + timedelta(days=3)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        db.session.commit()
        quote = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert any('Recurring cleaning billing' in item for item in quote['manual_review_flags'])


def test_storage_approved_rates_remain_capacity_unconfirmed_and_indicative(app):
    with app.app_context():
        booking = create_booking_request(
            location={'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT'},
            service_type='storage', initial_service_answers={
                'storage_collection': 'yes', 'storage_retrieval': 'no', 'storage_return_known': 'no',
            },
        )
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'service_answers': {
                'storage_duration': '1 month', 'storage_collection': 'yes',
                'storage_retrieval': 'no', 'storage_return_known': 'no',
            }},
            'inventory_answers': {'stored_item_summary': 'Household boxes', 'estimated_storage_volume_m3': 10},
            'schedule': {'date': (date.today() + timedelta(days=3)).isoformat(), 'time': 'Morning'},
        }
        booking.move_date = date.today() + timedelta(days=3)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        db.session.commit()
        _configure_rates(
            ('storage_per_m3_week', 'storage_collection_fee', 'storage_redelivery_fee', 'storage_minimum_weeks', 'storage_week'),
            {'storage_per_m3_week': 5, 'storage_collection_fee': 100, 'storage_redelivery_fee': 80,
             'storage_minimum_weeks': 1, 'storage_week': 1},
        )
        quote = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert quote['indicative_total'] == 300
        assert any('capacity' in item.lower() for item in quote['manual_review_flags'])


def test_logistics_rates_can_be_configured_but_fleet_availability_stays_pending(app):
    with app.app_context():
        booking = create_booking_request(
            location={
                'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            }, service_type='logistics',
        )
        answers = {
            'delivery_urgency': 'scheduled', 'sender_name': 'Sender', 'sender_phone': '08000000001',
            'recipient_name': 'Recipient', 'recipient_phone': '08000000002',
            'shipment_type': 'Equipment', 'shipment_quantity': 2, 'shipment_weight_kg': 400,
            'shipment_length_cm': 100, 'shipment_width_cm': 50, 'shipment_height_cm': 50,
            'shipment_ready': 'yes', 'shipment_fragile': 'no', 'loading_assistance': 'yes',
            'shipment_special_equipment': 'no', 'delivery_instructions': 'Call on arrival',
        }
        booking.workflow_data = {
            **booking.workflow_data, 'inventory_answers': answers,
            'schedule': {'date': (date.today() + timedelta(days=3)).isoformat(), 'time': 'Morning'},
        }
        booking.move_date = date.today() + timedelta(days=3)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        options = vehicle_options(booking)
        selected = next(option for option in options if option['recommended'])
        booking.workflow_data = {**booking.workflow_data, 'vehicle': {'selected_type_id': selected['id']}}
        db.session.commit()
        keys = (
            'logistics_base_fee', 'logistics_per_vehicle_trip', 'logistics_per_km', 'logistics_per_kg',
            'logistics_per_m3', 'logistics_loading_fee', 'logistics_special_handling_fee', 'logistics_urgent_surcharge',
        )
        _configure_rates(keys, {key: 1 for key in keys})
        quote = calculate_quote(booking)
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert quote['indicative_total'] > 0
        assert any('vehicle' in item.lower() or 'fleet' in item.lower() for item in quote['manual_review_flags'])


def test_logistics_vehicle_recommendation_uses_shipment_measurements(app):
    with app.app_context():
        booking = create_booking_request(
            location={
                'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
            }, service_type='logistics',
        )
        booking.workflow_data = {
            **booking.workflow_data,
            'inventory_answers': {
                    'delivery_urgency': 'scheduled', 'sender_name': 'Sender', 'sender_phone': '08000000001',
                    'recipient_name': 'Recipient', 'recipient_phone': '08000000002',
                'shipment_type': 'Equipment', 'shipment_quantity': 3,
                'shipment_weight_kg': 900, 'shipment_length_cm': 250,
                'shipment_width_cm': 120, 'shipment_height_cm': 100,
                'shipment_ready': 'yes', 'shipment_fragile': 'yes',
                'loading_assistance': 'yes', 'shipment_special_equipment': 'no',
            },
        }
        options = vehicle_options(booking)
        recommended = next(option for option in options if option['recommended'])
        assert recommended['fits'] is True
        assert recommended['capacity_m3'] >= booking.estimated_volume_m3
        assert booking.estimated_weight_kg == 900


def test_home_cleaning_details_survive_to_quote_required_review(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    client.post('/booking/start/cleaning', data={'cleaning_subtype': 'home'})
    location = client.post('/booking/location/cleaning', data={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
    })
    request_id = location.headers['Location'].split('/')[2]
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    property_page = client.get(f'/booking/{request_id}/property')
    assert property_page.status_code == 200
    assert b'Cleaning details' in property_page.data

    details = client.post(f'/booking/{request_id}/property', data={
        'service_cleaning_subtype': 'home', 'service_cleaning_property_type': 'flat',
        'service_cleaning_bedrooms': '2', 'service_cleaning_bathrooms': '1',
        'service_cleaning_floors': '1', 'service_cleaning_occupied': 'no',
        'service_cleaning_type': 'deep', 'service_cleaning_areas': ['kitchen', 'bathrooms'],
        'service_cleaning_frequency': 'one-time',
    })
    assert details.status_code == 302

    inventory = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'save_and_continue', 'inventory_cleaning_heavy_buildup': 'no',
    })
    assert inventory.status_code == 302
    assert inventory.headers['Location'].endswith('/schedule')
    inventory_page = client.get(f'/booking/{request_id}/inventory')
    assert inventory_page.status_code == 200

    schedule_page = client.get(f'/booking/{request_id}/schedule')
    assert schedule_page.status_code == 200
    scheduled = client.post(f'/booking/{request_id}/schedule', data={
        'move_date': (date.today() + timedelta(days=3)).isoformat(),
        'preferred_time': 'Morning',
    })
    assert scheduled.status_code == 302
    quote_url = scheduled.headers['Location']
    quote = client.get(quote_url)
    assert quote.status_code == 200
    assert b'Quote Required' in quote.data
    assert b'indicative only' in quote.data
    assert b'Cleaning Bedrooms' in quote.data

    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.quote_status == 'Quote Required'
        assert booking.estimated_total == 0
        assert booking.workflow_data['property']['service_answers']['cleaning_type'] == 'deep'
        assert booking.workflow_data['property']['service_answers']['cleaning_areas'] == ['kitchen', 'bathrooms']


def test_support_triage_creates_linked_human_request_without_location(app, monkeypatch):
    from moving_company.services import support_chat_service

    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr(support_chat_service, 'send_human_escalation_email', lambda *args, **kwargs: None)
    client = app.test_client()
    login_customer(app, client)
    start = client.post('/booking/location/support', data={})
    request_id = start.headers['Location'].split('/')[2]
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    details = client.post(f'/booking/{request_id}/property', data={
        'service_support_category': 'urgent-delivery', 'service_support_urgency': 'today',
        'service_support_description': 'A delivery has been delayed.', 'service_onsite_required': 'no',
    })
    assert details.status_code == 302
    inventory = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'save_and_continue', 'inventory_support_contact_preference': 'phone',
    })
    assert inventory.status_code == 302
    assert inventory.headers['Location'].endswith('/quote')
    retry = client.post(f'/booking/{request_id}/inventory', data={
        'action': 'save_and_continue', 'inventory_support_contact_preference': 'phone',
    })
    assert retry.status_code == 302
    assert retry.headers['Location'].endswith('/quote')

    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        conversation = SupportConversation.query.filter_by(booking_id=booking.id).one()
        support_requests = SupportRequest.query.filter_by(booking_id=booking.id).all()
        support_request = support_requests[0]
        assert len(support_requests) == 1
        assert booking.quote_status == 'Quote Required'
        assert booking.pickup_address is None and booking.destination_address is None
        assert support_request.status == 'WAITING_FOR_AGENT'
        assert 'Urgency: today' in support_request.reason


@pytest.mark.parametrize(
    ('service_type', 'property_type', 'expected_label', 'excluded_label'),
    [
        ('residential', 'Apartment', 'Destination property type', 'Business premises type'),
        ('commercial', 'Office', 'Current workspace size', 'Bedrooms at pickup'),
        ('packing', None, 'Packing scope', 'What type of residential property'),
        ('storage', None, 'Expected storage duration', 'What type of residential property'),
        ('logistics', None, 'No separate property questions', 'What type of residential property'),
    ],
)
def test_property_stage_renders_only_selected_service_questions(app, service_type, property_type, expected_label, excluded_label):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, service_type)
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    if property_type:
        selected = client.post(f'/booking/{request_id}/property', data={
            'action': 'choose_property_type', 'property_type': property_type,
        })
        assert selected.status_code == 302
    page = client.get(f'/booking/{request_id}/property').get_data(as_text=True)
    if service_type == 'logistics':
        assert 'No separate property questions are needed' in page
        with app.app_context():
            booking = Booking.query.filter_by(booking_request_id=request_id).one()
            booking.workflow_state = 'PROPERTY_COMPLETED'
            db.session.commit()
        inventory_page = client.get(f'/booking/{request_id}/inventory').get_data(as_text=True)
        assert 'Approximate total weight (kg)' in inventory_page
        assert 'Bedrooms' not in inventory_page
    else:
        assert expected_label in page
    assert excluded_label not in page


def test_property_stage_requires_and_persists_service_answers(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, 'residential')
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    missing = client.put(f'/api/bookings/{request_id}/property', json={
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1, 'floors': 1,
    })
    assert missing.status_code == 422
    assert 'Destination property type is required' in missing.get_json()['error']

    answers = {
        'destination_property_type': 'Apartment',
        'restricted_vehicle_access': 'no',
        'long_carry_distance': 'no',
    }
    saved = client.put(f'/api/bookings/{request_id}/property', json={
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1, 'floors': 1,
        'service_answers': answers,
    })
    assert saved.status_code == 200
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['property']['service_answers'] == answers
    item_response = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Sofa', 'quantity': 1, 'category': 'Living Room'}],
        'inventory_answers': {'heavy_or_oversized': 'no'},
    })
    assert item_response.status_code == 200
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['inventory_answers'] == {'heavy_or_oversized': 'no'}


def test_primary_service_intent_is_immutable_for_versioned_booking(app):
    from moving_company.services.service_workflow import validate_booking_question_data

    request_id = make_booking(app, 'residential')
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        booking.primary_service = 'commercial'
        with pytest.raises(ValueError, match='primary service.*cannot be changed'):
            validate_booking_question_data(booking)


def test_above_ground_location_requires_separate_lift_answer(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    location = {
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
        'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        'pickup_floor': '2nd', 'destination_floor': 'Ground',
    }
    with client.session_transaction() as session:
        pass
    missing_lift = client.post('/booking/location/residential', data=location)
    assert missing_lift.status_code == 200
    assert b'Confirm whether a lift is available at the above-ground pickup' in missing_lift.data

    location['pickup_lift_available'] = 'no'
    saved = client.post('/booking/location/residential', data=location)
    assert saved.status_code == 302
    request_id = saved.headers['Location'].split('/')[2]
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['location_access'] == {
            'pickup_lift_available': False, 'destination_lift_available': None,
        }


def test_logistics_dimensions_and_weight_drive_existing_vehicle_requirements(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, 'logistics')
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    response = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Shipment', 'quantity': 2, 'category': 'Shipment'}],
        'inventory_answers': {
            'delivery_urgency': 'scheduled', 'sender_name': 'Sender', 'sender_phone': '08000000001',
            'recipient_name': 'Recipient', 'recipient_phone': '08000000002',
            'shipment_type': 'Equipment', 'shipment_quantity': 2,
            'shipment_weight_kg': 400, 'shipment_length_cm': 100,
            'shipment_width_cm': 50, 'shipment_height_cm': 50,
            'shipment_ready': 'yes', 'shipment_fragile': 'no',
            'loading_assistance': 'yes', 'shipment_special_equipment': 'no',
        },
    })
    assert response.status_code == 200
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        requirements = inventory_requirements(booking)
        assert requirements['weight_kg'] == 400
        assert requirements['volume_m3'] > 0.5


@pytest.mark.parametrize(
    ('service_type', 'expected_text', 'excluded_text'),
    [
        ('residential', 'Household items not covered by the catalogue', 'Approximate total weight (kg)'),
        ('commercial', 'Business stock, office equipment or inventory notes', 'Bedrooms'),
        ('packing', 'Which selected items need packing or unpacking?', 'Business premises type'),
        ('storage', 'What items need storage?', 'Bedrooms'),
        ('logistics', 'What are you transporting?', 'Bedrooms'),
    ],
)
def test_inventory_stage_renders_service_specific_questions(app, service_type, expected_text, excluded_text):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, service_type)
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    html = client.get(f'/booking/{request_id}/inventory').get_data(as_text=True)
    assert expected_text in html
    assert excluded_text not in html


def _valid_schema_answers(schema):
    answers = {}
    for field in schema:
        condition = field['condition']
        if condition and answers.get(condition[0]) != condition[1]:
            continue
        if not field['required']:
            continue
        if field['type'] == 'multiselect':
            answers[field['key']] = list(field['options'][:2])
        elif field['type'] == 'select':
            answers[field['key']] = field['options'][0]
        elif field['type'] == 'number':
            answers[field['key']] = max(field['min'] or 1, 1)
        else:
            answers[field['key']] = 'Test details'
    return answers


@pytest.mark.parametrize('service_type', ['residential', 'commercial'])
def test_shared_move_reuses_location_and_accepts_distinct_addon_sections(app, service_type):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, service_type)
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    property_type = 'Apartment' if service_type == 'residential' else 'Office'
    property_answers = _valid_schema_answers(service_questions_for(service_type, 'property'))
    property_response = client.put(f'/api/bookings/{request_id}/property', json={
        'property_type': property_type,
        'bedrooms': 2 if service_type == 'residential' else 0,
        'bathrooms': 1 if service_type == 'residential' else 0,
        'service_answers': property_answers,
    })
    assert property_response.status_code == 200
    inventory_answers = _valid_schema_answers(service_questions_for(service_type, 'inventory'))
    inventory_response = client.post(f'/api/bookings/{request_id}/inventory', json={
        'items': [{'name': 'Sofa', 'quantity': 1, 'category': 'Living Room'}],
        'inventory_answers': inventory_answers,
    })
    assert inventory_response.status_code == 200

    selections = {'packing': 'partial', 'storage': '1 month', 'cleaning': 'move-out', 'home_decor': 'yes'}
    addon_answers = {
        addon: {
            section: _valid_schema_answers(schema)
            for section, schema in ADDON_QUESTION_SCHEMAS[addon].items()
        }
        for addon in selections
    }
    service_response = client.put(f'/api/bookings/{request_id}/services', json={
        **selections, 'addon_answers': addon_answers,
    })
    assert service_response.status_code == 200

    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['property']['service_answers'] == property_answers
        assert booking.workflow_data['services'] == selections
        assert booking.workflow_data['addon_answers'] == addon_answers
        assert booking.pickup_address == '12 Yakubu Gowon Way'
        assert booking.destination_address == 'Plot 405 Constitution Avenue'


def test_addon_question_answers_are_required_and_separate_from_quote_selections(app):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)
    request_id = make_booking(app, 'residential')
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        booking.workflow_state = 'INVENTORY_COMPLETED'
        db.session.commit()
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    invalid = client.put(f'/api/bookings/{request_id}/services', json={
        'cleaning': 'move-out', 'addon_answers': {},
    })
    assert invalid.status_code == 422
    assert 'Which existing booking location needs cleaning? is required' in invalid.get_json()['error']

    answers = {
        'cleaning': {
            'property': {
                'cleaning_at': 'destination', 'cleaning_property_type': 'Apartment',
                'cleaning_bedrooms': 2, 'cleaning_bathrooms': 1, 'cleaning_occupancy': 'no',
            },
            'inventory': {'cleaning_condition': 'normal', 'cleaning_areas': 'Kitchen and bathrooms'},
        },
    }
    saved = client.put(f'/api/bookings/{request_id}/services', json={
        'cleaning': 'move-out', 'addon_answers': answers,
    })
    assert saved.status_code == 200
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['services'] == {'cleaning': 'move-out'}
        assert booking.workflow_data['addon_answers'] == answers

    unexpected = client.put(f'/api/bookings/{request_id}/services', json={
        'addon_answers': {'home_decor': {'property': {'decor_room_count': 3}}},
    })
    assert unexpected.status_code == 422
    assert 'Select the home decor service' in unexpected.get_json()['error']


def test_admin_booking_summary_renders_nested_service_answers(app):
    with app.app_context():
        booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='commercial',
        )
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'property_type': 'Office', 'service_answers': {'business_work_areas': 4, 'current_workspace_size_m2': 400}},
            'services': {'cleaning': 'move-out'},
            'addon_answers': {'cleaning': {'property': {'cleaning_at': 'destination', 'cleaning_property_type': 'Office'}, 'inventory': {'cleaning_condition': 'normal', 'cleaning_areas': 'Office floor'}}},
        }
        db.session.commit()
        request_id = booking.booking_request_id
        admin = User.query.filter_by(email='admin@chigo.com').one()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    response = client.get(f'/admin/bookings/{booking.id}')
    assert response.status_code == 200
    assert b'Primary service' in response.data
    assert b'Commercial' in response.data
    assert b'Property Type' in response.data
    assert b'Current Workspace Size M2' in response.data
    assert b'Cleaning Property Type' in response.data
    assert b'Cleaning At' in response.data


@pytest.mark.parametrize(
    ('addon', 'expected_services'),
    [('cleaning', {'cleaning': 'move-out'}), ('home_decor', {'home_decor': 'yes'})],
)
def test_addon_booking_start_prefills_requested_service(app, addon, expected_services):
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    login_customer(app, client)

    start = client.get('/booking/start', query_string={'addon': addon})
    assert start.status_code == 302
    assert start.headers['Location'].endswith('/booking/location/residential')

    location = client.post(
        '/booking/location/residential',
        data={
            'pickup_address': '12 Yakubu Gowon Way',
            'pickup_city': 'Abuja',
            'pickup_state': 'FCT',
            'destination_address': 'Plot 405 Constitution Avenue',
            'destination_city': 'Abuja',
            'destination_state': 'FCT',
        },
    )
    assert location.status_code == 302
    request_id = location.headers['Location'].split('/')[2]

    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['services'] == expected_services


def test_pack_and_move_recommendation_is_one_bundle(app):
    with app.app_context():
        booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='packing',
        )
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'property_type': 'Apartment', 'rooms': 2},
            'inventory': {'pickup_location': 'A', 'destination_location': 'B'},
            'services': {'packing': 'full', 'transport': 'yes'},
        }
        booking.primary_service = 'packing'
        db.session.commit()
        assert validate_service_bundle(booking) == ('packing', 'transport')
        recommendations = recommendations_for_booking(booking)
        assert not any(item['key'] == 'transport' for item in recommendations)
        assert get_service_workflow(booking)['steps'] == ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review')

        recommendation_booking = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='packing',
        )
        assert any(item['key'] == 'transport' for item in recommendations_for_booking(recommendation_booking))


def test_storage_and_logistics_requirements_are_service_specific(app):
    with app.app_context():
        storage = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='storage',
        )
        storage.workflow_data['property'] = {'storage_duration': '1 month', 'access_frequency': 'monthly', 'pickup_method': 'collect'}
        storage.workflow_data['inventory'] = {'item_count': 8, 'storage_capacity': 'large'}
        assert get_service_workflow(storage)['questions']['inventory'] == tuple(
            field['key'] for field in service_questions_for('storage', 'inventory')
        )
        assert recommendations_for_booking(storage)[0]['key'] == 'storage_duration'

        logistics = create_booking_request(
            customer_id=None,
            location={
                'pickup_address': 'A', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': 'B', 'destination_city': 'Abuja', 'destination_state': 'FCT',
                'distance_km': 20,
            },
            service_type='logistics',
        )
        logistics.workflow_data['property'] = {'item_type': 'documents', 'weight_kg': 20, 'dimensions': '40x30x20'}
        assert get_service_workflow(logistics)['questions']['inventory'] == tuple(
            field['key'] for field in service_questions_for('logistics', 'inventory')
        )


def test_property_types_are_service_specific_and_validated():
    assert property_types_for_service('residential') == ('Apartment', 'House', 'Duplex')
    assert property_types_for_service('commercial') == ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site')
    assert property_types_for_service('packing') == ()

    assert validate_property_type_for_service('residential', 'Apartment') == 'Apartment'
    assert validate_property_type_for_service('commercial', 'Warehouse') == 'Warehouse'

    with pytest.raises(ValueError, match='commercial.*Apartment'):
        validate_property_type_for_service('commercial', 'Apartment')

    with pytest.raises(ValueError, match='residential.*Office'):
        validate_property_type_for_service('residential', 'Office')


def test_question_schemas_are_service_specific_and_validate_conditionals():
    assert set(SERVICE_QUESTION_SCHEMAS) == {'cleaning', 'residential', 'commercial', 'packing', 'storage', 'logistics', 'support'}
    assert 'shipment_weight_kg' in {field['key'] for field in service_questions_for('logistics', 'inventory')}
    assert 'business_operating_during_move' in {field['key'] for field in service_questions_for('commercial', 'property')}
    assert 'packing_scope' in {field['key'] for field in service_questions_for('packing', 'property')}
    assert 'storage_duration' in {field['key'] for field in service_questions_for('storage', 'property')}
    assert {'home', 'office', 'move-in-out'} == set(next(
        field for field in service_questions_for('cleaning', 'property')
        if field['key'] == 'cleaning_subtype'
    )['options'])
    assert 'onsite_required' in {field['key'] for field in service_questions_for('support', 'property')}
    assert next(field for field in ADDON_QUESTION_SCHEMAS['home_decor']['property'] if field['key'] == 'decor_rooms')['type'] == 'multiselect'
    assert {'property', 'inventory'} == set(service_question_schemas_for('residential'))
    assert {'property', 'inventory'} == set(ADDON_QUESTION_SCHEMAS['cleaning'])
    assert {'packing', 'storage', 'cleaning', 'home_decor'} == set(ADDON_QUESTION_SCHEMAS)

    with pytest.raises(ValueError, match='Approximate total weight'):
        validate_question_answers(service_questions_for('logistics', 'inventory'), {
            'delivery_urgency': 'scheduled', 'sender_name': 'Sender', 'sender_phone': '08000000001',
            'recipient_name': 'Recipient', 'recipient_phone': '08000000002',
            'shipment_type': 'Furniture', 'shipment_quantity': 1,
        })

    answers = validate_question_answers(service_questions_for('residential', 'property'), {
        'destination_property_type': 'Apartment',
        'destination_property_other': 'This stale value is ignored',
        'restricted_vehicle_access': 'no',
        'long_carry_distance': 'no',
    })
    assert 'destination_property_other' not in answers

    inventory_answers = validate_question_answers(service_questions_for('residential', 'inventory'), {
        'heavy_or_oversized': 'no',
    })
    assert inventory_answers == {'heavy_or_oversized': 'no'}

    with pytest.raises(ValueError, match='Describe the destination property is required'):
        validate_question_answers(service_questions_for('residential', 'property'), {
            'destination_property_type': 'Other',
            'restricted_vehicle_access': 'no',
            'long_carry_distance': 'no',
        })

    with pytest.raises(ValueError, match='Unknown service answer'):
        validate_question_answers(service_questions_for('commercial', 'property'), {'packing_full_price': 1})

    with pytest.raises(ValueError, match='Bedrooms or rooms is required'):
        validate_question_answers(service_questions_for('cleaning', 'property'), {
            'cleaning_subtype': 'home', 'cleaning_property_type': 'flat',
            'cleaning_floors': 1, 'cleaning_occupied': 'no', 'cleaning_type': 'deep',
            'cleaning_areas': ['kitchen'], 'cleaning_frequency': 'one-time',
        })

    all_cleaning_areas = validate_question_answers(service_questions_for('cleaning', 'property'), {
        'cleaning_subtype': 'home', 'cleaning_property_type': 'flat',
        'cleaning_bedrooms': 2, 'cleaning_bathrooms': 1, 'cleaning_floors': 1,
        'cleaning_occupied': 'no', 'cleaning_type': 'deep',
        'cleaning_areas': ['entire-property'], 'cleaning_frequency': 'one-time',
    })
    assert 'entire-property' not in all_cleaning_areas['cleaning_areas']
    assert {'living-room', 'kitchen', 'bathrooms'}.issubset(all_cleaning_areas['cleaning_areas'])
