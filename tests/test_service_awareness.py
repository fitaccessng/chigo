from datetime import date, timedelta

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking
from moving_company.services.booking_engine_service import (
    create_booking_request,
    get_service_workflow,
    recommendations_for_booking,
    validate_service_bundle,
)
from moving_company.services.service_workflow import (
    property_types_for_service,
    validate_property_type_for_service,
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
        assert get_service_workflow(booking)['questions']['property'] == ('business_name', 'business_type', 'workstations', 'rooms', 'loading_access', 'elevator')
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

    start = client.get(f'/booking/start/{service_type}')
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
        assert get_service_workflow(storage)['questions']['inventory'] == ('storage_items', 'storage_duration', 'access_frequency', 'pickup_method', 'delivery_method')
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
        assert get_service_workflow(logistics)['questions']['property'] == ('item_type', 'quantity', 'weight_kg', 'dimensions', 'fragile', 'recipient_name')


def test_property_types_are_service_specific_and_validated():
    assert property_types_for_service('residential') == ('Apartment', 'House', 'Duplex')
    assert property_types_for_service('commercial') == ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site')
    assert property_types_for_service('packing') == ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate')

    assert validate_property_type_for_service('residential', 'Apartment') == 'Apartment'
    assert validate_property_type_for_service('commercial', 'Warehouse') == 'Warehouse'

    with pytest.raises(ValueError, match='commercial.*Apartment'):
        validate_property_type_for_service('commercial', 'Apartment')

    with pytest.raises(ValueError, match='residential.*Office'):
        validate_property_type_for_service('residential', 'Office')
