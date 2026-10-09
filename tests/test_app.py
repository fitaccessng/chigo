from moving_company import create_app
from moving_company.extensions import db
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from sqlalchemy import func
import pytest

from moving_company.models import AuditLog, Booking, BookingAssignment, BookingEvent, Cleaner, IncidentReport, InventoryItem, LocalPlace, Mover, Payment, Review, ServicePricing, Truck, TruckPartner, User
from moving_company.services.assignment_service import check_assignment_conflict
from moving_company.services.booking_engine_service import create_booking_request
from moving_company.services.fct_location_data import FCT_AREA_COUNCILS
from moving_company.services import location_service
from moving_company.services.location_service import normalize_query, search_places


def test_postgres_database_url_uses_installed_psycopg2_driver():
    from moving_company.config import normalize_database_url
    from sqlalchemy.engine import make_url

    normalized = make_url(normalize_database_url(
        'postgresql://user:secret@db.example:10001/chigo?sslmode=require'
    ))

    assert normalized.drivername == 'postgresql+psycopg2'
    assert normalized.host == 'db.example'
    assert normalized.port == 10001
    assert normalized.database == 'chigo'
    assert normalized.query['sslmode'] == 'require'
    assert normalized.password == 'secret'


def test_unavailable_postgres_fails_startup_instead_of_using_sqlite(monkeypatch):
    import moving_company
    from flask import Flask
    from sqlalchemy.exc import SQLAlchemyError

    class UnavailableConnection:
        def __enter__(self):
            raise SQLAlchemyError('database unavailable')

        def __exit__(self, *_args):
            return False

    class Engine:
        def connect(self):
            return UnavailableConnection()

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql+psycopg2://user:secret@db.example/chigo'
    monkeypatch.setattr(moving_company, 'db', type('DB', (), {'engine': Engine()})())

    with pytest.raises(RuntimeError, match='PostgreSQL/PostGIS is required'):
        moving_company.validate_database_connection(app)


def test_postgis_location_payload_normalizes_fct_records():
    from moving_company.services.postgis_ingestion import normalize_location_payload

    payload = {
        'name': 'Gwarinpa',
        'area_council': 'Abuja Municipal Area Council',
        'district': 'Gwarinpa',
        'neighborhood': 'Gwarinpa',
        'latitude': 9.118,
        'longitude': 7.423,
        'aliases': ['Gwarimpa', 'Gwarinpa Estate'],
        'source': 'fct-curated',
        'place_type': 'district',
    }

    normalized = normalize_location_payload(payload)

    assert normalized['normalized_name'] == 'gwarinpa'
    assert normalized['area_council'] == 'Abuja Municipal Area Council'
    assert normalized['centroid']['type'] == 'Point'
    assert normalized['centroid']['coordinates'] == [7.423, 9.118]
    assert normalized['aliases'] == ['gwarimpa', 'gwarinpa estate']


def test_geodata_ingestion_detects_fct_from_boundary_geometry(tmp_path):
    import json
    from moving_company.services.postgis_ingestion import _load_fct_boundary, _boundary_features
    from shapely.geometry import Polygon

    boundary = {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'properties': {'shapeName': 'Abuja Federal Capital Territory', 'shapeID': 'fct-test'},
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[[6.8, 8.4], [7.8, 8.4], [7.8, 9.6], [6.8, 9.6], [6.8, 8.4]]],
            },
        }],
    }
    boundary_path = tmp_path / 'adm1.geojson'
    boundary_path.write_text(json.dumps(boundary))

    geometry, properties = _load_fct_boundary(boundary_path)

    assert isinstance(geometry, Polygon)
    assert properties['shapeID'] == 'fct-test'
    assert list(_boundary_features(boundary_path, geometry))[0][0] == 'Abuja Federal Capital Territory'


def test_geodata_ingestion_maps_real_geoboundaries_council_labels():
    from moving_company.services.postgis_ingestion import COUNCILS, _classify_osm

    assert COUNCILS['municipal area council'] == 'Abuja Municipal Area Council'
    assert _classify_osm({'amenity': 'hospital'}) == 'hospital'
    assert _classify_osm({'highway': 'residential'}) == 'street'
    assert _classify_osm({'place': 'village'}) == 'village'


def test_osm_handler_clips_named_records_and_counts_source_types(tmp_path):
    from collections import Counter
    from moving_company.services.postgis_ingestion import OsmFctHandler
    from shapely.geometry import Point, Polygon

    handler = object.__new__(OsmFctHandler)
    handler.fct_geometry = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    handler.council_boundaries = {'AMAC': handler.fct_geometry}
    handler.records = []
    handler.objects_processed = 1
    handler.named_objects = 0
    handler.discarded = 0
    handler.categories = Counter()

    class Tags(dict):
        def __iter__(self):
            return iter(self.items())

    class Node:
        id = 44
        tags = Tags(name='Test Hospital', amenity='hospital')

    Node.__name__ = 'Node'
    handler._append(Node(), Point(1, 1))

    assert handler.named_objects == 1
    assert handler.records[0]['name'] == 'Test Hospital'
    assert handler.records[0]['place_type'] == 'hospital'
    assert handler.records[0]['source_id'] == 'node:44'
    assert handler.categories['hospital'] == 1


def test_boundary_ingestion_dry_run_reports_polygons_without_database(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from moving_company.services import postgis_ingestion

    boundary = {
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'properties': {'shapeName': 'Abuja Federal Capital Territory', 'shapeID': 'fct'},
                'geometry': {'type': 'Polygon', 'coordinates': [[[6.8, 8.4], [7.8, 8.4], [7.8, 9.6], [6.8, 9.6], [6.8, 8.4]]]},
            },
            {
                'type': 'Feature',
                'properties': {'shapeName': 'Municipal Area Council', 'shapeID': 'amac'},
                'geometry': {'type': 'Polygon', 'coordinates': [[[7.0, 8.7], [7.6, 8.7], [7.6, 9.4], [7.0, 9.4], [7.0, 8.7]]]},
            },
        ],
    }
    adm1 = tmp_path / 'adm1.geojson'
    adm2 = tmp_path / 'adm2.geojson'
    adm1.write_text(json.dumps(boundary))
    adm2.write_text(json.dumps(boundary))
    monkeypatch.setattr(postgis_ingestion, 'download_sources', lambda *args, **kwargs: {
        'boundaries_adm1': adm1, 'boundaries_adm2': adm2,
    })
    monkeypatch.setattr(postgis_ingestion, '_validate_postgis_schema', lambda: (_ for _ in ()).throw(AssertionError('dry run must not connect to Postgres')))

    report = postgis_ingestion.ingest(SimpleNamespace(
        data_dir=str(tmp_path), all=False, download=False, import_osm=False,
        import_geonames=False, import_boundaries=True, dry_run=True, force=False,
    ))

    assert report['boundary_records'] == 2
    assert report['area_councils_resolved'] == 1
    assert report['canonical_locations'] == 0


