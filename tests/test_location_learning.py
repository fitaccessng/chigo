from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import LocalPlace
from moving_company.services import location_service


def _app():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def test_search_uses_local_then_photon_then_customer_fallback(monkeypatch):
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_search_postgis_locations', lambda *args, **kwargs: [])
    monkeypatch.setattr(location_service, '_search_local_abuja_places', lambda *args, **kwargs: [])
    monkeypatch.setattr(location_service, '_photon_request', lambda *args, **kwargs: {
        'hits': [{
            'display_name': 'ABC Estate, Lokogoma',
            'osm_id': 101,
            'lat': 9.0,
            'lon': 7.4,
            'osm_type': 'way',
        }]
    })

    with _app().app_context():
        results = location_service.search_places('ABC Estate', limit=4)

    assert results[0]['name'] == 'ABC Estate, Lokogoma'
    assert results[0]['provider'] == 'photon'
    assert results[0]['coordinate_precision'] == 'street'


def test_customer_location_is_saved_sanitized_and_searchable(monkeypatch):
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_search_postgis_locations', lambda *args, **kwargs: [])
    monkeypatch.setattr(location_service, '_photon_request', lambda *args, **kwargs: {'hits': []})

    with _app().app_context():
        saved = location_service.save_customer_location(
            'ABC Estate, Lokogoma',
            latitude=9.0,
            longitude=7.4,
            source='manual',
            coordinate_precision='area',
        )
        later = location_service.search_places('ABC Estate', limit=5)
        duplicate = location_service.save_customer_location(
            'ABC Estate, Lokogoma',
            latitude=9.0,
            longitude=7.4,
            source='manual',
            coordinate_precision='area',
        )

    assert saved['id'] is not None
    assert saved['source'] == 'customer-entered'
    assert saved['coordinate_precision'] == 'area'
    assert saved['name'] == 'ABC Estate, Lokogoma'
    assert any(result['id'] == saved['id'] for result in later)
    assert duplicate['id'] == saved['id']
    assert duplicate['duplicate'] is True


def test_customer_location_does_not_persist_private_booking_data(monkeypatch):
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_search_postgis_locations', lambda *args, **kwargs: [])
    monkeypatch.setattr(location_service, '_photon_request', lambda *args, **kwargs: {'hits': []})

    with _app().app_context():
        location_service.save_customer_location(
            'ABC Estate, Lokogoma',
            latitude=9.0,
            longitude=7.4,
            source='manual',
            coordinate_precision='area',
            city='Abuja', district='Lokogoma', area_council='Abaji Area Council',
            landmark='ABC Estate', location_type='estate', confidence=0.75,
            precision_level='area',
        )
        place = LocalPlace.query.filter_by(name='ABC Estate, Lokogoma').one()
        assert place.name == 'ABC Estate, Lokogoma'
        assert not any(field in place.name.lower() for field in ('customer', 'phone', 'email', 'booking'))
        assert place.provider == 'customer-entered'
        assert place.city == 'Abuja'
        assert place.precision_level == 'area'
        assert place.confidence == 0.75


def test_location_usage_counters_are_updated(monkeypatch):
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_search_postgis_locations', lambda *args, **kwargs: [])
    monkeypatch.setattr(location_service, '_photon_request', lambda *args, **kwargs: {'hits': []})

    with _app().app_context():
        saved = location_service.save_customer_location(
            'ABC Estate, Lokogoma',
            latitude=9.0,
            longitude=7.4,
            source='manual',
            coordinate_precision='area',
        )
        location_service.record_location_usage(saved['id'], action='selection')
        location_service.record_location_usage(saved['id'], action='booking')
        place = LocalPlace.query.filter_by(id=saved['id']).one()

    assert place.search_count == 1
    assert place.selection_count == 1
    assert place.booking_count == 1
    assert place.last_used_at is not None


def test_route_fallback_returns_approximate_distance_and_source(monkeypatch):
    monkeypatch.setattr(location_service, '_osrm_request', lambda *args, **kwargs: None)
    route = location_service.route_distance(9.0, 7.4, 9.01, 7.41, fallback=True)

    assert route is not None
    assert route['source'] == 'approximate'
    assert route['distance_source'] == 'approximate'
    assert route['distance_km'] > 0


def test_booking_route_metadata_is_persisted(monkeypatch):
    from moving_company.services.booking_engine_service import create_booking_request

    app = _app()
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': 'Gwarinpa',
            'pickup_city': 'Abuja',
            'pickup_state': 'FCT',
            'destination_address': 'Wuse',
            'destination_city': 'Abuja',
            'destination_state': 'FCT',
            'pickup_latitude': 9.118,
            'pickup_longitude': 7.423,
            'destination_latitude': 9.056,
            'destination_longitude': 7.487,
            'distance_source': 'osrm-road-network',
            'distance_precision': 'street',
            'route_status': 'CALCULATED',
        })
        persisted = {
            'distance_source': booking.distance_source,
            'distance_precision': booking.distance_precision,
            'route_status': booking.route_status,
        }

    assert persisted == {
        'distance_source': 'osrm-road-network',
        'distance_precision': 'street',
        'route_status': 'CALCULATED',
    }


def test_booking_retains_explicit_location_ids(monkeypatch):
    from moving_company.services.booking_engine_service import create_booking_request

    app = _app()
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': 'Gwarinpa',
            'pickup_city': 'Abuja',
            'pickup_state': 'FCT',
            'destination_address': 'Wuse',
            'destination_city': 'Abuja',
            'destination_state': 'FCT',
            'pickup_location_id': 12,
            'dropoff_location_id': 34,
        })
        assert booking.pickup_location_id == 12
        assert booking.dropoff_location_id == 34