def test_geodata_download_resumes_after_interrupted_response(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from moving_company.services.postgis_ingestion import _http_download

    payload = b'geodata-block-' * 12000
    state = {'requests': 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'] += 1
            range_header = self.headers.get('Range')
            start = int(range_header.split('=')[1].split('-')[0]) if range_header else 0
            response = payload[start:]
            self.send_response(206 if range_header else 200)
            if range_header:
                self.send_header('Content-Range', f'bytes {start}-{len(payload) - 1}/{len(payload)}')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            if not range_header:
                self.wfile.write(response[:2048])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(response)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _http_download(f'http://127.0.0.1:{server.server_port}/data', tmp_path / 'data.pbf', timeout=2, retries=2)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.read_bytes() == payload
    assert state['requests'] >= 2


def test_app_factory_creates_app():
    app = create_app(testing=True)
    assert app is not None
    assert app.config['TESTING'] is True


def test_public_home_uses_responsive_chigo_logo_without_brand_intro():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.get('/')
    logo_response = client.get('/static/images/chigo-logo.svg')

    assert response.status_code == 200
    assert b'id="brand-intro"' not in response.data
    assert b'brand-intro.css' not in response.data
    assert b'/static/images/chigo-logo.svg' in response.data
    assert logo_response.status_code == 200
    assert b'<svg' in logo_response.data


def test_postgres_config_disables_automatic_schema_creation(monkeypatch):
    from moving_company.config import Config

    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', 'postgresql+psycopg2://user:pass@db.example/chigo')
    monkeypatch.setattr(Config, 'AUTO_CREATE_SCHEMA', False)
    monkeypatch.setenv('FLASK_SKIP_DB_PREFLIGHT', '1')
    app = create_app(testing=False)

    assert app.config['AUTO_CREATE_SCHEMA'] is False


def test_local_place_merge_reassigns_booking_references_and_preserves_aliases():
    app = create_app(testing=True)
    with app.app_context():
        source = LocalPlace.query.filter_by(normalized_name='gwarinpa').one()
        target = LocalPlace.query.filter_by(normalized_name='gwarinpa estate').one()
        booking = Booking.query.first()
        if booking is None:
            from moving_company.services.booking_engine_service import create_booking_request
            booking = create_booking_request(location={
                'pickup_address': 'Gwarinpa', 'pickup_city': 'Abuja', 'pickup_state': 'Federal Capital Territory',
                'destination_address': 'Gwarinpa Estate', 'destination_city': 'Abuja', 'destination_state': 'Federal Capital Territory',
                'pickup_local_place_id': source.id, 'destination_local_place_id': source.id,
            })
        booking.pickup_local_place_id = source.id
        booking.destination_local_place_id = source.id
        db.session.commit()

        with pytest.raises(ValueError, match='different types'):
            location_service.merge_local_places(source.id, target.id)

        second_target = LocalPlace(
            name='Gwarinpa Duplicate', normalized_name='gwarinpa duplicate',
            place_type=source.place_type, area_council=source.area_council,
            latitude=source.latitude + 0.0001, longitude=source.longitude,
            aliases='Gwarinpa Alternate', verified=True, active=True,
        )
        db.session.add(second_target)
        db.session.flush()
        source_id, target_id, booking_id = source.id, second_target.id, booking.id
        location_service.merge_local_places(source_id, target_id)

        merged_booking = db.session.get(Booking, booking_id)
        assert merged_booking.pickup_local_place_id == target_id
        assert merged_booking.destination_local_place_id == target_id
        assert LocalPlace.query.filter_by(id=source_id).one().active is False
        assert 'Gwarinpa' in LocalPlace.query.filter_by(id=target_id).one().aliases


def test_location_query_normalization_handles_abuja_variants():
    assert normalize_query('Market Square Jikowyi') == 'market square jikwoyi'
    assert normalize_query('Market Square Jikoyi') == 'market square jikwoyi'
    assert normalize_query('Gwarimpa') == 'gwarinpa'
    assert normalize_query('Wuse 2') == 'wuse ii'
    assert normalize_query('Life Camp') == 'life camp'


def test_autocomplete_endpoint_returns_search_results_without_crashing():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.get('/api/locations/autocomplete?q=Market%20Square%20Jikowyi')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert isinstance(payload['results'], list)


def test_local_fct_autocomplete_is_ranked_without_calling_nominatim(monkeypatch):
    app = create_app(testing=True)
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_nominatim_request', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('external lookup must not run for local matches')))
    with app.app_context():
        assert LocalPlace.query.filter(func.lower(LocalPlace.normalized_name) == 'gwarinpa', LocalPlace.active.is_(True)).count() == 1
        assert LocalPlace.query.filter(func.lower(LocalPlace.normalized_name) == 'wuse', LocalPlace.active.is_(True)).count() == 1
        gwarinpa = search_places('gwar', limit=8)
        maitama = search_places('mait', limit=8)
        kubwa = search_places('kub', limit=8)
        airport = search_places('airport', limit=8)
        councils = [search_places(council, limit=8) for council in FCT_AREA_COUNCILS]

    assert gwarinpa[0]['name'] == 'Gwarinpa'
    assert gwarinpa[0]['area_council'] == 'Abuja Municipal Area Council'
    assert maitama[0]['name'] == 'Maitama'
    assert kubwa[0]['name'] == 'Kubwa'
    assert airport[0]['name'] == 'Nnamdi Azikiwe International Airport'
    assert all(results and results[0]['place_id'] for results in councils)


def test_route_distance_uses_validated_road_network_provider(monkeypatch):
    app = create_app(testing=True)
    calls = []
    monkeypatch.setattr(location_service, '_osrm_request', lambda path, params=None, timeout=5: calls.append((path, params)) or {'routes': [{'distance': 18400, 'duration': 1920}]})
    with app.app_context():
        route = location_service.route_distance(9.118, 7.423, 9.056, 7.487)
        invalid = location_service.route_distance(900, 7.4, 9.0, 7.5)
    assert route['distance_km'] == 18.4
    assert route['duration_minutes'] == 32
    assert route['provider'] == 'osrm'
    assert len(calls) == 1
    assert invalid is None


def test_location_save_resolves_local_references_and_allows_pending_route(monkeypatch):
    from moving_company.services import location_service

    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_osrm_request', lambda *args, **kwargs: None)
    with app.app_context():
        pickup = LocalPlace.query.filter_by(normalized_name='gwarinpa').first()
        destination = LocalPlace.query.filter_by(normalized_name='wuse').first()
        pickup_id, destination_id = pickup.id, destination.id
        expected_pickup = (pickup.latitude, pickup.longitude)
        expected_destination = (destination.latitude, destination.longitude)

    client = app.test_client()
    response = client.post('/booking/location', data={
        'pickup_address': 'Gwarinpa', 'pickup_city': 'Abuja', 'pickup_state': 'Federal Capital Territory',
        'pickup_formatted_address': 'Gwarinpa, Abuja, FCT', 'pickup_latitude': '9.7', 'pickup_longitude': '7.7',
        'pickup_place_id': f'local-{pickup_id}', 'pickup_provider': 'local-reference',
        'pickup_original_input': 'gwar',
        'destination_address': 'Wuse', 'destination_city': 'Abuja', 'destination_state': 'Federal Capital Territory',
        'destination_formatted_address': 'Wuse, Abuja, FCT', 'destination_latitude': '8.3', 'destination_longitude': '6.8',
        'destination_place_id': f'local-{destination_id}', 'destination_provider': 'local-reference',
        'destination_original_input': 'wuse 2',
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        request_id = session['active_booking_request_id']
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert (booking.pickup_latitude, booking.pickup_longitude) == expected_pickup
        assert (booking.destination_latitude, booking.destination_longitude) == expected_destination
        assert booking.pickup_local_place.name == 'Gwarinpa'
        assert booking.destination_local_place.name == 'Wuse'
        assert booking.pickup_area_council == 'Abuja Municipal Area Council'
        assert booking.pickup_original_input == 'gwar'
        assert booking.destination_original_input == 'wuse 2'
        assert booking.distance_km > 0
        assert booking.distance_source == 'approximate-landmark-or-district'
        assert booking.distance_precision == 'approximate'
        assert booking.route_status == 'CALCULATED'


def test_unresolved_fct_address_is_saved_and_does_not_block_booking(monkeypatch):
    import moving_company.routes.booking_engine as booking_routes

    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    monkeypatch.setattr(booking_routes, 'geocode_address', lambda _address: None)
    monkeypatch.setattr(booking_routes, 'route_distance', lambda *args: None)

    response = app.test_client().post('/booking/location', data={
        'pickup_address': 'No. 14 Example Street, New Estate, Abuja',
        'destination_address': 'House 2, Sample Close, Gwagwalada',
        'submit': 'Continue',
    })

    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.one()
        assert booking.pickup_original_input == 'No. 14 Example Street, New Estate, Abuja'
        assert booking.destination_original_input == 'House 2, Sample Close, Gwagwalada'
        assert booking.pickup_city == 'Abuja'
        assert booking.pickup_state == 'Federal Capital Territory'
        assert booking.route_status == 'PENDING'


def test_admin_can_import_hierarchical_fct_locations_but_customers_cannot():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        admin_id = User.query.filter_by(email='admin@chigo.com').one().id
        customer = User(first_name='Local', last_name='Customer', email='local@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id

    payload = {'locations': [{
        'name': 'FCT Import Test Estate', 'place_type': 'estate',
        'area_council': 'Abuja Municipal Area Council', 'parent_name': 'Gwarinpa',
        'district': 'Gwarinpa', 'neighborhood': 'Gwarinpa', 'latitude': 9.12,
        'longitude': 7.42, 'aliases': ['Import Estate'], 'search_keywords': ['test estate'],
        'popularity_score': 10,
    }]}
    admin_client = app.test_client()
    with admin_client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    imported = admin_client.post('/admin/locations/import', json=payload)
    assert imported.status_code == 200
    assert imported.get_json()['imported'] == 1
    with app.app_context():
        place = LocalPlace.query.filter_by(normalized_name='fct import test estate').one()
        assert place.parent.name == 'Gwarinpa'

    customer_client = app.test_client()
    with customer_client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    assert customer_client.post('/admin/locations/import', json=payload).status_code == 403


def test_admin_location_observation_actions_are_protected_and_dispatch(monkeypatch):
    import moving_company.routes.admin as admin_routes
    from types import SimpleNamespace

    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    actions = []
    monkeypatch.setattr(
        admin_routes,
        'db',
        SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))),
    )
    monkeypatch.setattr(admin_routes, 'review_location_observation',
                        lambda observation_id, action, **kwargs: actions.append((observation_id, action, kwargs)) or {'status': action, 'location_id': 9})
    with app.app_context():
        admin_id = User.query.filter_by(email='admin@chigo.com').one().id
        customer = User(first_name='Review', last_name='Customer', email='review-customer@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    promoted = client.post('/admin/locations/observations/7/promote', json={'canonical_name': 'Example Estate', 'place_type': 'estate'})
    assert promoted.status_code == 200
    assert promoted.get_json()['location_id'] == 9
    assert actions[0][0:2] == (7, 'promote')
    assert actions[0][2]['canonical_name'] == 'Example Estate'

    customer_client = app.test_client()
    with customer_client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    denied = customer_client.post('/admin/locations/observations/7/approve', json={})
    assert denied.status_code == 403


def test_booking_location_page_uses_autocomplete_without_visible_map():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.get('/booking/location')
    assert response.status_code == 200
    html = response.get_data(as_text=True).lower()
    assert 'leaflet' not in html
    assert 'pickup_map' not in html
    assert 'destination_map' not in html
    assert 'search for an address' in html


def test_booking_back_button_uses_previous_valid_route():
    app = create_app(testing=True)
    anonymous_client = app.test_client()
    location_response = anonymous_client.get('/booking/location')
    assert location_response.status_code == 200
    assert b'href="/"' in location_response.data
    assert b'Previous: Home' in location_response.data

    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': '12 A Street', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': '14 B Street', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        })
        request_id = booking.booking_request_id

    draft_client = app.test_client()
    with draft_client.session_transaction() as session:
        session['active_booking_request_id'] = request_id
    property_response = draft_client.get(f'/booking/{request_id}/property')
    assert property_response.status_code == 200
    assert f'href="/booking/{request_id}/location"'.encode() in property_response.data
    assert b'Previous: Location' in property_response.data


def test_fresh_booking_entry_starts_with_blank_location_form():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.get('/booking/location')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="12 Yakubu Gowon Way"' not in html
    assert 'value="Plot 405 Constitution Avenue"' not in html


def test_csrf_errors_redirect_with_flash_instead_of_bad_request_page():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()

    response = client.post(
        '/auth/login',
        data={'email': 'user@example.com', 'password': 'password'},
        headers={'Referer': '/auth/login'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/auth/login')

    followup = client.get('/auth/login')
    html = followup.get_data(as_text=True)
    assert 'Your session expired for security. Please try again.' in html
    assert 'Bad Request' not in html


def test_stale_booking_session_redirects_to_fresh_residential_booking_start_after_csrf_expiry():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()

    with client.session_transaction() as session:
        session['active_booking_request_id'] = 'BR-FAKE-REQUEST'

    response = client.post(
        '/booking/BR-FAKE-REQUEST/property',
        data={'action': 'choose_property_type', 'property_type': 'Apartment'},
        headers={'Referer': '/booking/BR-FAKE-REQUEST/property'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/booking/start/residential')

    followup = client.get('/booking/start/residential', follow_redirects=False)
    assert followup.status_code == 302
    assert followup.headers['Location'].endswith('/booking/location/residential')


def test_login_page_renders_login_fields():
    app = create_app(testing=True)
    response = app.test_client().get('/auth/login')
    assert response.status_code == 200
    assert b'name="email"' in response.data
    assert b'name="password"' in response.data


def test_admin_auth_pages_and_account_creation_permissions():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        super_admin = User.query.filter_by(email='admin@chigo.com').one()
        customer = User(first_name='Casey', last_name='Customer', email='admin-customer@example.com', phone='')
        customer.set_password('customer-password')
        staff = User(first_name='Taylor', last_name='Staff', email='admin-staff@example.com', phone='', role='admin')
        staff.set_password('staff-password')
        db.session.add_all([customer, staff])
        db.session.commit()
        super_admin_id, customer_id, staff_id = super_admin.id, customer.id, staff.id

    client = app.test_client()
    login_page = client.get('/admin/login')
    assert login_page.status_code == 200
    assert b'Administrator sign in' in login_page.data
    assert b'href="/admin/login"' in client.get('/auth/login').data

    denied_customer_login = client.post('/admin/login', data={
        'email': 'admin-customer@example.com', 'password': 'customer-password',
    })
    assert denied_customer_login.status_code == 200
    assert b'Invalid admin email or password.' in denied_customer_login.data
    with client.session_transaction() as session:
        assert '_user_id' not in session

    assert client.get('/admin/create-account').headers['Location'].endswith('/admin/login')
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    assert client.get('/admin/create-account').status_code == 403

    with client.session_transaction() as session:
        session['_user_id'] = str(staff_id)
        session['_fresh'] = True
    assert client.get('/admin/create-account').status_code == 403

    with client.session_transaction() as session:
        session['_user_id'] = str(super_admin_id)
        session['_fresh'] = True
    account_page = client.get('/admin/create-account')
    assert account_page.status_code == 200
    assert b'Create an admin account' in account_page.data
    created = client.post('/admin/create-account', data={
        'full_name': 'New Operations Admin',
        'email': 'new-admin@example.com',
        'password': 'secure-admin-password',
    })
    assert created.status_code == 302
    assert created.location.endswith('/admin/dashboard')
    with app.app_context():
        new_admin = User.query.filter_by(email='new-admin@example.com').one()
        assert new_admin.role == 'operations_manager'
        assert new_admin.check_password('secure-admin-password')
        assert AuditLog.query.filter_by(action='admin.account_created', entity_id=str(new_admin.id)).count() == 1

    dispatcher_response = client.post('/admin/create-account', data={
        'full_name': 'Operations Dispatcher',
        'email': 'created-dispatcher@example.com',
        'password': 'secure-dispatcher-password',
        'role': 'dispatcher',
    })
    assert dispatcher_response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email='created-dispatcher@example.com').one().role == 'dispatcher'


def test_inventory_catalogue_admin_crud_and_restrictions():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        super_admin = User.query.filter_by(email='admin@chigo.com').one()
        customer = User(first_name='Casey', last_name='Customer', email='catalogue-customer@example.com', phone='')
        customer.set_password('customer-password')
        db.session.add(customer)
        db.session.commit()
        super_admin_id = super_admin.id
        customer_id = customer.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    assert client.get('/admin/inventory').status_code == 403

    with client.session_transaction() as session:
        session['_user_id'] = str(super_admin_id)
        session['_fresh'] = True
    catalogue_page = client.get('/admin/inventory')
    assert catalogue_page.status_code == 200
    assert b'Inventory catalogue' in catalogue_page.data

    create_response = client.post('/admin/inventory', data={
        'action': 'save', 'name': 'Moving Dollies', 'category': 'Children\'s Items',
        'estimated_volume_m3': '0.25', 'estimated_weight_kg': '8',
        'dimensions_length_cm': '20', 'dimensions_width_cm': '15', 'dimensions_height_cm': '10',
        'fragile': 'y', 'special_handling': 'y', 'active': 'y', 'sort_order': '1',
    })
    assert create_response.status_code == 302
    with app.app_context():
        item = InventoryItem.query.filter_by(name='Moving Dollies').one()
        assert item.active is True and item.fragile is True
        assert AuditLog.query.filter_by(action='inventory_item.created', entity_id=str(item.id)).count() == 1

    edit_response = client.post(f"/admin/inventory?item_id={item.id}", data={
        'action': 'save', 'item_id': str(item.id), 'name': 'Moving Dollies',
        'category': 'Miscellaneous', 'estimated_volume_m3': '0.3', 'estimated_weight_kg': '9',
        'active': '', 'sort_order': '2',
    })
    assert edit_response.status_code == 302
    with app.app_context():
        updated = InventoryItem.query.filter_by(id=item.id).one()
        assert updated.category == 'Miscellaneous' and updated.active is False

    delete_response = client.post('/admin/inventory', data={
        'action': 'delete', 'item_id': str(item.id),
    })
    assert delete_response.status_code == 302
    with app.app_context():
        assert InventoryItem.query.filter_by(id=item.id).count() == 0


def test_inventory_page_has_searchable_categories_and_selection_summary():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        })
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
        request_id = booking.booking_request_id

    client = app.test_client()
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id
    response = client.get(f'/booking/{request_id}/inventory')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="inventory-search"' in html
    assert 'id="inventory-category-filter"' in html
    assert 'inventory-category-toggle' in html
    assert 'Your selections' in html
    assert 'Save inventory and continue' in html


def test_admin_login_accepts_admin_role():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    response = client.post('/admin/login', data={
        'email': 'admin@chigo.com', 'password': 'admin123',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Operations overview' in response.data
    assert b'id="message-modal"' not in response.data


def test_dashboard_uses_live_booking_payment_and_resource_data():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    today = date.today()
    now = datetime.utcnow()
    with app.app_context():
        admin = User.query.filter_by(email='admin@chigo.com').one()
        customer = User(first_name='Rae', last_name='Customer', email='rae-metrics@example.com', phone='+2348000000099')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.flush()
        active_booking = Booking(
            booking_number='METRIC-TODAY', customer_id=customer.id, status='In Progress',
            workflow_state='IN_PROGRESS', quote_status='Draft', payment_status='Paid',
            created_at=now, move_date=today, preferred_time='Morning', estimated_total=250,
            pickup_address='1 A Street', pickup_city='Abuja', pickup_state='FCT',
            destination_address='2 B Street', destination_city='Wuse', destination_state='FCT',
        )
        upcoming_booking = Booking(
            booking_number='METRIC-UPCOMING', customer_id=customer.id, status='Confirmed',
            workflow_state='CONFIRMED', quote_status='Ready', payment_status='Pending',
            created_at=now - timedelta(days=2), move_date=today + timedelta(days=2),
            estimated_total=500, pickup_address='3 C Street', pickup_city='Abuja', pickup_state='FCT',
            destination_address='4 D Street', destination_city='Gwarinpa', destination_state='FCT',
        )
        db.session.add_all([
            active_booking, upcoming_booking,
            Truck(registration_number='METRIC-TRUCK', vehicle_type='Canter', status='Available'),
            Mover(name='Metric Mover', status='Available'),
        ])
        db.session.flush()
        db.session.add(Payment(booking_id=active_booking.id, amount=100, status='Successful', created_at=now))
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    dashboard = client.get('/admin/dashboard')
    assert dashboard.status_code == 200
    html = dashboard.get_data(as_text=True)
    assert 'METRIC-TODAY' in html
    assert 'METRIC-UPCOMING' in html
    assert 'Today&#39;s Bookings' in html
    assert 'Available Vehicles' in html
    assert '₦100' in html
    assert '₦650' in html
    assert 'href="/admin/bookings?created=today"' in html
    assert 'href="/admin/bookings?move=upcoming"' in html

    active_results = client.get('/admin/bookings?status=In%20Progress')
    assert b'METRIC-TODAY' in active_results.data
    assert b'METRIC-UPCOMING' not in active_results.data


def test_admin_can_add_truck_partner_and_mover():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        admin_id = User.query.filter_by(email='admin@chigo.com').one().id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True

    partner_page = client.get('/admin/trucks')
    assert partner_page.status_code == 200
    assert b'Add truck partner' in partner_page.data
    partner_response = client.post('/admin/trucks', data={
        'name': 'Amina Yusuf', 'company': 'Capital Haulage',
        'phone': '+2348000000701', 'email': 'fleet@example.com',
        'vehicle_type': 'Canter', 'capacity': 'Medium', 'rate': '42500',
        'operating_areas': 'Abuja and FCT',
    })
    assert partner_response.status_code == 302

    mover_page = client.get('/admin/movers')
    assert mover_page.status_code == 200
    assert b'Add mover' in mover_page.data
    mover_response = client.post('/admin/movers', data={
        'name': 'Chika Okafor', 'phone': '+2348000000702',
        'skills': 'Packing, loading', 'experience': '4 years',
    })
    assert mover_response.status_code == 302

    with app.app_context():
        partner = TruckPartner.query.filter_by(company='Capital Haulage').one()
        mover = Mover.query.filter_by(name='Chika Okafor').one()
        assert partner.rate == 42500
        assert partner.status == 'Available'
        assert mover.status == 'Available'
        assert AuditLog.query.filter_by(action='truck_partner.created', entity_id=str(partner.id)).count() == 1
        assert AuditLog.query.filter_by(action='mover.created', entity_id=str(mover.id)).count() == 1


def test_admin_can_view_and_edit_quote_engine_service_prices():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        admin_id = User.query.filter_by(email='admin@chigo.com').one().id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    page = client.get('/admin/pricing')
    assert page.status_code == 200
    assert b'Partial packing' in page.data
    assert b'value="18000"' in page.data
    values = {key: '100' for key in (
        'base_moving_fee', 'price_per_km', 'property_surcharge', 'floor_surcharge',
        'packing_partial', 'packing_full', 'cleaning_move_out', 'cleaning_move_in',
        'cleaning_both', 'unpacking', 'service_assembly_yes', 'service_disassembly_yes',
        'storage_week', 'special_handling', 'weekend_surcharge', 'holiday_surcharge',
        'additional_mover_fee', 'base_mover_hourly_fee', 'access_difficulty_surcharge',
        'outside_service_area_surcharge', 'tax_rate', 'deposit_percentage',
        'volume_safety_factor',
    )}
    values['volume_safety_factor'] = '1.15'
    values['packing_partial'] = '22000'
    response = client.post('/admin/pricing', data=values)
    assert response.status_code == 302
    with app.app_context():
        assert ServicePricing.query.filter_by(key='packing_partial').one().value == 22000
        audit = AuditLog.query.filter_by(action='pricing.updated').one()
        assert 'packing_partial' in audit.before_value
        assert '22000' in audit.after_value


def test_admin_ride_statuses_follow_sequence_and_record_history():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        admin = User.query.filter_by(email='admin@chigo.com').one()
        customer = User(first_name='Ride', last_name='Customer', email='ride-customer@example.com', phone='')
        customer.set_password('password123')
        truck = Truck(registration_number='RIDE-001', vehicle_type='Canter', status='Assigned')
        db.session.add_all([customer, truck])
        db.session.flush()
        booking = Booking(
            booking_number='RIDE-STATUS-001', customer_id=customer.id,
            status='Assigned', payment_status='Paid', move_date=date.today(),
            pickup_address='1 Ride Road', pickup_city='Abuja', pickup_state='FCT',
            destination_address='2 Ride Close', destination_city='Wuse', destination_state='FCT',
        )
        db.session.add(booking)
        db.session.flush()
        db.session.add(BookingAssignment(booking_id=booking.id, truck_id=truck.id, driver_name='Driver One'))
        db.session.commit()
        admin_id, customer_id, booking_id = admin.id, customer.id, booking.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    detail = client.get(f'/admin/bookings/{booking_id}')
    assert detail.status_code == 200
    assert b'Ride status' in detail.data
    assert b'En Route' in detail.data

    assert client.post(f'/admin/bookings/{booking_id}/status', data={'status': 'En Route'}).status_code == 302
    assert client.post(f'/admin/bookings/{booking_id}/status', data={'status': 'Loading'}).status_code == 302
    with app.app_context():
        assert Booking.query.get(booking_id).status == 'En Route'

    for status in ('Arrived', 'Loading', 'In Transit', 'Unloading', 'Completed'):
        response = client.post(f'/admin/bookings/{booking_id}/status', data={'status': status})
        assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.status == 'Completed'
        assert booking.workflow_state == 'COMPLETED'
        assert BookingEvent.query.filter_by(booking_id=booking_id, action='ride.status_changed').count() == 6
        assert AuditLog.query.filter_by(action='ride.status_changed', entity_id=str(booking_id)).count() == 6

    customer_client = app.test_client()
    with customer_client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    customer_detail = customer_client.get(f'/customer/bookings/{booking_id}')
    assert customer_detail.status_code == 200
    assert b'Move timeline' in customer_detail.data
    assert b'En Route' in customer_detail.data
    assert b'In Transit' in customer_detail.data
    assert b'Completed' in customer_detail.data
    assert b'Current status' in customer_detail.data


def test_dispatcher_role_is_authorized_only_for_assigned_modules():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        dispatcher = User(
            first_name='Devon', last_name='Dispatch', email='dispatcher@example.com',
            phone='', role='dispatcher',
        )
        dispatcher.set_password('dispatcher-password')
        db.session.add(dispatcher)
        db.session.commit()

    client = app.test_client()
    login = client.post('/admin/login', data={
        'email': 'dispatcher@example.com', 'password': 'dispatcher-password',
    })
    assert login.status_code == 302
    assert client.get('/admin/bookings').status_code == 200
    assert client.get('/admin/trucks').status_code == 200
    assert client.get('/admin/pricing').status_code == 403
    assert client.get('/admin/create-account').status_code == 403


def test_create_super_admin_cli_bootstraps_first_super_admin():
    app = create_app(testing=True)
    with app.app_context():
        User.query.filter_by(role='super_admin').delete()
        db.session.commit()

    result = app.test_cli_runner().invoke(
        args=['create-super-admin'],
        input='Morgan Admin\nmorgan-admin@example.com\nsecure-password\nsecure-password\n',
    )
    assert result.exit_code == 0, result.output
    assert 'Super-admin account created' in result.output
    with app.app_context():
        user = User.query.filter_by(email='morgan-admin@example.com').one()
        assert user.role == 'super_admin'
        assert user.check_password('secure-password')


def test_create_super_admin_cli_refuses_when_super_admin_exists():
    app = create_app(testing=True)
    result = app.test_cli_runner().invoke(args=['create-super-admin'])
    assert result.exit_code != 0
    assert 'A super-admin account already exists.' in result.output


def test_registration_uses_full_name_without_optional_details():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    response = client.post('/auth/register', data={
        'full_name': 'Avery Johnson',
        'email': 'avery@example.com',
        'password': 'secure-password',
    })
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email='avery@example.com').one()
        assert user.first_name == 'Avery'
        assert user.last_name == 'Johnson'
        assert user.phone == ''
    welcome_email = app.extensions['outbox'][0]
    assert welcome_email['to'] == 'avery@example.com'
    assert welcome_email['subject'] == 'Welcome to Chigo Relocations'
    assert 'Welcome, Avery.' in welcome_email['html']
    assert 'Open your account' in welcome_email['html']
    assert 'Open your account' in welcome_email['text']
    page = client.get('/auth/register')
    assert b'Add optional details' not in page.data
    assert b'name="full_name"' in page.data


def test_signup_and_login_complete_without_password_rule_surprises():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    short_password_response = client.post('/auth/register', data={
        'full_name': 'Jamie Lee',
        'email': 'jamie@example.com',
        'password': 'seven77',
    })
    assert short_password_response.status_code == 200

    signup_response = client.post('/auth/register', data={
        'full_name': 'Jamie Lee',
        'email': 'JAMIE@example.com',
        'password': 'long-enough-password',
    })
    assert signup_response.status_code == 302
    assert client.get('/customer/dashboard').status_code == 200
    assert client.get('/auth/logout').status_code == 302

    login_response = client.post('/auth/login', data={
        'email': '  JAMIE@example.com  ',
        'password': 'long-enough-password',
    })
    assert login_response.status_code == 302
    assert client.get('/customer/dashboard').status_code == 200


def _mock_google_oauth(monkeypatch, claims=None, error=None):
    from authlib.integrations.base_client.errors import OAuthError
    from moving_company.routes import auth as auth_routes

    class MockGoogle:
        redirect_arguments = None

        def authorize_redirect(self, **kwargs):
            self.redirect_arguments = kwargs
            from flask import redirect
            return redirect('https://accounts.google.com/mock-authorize')

        def authorize_access_token(self, **kwargs):
            assert not kwargs
            if error:
                raise OAuthError(error=error)
            return {'userinfo': claims or {}}

    fake = MockGoogle()
    monkeypatch.setattr(auth_routes.google_oauth, 'google', fake, raising=False)
    return fake


def _google_callback_session(client, **values):
    defaults = {
        'google_flow': 'login',
        'google_next': None,
    }
    defaults.update(values)
    with client.session_transaction() as session:
        session.update(defaults)


def test_google_oidc_start_uses_minimal_scope_pkce_and_safe_next(monkeypatch):
    app = create_app(testing=True)
    app.config.update(GOOGLE_CLIENT_ID='client-id', GOOGLE_CLIENT_SECRET='client-secret')
    fake = _mock_google_oauth(monkeypatch)
    response = app.test_client().get('/auth/google?next=https://attacker.example/')

    assert response.status_code == 302
    assert response.location == 'https://accounts.google.com/mock-authorize'
    assert fake.redirect_arguments['scope'] == 'openid email profile'
    assert fake.redirect_arguments['nonce']


def test_google_first_login_creates_customer_and_rotates_session(monkeypatch):
    from moving_company.models import OAuthIdentity

    claims = {
        'sub': 'google-sub-100', 'email': 'new.google@example.com',
        'email_verified': True, 'given_name': 'Taylor', 'family_name': 'Adebayo',
    }
    _mock_google_oauth(monkeypatch, claims)
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    _google_callback_session(client, google_next='/customer/bookings')
    previous_cookie = client.get_cookie(app.config['SESSION_COOKIE_NAME']).value

    response = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert response.status_code == 302
    assert response.location == '/customer/bookings'
    assert client.get_cookie(app.config['SESSION_COOKIE_NAME']).value != previous_cookie
    with app.app_context():
        user = User.query.filter_by(email='new.google@example.com').one()
        identity = OAuthIdentity.query.filter_by(provider='google', subject='google-sub-100').one()
        assert identity.user_id == user.id
        assert user.role == 'customer'
        assert user.check_password('not-the-google-password') is False
    with client.session_transaction() as session:
        assert 'google_flow' not in session


def test_google_login_for_existing_subject_preserves_role(monkeypatch):
    from moving_company.models import OAuthIdentity

    _mock_google_oauth(monkeypatch, {
        'sub': 'google-staff-sub', 'email': 'dispatcher@example.com',
        'email_verified': True,
    })
    app = create_app(testing=True)
    with app.app_context():
        user = User(first_name='Dispatch', last_name='User', email='dispatcher@example.com', phone='', role='dispatcher')
        user.set_password('existing-password')
        db.session.add(user)
        db.session.flush()
        db.session.add(OAuthIdentity(user_id=user.id, provider='google', subject='google-staff-sub', email=user.email, email_verified=True))
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    _google_callback_session(client)
    response = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert response.status_code == 302
    assert response.location == '/admin/dashboard'
    with app.app_context():
        assert db.session.get(User, user_id).role == 'dispatcher'


def test_google_identity_is_unique_and_logout_clears_google_session(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from moving_company.models import OAuthIdentity

    _mock_google_oauth(monkeypatch, {
        'sub': 'logout-google-sub', 'email': 'logout.google@example.com', 'email_verified': True,
    })
    app = create_app(testing=True)
    client = app.test_client()
    _google_callback_session(client)
    assert client.get('/auth/google/callback?state=mock-state&code=mock-code').status_code == 302
    assert client.get('/customer/dashboard').status_code == 200

    with app.app_context():
        user = User.query.filter_by(email='logout.google@example.com').one()
        db.session.add(OAuthIdentity(
            user_id=user.id, provider='google', subject='logout-google-sub',
            email=user.email, email_verified=True,
        ))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    assert client.get('/auth/logout').status_code == 302
    assert client.get('/customer/dashboard').status_code == 302


def test_google_email_collision_requires_existing_account_login(monkeypatch):
    from moving_company.models import OAuthIdentity

    _mock_google_oauth(monkeypatch, {
        'sub': 'new-google-sub', 'email': 'password.user@example.com', 'email_verified': True,
    })
    app = create_app(testing=True)
    with app.app_context():
        user = User(first_name='Password', last_name='User', email='password.user@example.com', phone='', role='customer')
        user.set_password('keep-this-password')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    client = app.test_client()
    _google_callback_session(client)

    response = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert response.status_code == 302
    assert response.location == '/auth/login'
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.check_password('keep-this-password')
        assert OAuthIdentity.query.filter_by(user_id=user_id).count() == 0


def test_authenticated_user_can_explicitly_link_google(monkeypatch):
    from moving_company.models import OAuthIdentity

    fake = _mock_google_oauth(monkeypatch, {
        'sub': 'explicit-link-sub', 'email': 'linked@example.com', 'email_verified': True,
    })
    app = create_app(testing=True)
    app.config.update(GOOGLE_CLIENT_ID='client-id', GOOGLE_CLIENT_SECRET='client-secret')
    with app.app_context():
        user = User(first_name='Linked', last_name='Customer', email='linked@example.com', phone='', role='customer')
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True

    start = client.get('/auth/google/link')
    assert start.status_code == 302
    assert fake.redirect_arguments['scope'] == 'openid email profile'
    _google_callback_session(client, google_flow='link', google_link_user_id=str(user_id))
    callback = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert callback.status_code == 302
    assert callback.location == '/customer/profile'
    with app.app_context():
        identity = OAuthIdentity.query.filter_by(provider='google', subject='explicit-link-sub').one()
        assert identity.user_id == user_id


@pytest.mark.parametrize('oauth_error', ['access_denied', 'invalid_grant', 'mismatching_state', 'invalid_nonce', 'invalid_signature', 'invalid_audience', 'invalid_issuer', 'expired_token'])
def test_google_callback_handles_oauth_validation_errors(monkeypatch, oauth_error):
    _mock_google_oauth(monkeypatch, error=oauth_error)
    app = create_app(testing=True)
    client = app.test_client()
    _google_callback_session(client)

    response = client.get('/auth/google/callback?state=invalid&code=mock-code')

    assert response.status_code == 302
    assert response.location == '/auth/login'


def test_google_callback_rejects_unverified_email(monkeypatch):
    _mock_google_oauth(monkeypatch, {
        'sub': 'unverified-sub', 'email': 'unverified@example.com', 'email_verified': False,
    })
    app = create_app(testing=True)
    client = app.test_client()
    _google_callback_session(client)

    response = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert response.location == '/auth/login'
    with app.app_context():
        assert User.query.filter_by(email='unverified@example.com').first() is None


def test_google_login_preserves_booking_draft_and_rejects_open_redirect(monkeypatch):
    claims = {
        'sub': 'booking-google-sub', 'email': 'booking.google@example.com',
        'email_verified': True, 'name': 'Booking Customer',
    }
    _mock_google_oauth(monkeypatch, claims)
    app = create_app(testing=True)
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': '12 A Street', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': '14 B Street', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        })
        booking.workflow_state = 'PROPERTY_COMPLETED'
        db.session.commit()
        request_id = booking.booking_request_id
    client = app.test_client()
    _google_callback_session(client, google_next='//evil.example/steal')
    with client.session_transaction() as session:
        session['active_booking_request_id'] = request_id

    response = client.get('/auth/google/callback?state=mock-state&code=mock-code')

    assert response.status_code == 302
    assert f'/booking/{request_id}/inventory' in response.location
    assert 'evil.example' not in response.location
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.customer_id is not None
        assert booking.pickup_address == '12 A Street'


def test_branded_email_uses_implicit_ssl_transport(monkeypatch):
    from moving_company.services import transactional_email_service

    calls = {}

    class SMTPConnection:
        def __init__(self, server, port, **kwargs):
            calls['connection'] = (server, port, kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, username, password):
            calls['login'] = (username, password)

        def send_message(self, message):
            calls['message'] = message

    monkeypatch.setattr(transactional_email_service.smtplib, 'SMTP_SSL', SMTPConnection)
    app = create_app(testing=True)
    app.testing = False
    app.config.update(
        MAIL_SERVER='chigomove.online',
        MAIL_PORT=465,
        MAIL_USE_SSL=True,
        MAIL_USE_TLS=False,
        MAIL_USERNAME='hello@chigomove.online',
        MAIL_PASSWORD='test-password',
    )

    with app.app_context():
        transactional_email_service.send_branded_email(
            recipient='customer@example.com',
            subject='Test message',
            preheader='A preview',
            heading='Hello',
            paragraphs=['Your account is ready.'],
            action_label='Open account',
            action_url='https://www.chigomove.online/customer/dashboard',
        )

    assert calls['connection'][0:2] == ('chigomove.online', 465)
    assert 'context' in calls['connection'][2]
    assert calls['login'] == ('hello@chigomove.online', 'test-password')
    assert calls['message'].get_content_subtype() == 'alternative'
    assert calls['message'].get_body(preferencelist=('html',)) is not None


def test_customer_dashboard_shows_booking_details_and_unique_numbers():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        customer = User(first_name='Casey', last_name='Customer', email='casey@example.com', phone='')
        customer.set_password('password123')
        db.session.add(customer)
        db.session.flush()
        customer_id = customer.id

        first_booking = create_booking_request(
            customer_id=customer.id,
            location={
                'pickup_address': '1 A Street', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
                'destination_address': '2 B Street', 'destination_city': 'Kubwa', 'destination_state': 'FCT',
            },
        )
        second_booking = create_booking_request(
            customer_id=customer.id,
            location={
                'pickup_address': '7 Palm Road', 'pickup_city': 'Lekki', 'pickup_state': 'Lagos',
                'destination_address': '11 Harbour Lane', 'destination_city': 'Ikoyi', 'destination_state': 'Lagos',
            },
        )
        db.session.flush()
        assert first_booking.booking_number.startswith('BR-')
        assert second_booking.booking_number.startswith('BR-')
        assert first_booking.booking_number != second_booking.booking_number

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True

    response = client.get('/customer/dashboard')
    assert response.status_code == 200
    assert b'/static/images/chigo-logo.svg' in response.data
    assert first_booking.booking_number.encode() in response.data
    assert second_booking.booking_number.encode() in response.data
    assert f'href="/customer/bookings/{first_booking.id}"'.encode() in response.data
    assert f'href="/customer/bookings/{second_booking.id}"'.encode() in response.data


def test_dashboard_pages_have_role_specific_navigation():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        customer = User(first_name='Casey', last_name='Customer', email='casey@example.com', phone='')
        customer.set_password('password123')
        admin = User(first_name='Alex', last_name='Admin', email='alex@example.com', phone='', role='admin')
        admin.set_password('password123')
        db.session.add_all([customer, admin])
        db.session.flush()
        booking = Booking(
            booking_number='MOV-009001', customer_id=customer.id,
            pickup_address='1 A Street', pickup_city='Abuja', pickup_state='FCT',
            destination_address='2 B Street', destination_city='Kubwa', destination_state='FCT',
        )
        db.session.add_all([
            booking,
            Truck(registration_number='TST-001', vehicle_type='Canter', status='Available'),
            TruckPartner(name='Partner One', status='Available'),
            Mover(name='Mover One', status='Available'),
            Cleaner(name='Cleaner One', status='Available'),
        ])
        db.session.commit()
        customer_id, admin_id, booking_id = customer.id, admin.id, booking.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    for path in (
        '/customer/dashboard',
        '/customer/bookings',
        '/customer/profile',
        f'/customer/bookings/{booking_id}',
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b'/static/images/chigo-logo.svg' in response.data
        assert b'href="/customer/bookings"' in response.data
        assert b'href="/customer/profile"' in response.data
        assert b'uppercase }}' not in response.data
        assert b'href="/booking/start"' in response.data
    assert b'Operations' not in client.get('/customer/dashboard').data
    payment_history = client.get(f'/booking/payment/{booking_id}')
    assert payment_history.status_code == 200
    assert b'Payment history' in payment_history.data

    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    for path in (
        '/admin/dashboard', '/admin/bookings', '/admin/pricing',
        '/admin/trucks', '/admin/movers', '/admin/cleaners',
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b'/static/images/chigo-logo.svg' in response.data
        assert b'Operations' in response.data
        assert b'Pricing' in response.data
        assert b'Movers' in response.data
        assert b'href="/admin/pricing"' in response.data
        assert b'href="/admin/trucks"' in response.data

    admin_client = app.test_client()
    login_response = admin_client.post('/auth/login', data={
        'email': 'alex@example.com', 'password': 'password123',
    })
    assert login_response.status_code == 302
    assert login_response.location.endswith('/admin/dashboard')


def test_password_reset_request_and_token_flow():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        user = User(first_name='Reset', last_name='User', email='reset@example.com', phone='+2348000000001')
        user.set_password('old-password')
        db.session.add(user)
        db.session.commit()

    client = app.test_client()
    response = client.post('/auth/forgot-password', data={'email': 'reset@example.com'})
    assert response.status_code == 302
    reset_url = app.extensions['outbox'][0]['reset_url']
    reset_email = app.extensions['outbox'][0]
    assert reset_email['subject'] == 'Reset your Chigo Relocations password'
    assert 'Reset your password' in reset_email['html']
    assert reset_url in reset_email['html']
    assert reset_url in reset_email['text']
    reset_path = urlsplit(reset_url).path
    assert client.get(reset_path).status_code == 200
    response = client.post(reset_path, data={
        'password': 'new-password',
        'confirm_password': 'new-password',
    })
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email='reset@example.com').one()
        assert user.check_password('new-password')
    assert client.get(reset_path).status_code == 302

    response = client.post('/auth/forgot-password', data={'email': 'unknown@example.com'})
    assert response.status_code == 302
    assert len(app.extensions['outbox']) == 1


def test_terms_and_privacy_pages_render_and_are_linked():
    app = create_app(testing=True)
    client = app.test_client()
    for path, heading in (
        ('/terms', b'Terms of Service'),
        ('/terms-of-service', b'Terms of Service'),
        ('/privacy', b'Privacy Policy'),
        ('/privacy-policy', b'Privacy Policy'),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert heading in response.data

    registration = client.get('/auth/register')
    assert registration.status_code == 200
    assert b'href="/terms-of-service"' in registration.data
    assert b'href="/privacy-policy"' in registration.data


def test_public_services_page_renders():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.get('/services')
    assert response.status_code == 200
    assert b'Our Services' in response.data
    for path, title in (
        ('/services/moving', b'Moving'),
        ('/services/packing', b'Packing'),
        ('/services/cleaning', b'Cleaning'),
        ('/services/unpacking', b'Unpacking'),
        ('/services/storage', b'Storage'),
    ):
        detail_response = client.get(path)
        assert detail_response.status_code == 200
        assert title in detail_response.data


def test_public_contact_page_renders():
    app = create_app(testing=True)
    response = app.test_client().get('/contact')
    assert response.status_code == 200
    assert b'hello@chigo.com' in response.data


def test_how_it_works_page_renders():
    app = create_app(testing=True)
    response = app.test_client().get('/how-it-works')
    assert response.status_code == 200
    assert b'How it works' in response.data


def test_public_pricing_about_and_faq_pages_render():
    app = create_app(testing=True)
    client = app.test_client()
    for path, heading in (
        ('/pricing', b'Pricing'),
        ('/about', b'Redefining Relocation With'),
        ('/faq', b'Frequently asked questions'),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert heading in response.data


def test_configurable_pricing_workflow():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        user = User(
            first_name='Jane',
            last_name='Doe',
            email='jane@example.com',
            phone='+2348001112222',
            role='customer',
        )
        user.set_password('secret123')
        db.session.add(user)
        db.session.commit()

        pricing = ServicePricing.query.filter_by(key='base_moving_fee').first()
        pricing.value = 30000
        db.session.commit()
        assert ServicePricing.query.filter_by(key='base_moving_fee').first().value == 30000


def test_location_search_and_coordinate_persistence_workflow(monkeypatch):
    from moving_company.services import location_service
    from moving_company.routes import booking_engine

    location_service.clear_location_search_cache()
    monkeypatch.setattr(location_service, '_osrm_request', lambda path, params=None, timeout=5: {'routes': [{'distance': 18000, 'duration': 1800}]})
    monkeypatch.setattr(booking_engine, 'geocode_address', lambda address: {
        'provider': 'nominatim', 'provider_place_id': f'verified:{address}',
        'formatted_address': address, 'latitude': 9.0919 if 'Maitama' in address else 9.0741,
        'longitude': 7.4860 if 'Maitama' in address else 7.4992,
        'country': 'Nigeria', 'state': 'Federal Capital Territory', 'city': 'Abuja',
        'area': 'Maitama' if 'Maitama' in address else 'Wuse',
        'district': '', 'area_council': 'Abuja Municipal Area Council',
        'neighborhood': '', 'location_type': 'address',
    })
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    search_response = client.get('/api/locations/autocomplete?q=Maitama%20Abuja')
    assert search_response.status_code == 200
    payload = search_response.get_json()
    assert payload['success'] is True
    assert payload['results']
    first = payload['results'][0]
    assert 'formatted_address' in first
    assert 'latitude' in first and 'longitude' in first

    response = client.post('/booking/location', data={
        'pickup_address': '1 Maitama Close',
        'pickup_city': 'Abuja',
        'pickup_state': 'FCT',
        'pickup_formatted_address': '1 Maitama Close, Maitama, Abuja, Federal Capital Territory, Nigeria',
        'pickup_latitude': '9.0919',
        'pickup_longitude': '7.4860',
        'destination_address': '12 Wuse Market Road',
        'destination_city': 'Abuja',
        'destination_state': 'FCT',
        'destination_formatted_address': '12 Wuse Market Road, Wuse, Abuja, Federal Capital Territory, Nigeria',
        'destination_latitude': '9.0741',
        'destination_longitude': '7.4992',
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        request_id = session['active_booking_request_id']
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.pickup_latitude == 9.0919
        assert booking.destination_longitude == 7.4992
        assert booking.distance_km == 18
        assert booking.route_status == 'CALCULATED'
        assert booking.pickup_original_input == '1 Maitama Close'
        assert booking.pickup_normalized_location == 'Maitama, Abuja Municipal Area Council'


def test_osrm_route_endpoint_returns_distance_and_duration():
    from moving_company.services import location_service

    def fake_nominatim(path, params):
        if path == '/search':
            return [{'display_name': 'Maitama, Abuja', 'lat': '9.0919', 'lon': '7.4860', 'address': {'city': 'Abuja', 'state': 'Federal Capital Territory', 'country': 'Nigeria'}, 'place_id': 101}]
        return []

    def fake_osrm(path, params=None):
        assert path.startswith('/route/v1/driving/')
        return {'routes': [{'distance': 18000, 'duration': 1800}]}

    location_service._nominatim_request = fake_nominatim
    location_service._osrm_request = fake_osrm

    app = create_app(testing=True)
    client = app.test_client()
    response = client.post('/api/locations/route', json={
        'pickup': {'latitude': 9.0919, 'longitude': 7.4860},
        'destination': {'latitude': 9.0741, 'longitude': 7.4992},
    })
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['route']['distance_km'] == 18.0
    assert payload['route']['duration_minutes'] == 30.0


def test_route_endpoint_returns_retryable_failure_when_provider_unavailable(monkeypatch):
    from moving_company.services import location_service

    app = create_app(testing=True)
    monkeypatch.setattr(location_service, '_osrm_request', lambda *args, **kwargs: None)
    location_service._ROUTE_CACHE.clear()
    response = app.test_client().post('/api/locations/route', json={
        'pickup': {'latitude': 9.09, 'longitude': 7.48},
        'destination': {'latitude': 9.07, 'longitude': 7.49},
    })
    assert response.status_code == 503
    assert response.get_json()['success'] is False


def test_assignment_conflict_and_payment_review_flow():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        user = User(
            first_name='Sam',
            last_name='Smith',
            email='sam@example.com',
            phone='+2348001113333',
            role='customer',
        )
        user.set_password('secret123')
        db.session.add(user)
        db.session.commit()

        booking = Booking(
            booking_number='MOV-000101',
            customer_id=user.id,
            status='Assigned',
            move_date=date(2026, 10, 20),
            pickup_city='Abuja',
            pickup_state='FCT',
            destination_city='Gwarinpa',
            destination_state='FCT',
            pickup_address='Test 1',
            destination_address='Test 2',
        )
        db.session.add(booking)
        db.session.flush()

        truck = Truck(
            registration_number='ABC-123',
            vehicle_type='Canter',
            capacity='Medium',
            status='Available',
            rate=70000,
        )
        db.session.add(truck)
        db.session.flush()

        other_booking = Booking(
            booking_number='MOV-000102',
            customer_id=user.id,
            move_date=date(2026, 10, 20),
            pickup_address='Test 3',
            pickup_city='Abuja',
            pickup_state='FCT',
            destination_address='Test 4',
            destination_city='Gwarinpa',
            destination_state='FCT',
        )
        db.session.add(other_booking)
        db.session.flush()
        db.session.add(BookingAssignment(booking_id=other_booking.id, truck_id=truck.id, driver_name='Driver A'))
        db.session.commit()

        conflict = check_assignment_conflict(booking.id, truck.id, '2026-10-20')
        assert conflict is True

        review = Review(booking_id=booking.id, user_id=user.id, rating=5, comment='Great service')
        db.session.add(review)
        db.session.commit()
        assert Review.query.filter_by(booking_id=booking.id).count() == 1

        incident = IncidentReport(
            booking_id=booking.id,
            item='Sofa',
            incident_type='Damaged item',
            description='Minor tear on corner',
            reported_by='Customer',
            status='Open',
        )
        db.session.add(incident)
        db.session.commit()
        assert IncidentReport.query.filter_by(booking_id=booking.id).count() == 1


def test_operational_routes_persist_and_enforce_workflows():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        customer = User(first_name='Taylor', last_name='Jones', email='taylor@example.com', phone='+2348001114444')
        admin = User(first_name='Ops', last_name='Admin', email='ops@example.com', phone='+2348001115555', role='admin')
        customer.set_password('secret123')
        admin.set_password('secret123')
        db.session.add_all([customer, admin])
        db.session.commit()
        booking = Booking(
            booking_number='MOV-000201',
            customer_id=customer.id,
            status='Completed',
            move_date=date(2026, 11, 2),
            pickup_address='12 Example Road',
            pickup_city='Abuja',
            pickup_state='FCT',
            destination_address='4 Sample Close',
            destination_city='Gwarinpa',
            destination_state='FCT',
            estimated_total=100000,
            deposit_amount=30000,
            balance_amount=70000,
        )
        truck = Truck(registration_number='OPS-201', vehicle_type='Canter', status='Available')
        db.session.add_all([booking, truck])
        db.session.commit()
        customer_id, admin_id, booking_id, truck_id = customer.id, admin.id, booking.id, truck.id
        private_booking = Booking(
            booking_number='MOV-000202',
            customer_id=admin.id,
            pickup_address='Private 1',
            pickup_city='Abuja',
            pickup_state='FCT',
            destination_address='Private 2',
            destination_city='Kubwa',
            destination_state='FCT',
        )
        db.session.add(private_booking)
        db.session.commit()
        private_booking_id = private_booking.id

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True
    assert client.get('/admin/pricing').status_code == 403
    assert client.get(f'/booking/payment/{private_booking_id}').status_code == 404
    assert client.get(f'/customer/bookings/{booking_id}').status_code == 200
    assert client.get(f'/booking/payment/{booking_id}').status_code == 200
    assert client.post(f'/booking/payment/{booking_id}', data={'action': 'initiate'}).status_code == 302
    with app.app_context():
        assert Payment.query.filter_by(booking_id=booking_id).count() == 0
        assert Booking.query.get(booking_id).payment_status == 'Pending'
    assert client.post(f'/customer/bookings/{booking_id}/review', data={
        'rating': '5', 'comment': 'Careful, on-time team.',
    }).status_code == 302
    assert client.get(f'/customer/bookings/{booking_id}/review').status_code == 302
    assert client.get(f'/customer/bookings/{booking_id}/incidents').status_code == 200
    assert client.post(f'/customer/bookings/{booking_id}/incidents', data={
        'incident_type': 'Damaged item', 'item': 'Lamp', 'description': 'Shade was cracked.',
    }).status_code == 302

    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    assert client.get('/admin/pricing').status_code == 200
    assert client.get(f'/admin/bookings/{booking_id}').status_code == 200
    pricing_values = {key: '100' for key in (
        'base_moving_fee', 'price_per_km', 'property_surcharge', 'floor_surcharge',
        'packing_partial', 'packing_full', 'cleaning_move_out', 'cleaning_move_in',
        'unpacking', 'storage_week', 'special_handling', 'weekend_surcharge', 'holiday_surcharge',
        'cleaning_both', 'service_assembly_yes', 'service_disassembly_yes',
            'additional_mover_fee', 'base_mover_hourly_fee', 'access_difficulty_surcharge',
            'outside_service_area_surcharge', 'tax_rate', 'deposit_percentage', 'volume_safety_factor',
    )}
    pricing_values['volume_safety_factor'] = '1.15'
    assert client.post('/admin/pricing', data=pricing_values).status_code == 302
    assert client.post(f'/admin/bookings/{booking_id}/assign', data={
        'truck_id': str(truck_id), 'driver_name': 'Driver One', 'movers': 'Mover One, Mover Two',
    }).status_code == 302
    with app.app_context():
        assert ServicePricing.query.filter_by(key='base_moving_fee').one().value == 100
        assert Booking.query.get(booking_id).status == 'Assigned'
        assert AuditLog.query.filter(AuditLog.action.in_([
            'booking.assignment_updated', 'pricing.updated',
        ])).count() == 2
        incident = IncidentReport.query.filter_by(booking_id=booking_id).one()
        assert incident.status == 'Open'
        incident_id = incident.id
    assert client.post(f'/admin/incidents/{incident_id}', data={
        'status': 'Resolved', 'resolution': 'Reimbursed replacement cost.',
    }).status_code == 302
    assert client.get(f'/admin/bookings/{booking_id}').status_code == 200
