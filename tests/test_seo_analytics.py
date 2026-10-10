from xml.etree import ElementTree

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, BookingEvent, Payment
from moving_company.services.booking_engine_service import create_booking_request
from moving_company.services.seo_analytics import analytics_ids, queue_booking_conversion
from moving_company.services.payment_provider_service import _record_payment_success


PRODUCTION_CONFIG = {
    'APP_ENV': 'production',
    'CANONICAL_ORIGIN': 'https://www.chigomove.online',
    'GA4_MEASUREMENT_ID': 'G-1A2B3C4D5E',
    'GTM_CONTAINER_ID': '',
}


def test_robots_and_sitemap_publish_only_canonical_public_urls():
    app = create_app(testing=True)
    app.config.update(CANONICAL_ORIGIN='https://www.chigomove.online')
    client = app.test_client()

    robots = client.get('/robots.txt')
    assert robots.status_code == 200
    assert 'Disallow: /booking' in robots.text
    assert 'Disallow: /customer/' in robots.text
    assert 'Sitemap: https://www.chigomove.online/sitemap.xml' in robots.text

    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    root = ElementTree.fromstring(response.data)
    urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
    assert 'https://www.chigomove.online/' in urls
    assert 'https://www.chigomove.online/terms' in urls
    assert not any(any(private in url for private in ('/booking', '/auth', '/customer', '/admin', '/api')) for url in urls)
    assert 'terms-of-service' not in ''.join(urls)


def test_public_canonical_and_private_noindex_metadata():
    app = create_app(testing=True)
    app.config.update(CANONICAL_ORIGIN='https://www.chigomove.online')
    client = app.test_client()

    public = client.get('/services/moving?campaign=sample')
    assert '<title>Moving Services in Abuja | Chigo Relocations</title>' in public.text
    assert '<link rel="canonical" href="https://www.chigomove.online/services/moving">' in public.text
    assert 'og:title' in public.text
    assert '"@type": "Service"' in public.text
    assert 'campaign=sample' not in public.text

    private = client.get('/auth/login')
    assert '<meta name="robots" content="noindex, nofollow">' in private.text
    assert 'rel="canonical"' not in private.text


def test_production_tracking_rejects_missing_and_placeholder_ids():
    app = create_app(testing=True)
    app.config.update(
        APP_ENV='production', CANONICAL_ORIGIN='https://www.chigomove.online',
        GA4_MEASUREMENT_ID='', GTM_CONTAINER_ID='',
    )
    with app.app_context():
        assert analytics_ids() == ('', '')
        app.config['GA4_MEASUREMENT_ID'] = 'G-XXXXXXXXXX'
        app.config['GTM_CONTAINER_ID'] = 'GTM-XXXXXXX'
        assert analytics_ids() == ('', '')
        app.config['GA4_MEASUREMENT_ID'] = 'G-1A2B3C4D5E'
        app.config['GTM_CONTAINER_ID'] = ''
        assert analytics_ids() == ('G-1A2B3C4D5E', '')

    app.config.update(PRODUCTION_CONFIG)
    public = app.test_client().get('/')
    assert public.text.count("gtag('config'") == 1
    assert public.text.count('googletagmanager.com/gtag/js') == 1
    assert 'googletagmanager.com/gtm.js' not in public.text
    assert 'Allow analytics' in public.text

    private = app.test_client().get('/auth/login')
    assert 'G-1A2B3C4D5E' not in private.text
    assert 'googletagmanager.com' not in private.text


def test_gtm_mode_uses_one_container_and_does_not_initialize_duplicate_ga4():
    app = create_app(testing=True)
    app.config.update(PRODUCTION_CONFIG)
    app.config['GTM_CONTAINER_ID'] = 'GTM-1A2B3C4'
    page = app.test_client().get('/')
    assert page.text.count('googletagmanager.com/gtm.js') == 1
    assert 'googletagmanager.com/gtag/js' not in page.text
    assert "gtag('config'" not in page.text


def test_analytics_payload_uses_only_safe_event_parameters_and_canonical_page_location():
    app = create_app(testing=True)
    app.config.update(PRODUCTION_CONFIG)
    client = app.test_client()
    with client.session_transaction() as session:
        session['_analytics_pending_events'] = [{
            'name': 'location_completed', 'key': 'random-event-key',
            'params': {'service_type': 'residential'},
        }]

    page = client.get('/?pickup_address=12%20Private%20Street&email=person@example.com')
    assert 'random-event-key' in page.text
    assert 'service_type' in page.text
    assert '12 Private Street' not in page.text
    assert 'person@example.com' not in page.text
    assert 'pickup_address=' not in page.text
    assert 'page_location: \'https://www.chigomove.online/\'' in page.text


def test_booking_conversion_marker_is_deduplicated_and_contains_no_booking_data():
    app = create_app(testing=True)
    app.config.update(PRODUCTION_CONFIG)
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': 'Private Pickup', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': 'Private Destination', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        })
        assert queue_booking_conversion(booking, 'location_completed') is True
        assert queue_booking_conversion(booking, 'location_completed') is False
        markers = BookingEvent.query.filter_by(booking_id=booking.id, action='analytics.location_completed').all()
        assert len(markers) == 1
        assert markers[0].new_value == {'event': 'location_completed', 'event_id': markers[0].new_value['event_id']}
        assert 'Private Pickup' not in str(markers[0].new_value)
        assert booking.booking_request_id not in str(markers[0].new_value)


def test_payment_conversion_only_follows_verified_provider_confirmation():
    app = create_app(testing=True)
    app.config.update(PRODUCTION_CONFIG)
    with app.app_context():
        booking = create_booking_request(location={
            'pickup_address': 'Pickup', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': 'Destination', 'destination_city': 'Abuja', 'destination_state': 'FCT',
        })
        booking.deposit_amount = 1500
        db.session.commit()
        payment = Payment(
            booking_id=booking.id, amount=1500, status='Pending', payment_reference='cs_validated',
            gateway='stripe', currency='NGN',
        )
        db.session.add(payment)
        db.session.commit()
        session = {
            'id': 'cs_validated', 'mode': 'payment', 'payment_status': 'unpaid',
            'amount_total': 150000, 'currency': 'ngn',
            'metadata': {'booking_request_id': booking.booking_request_id},
        }

        with pytest.raises(ValueError, match='could not confirm'):
            _record_payment_success(payment, session)
        assert BookingEvent.query.filter(BookingEvent.action.in_([
            'analytics.payment_success', 'analytics.booking_completed',
        ])).count() == 0

        session['payment_status'] = 'paid'
        _record_payment_success(payment, session)
        _record_payment_success(payment, session)
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='analytics.payment_success').count() == 1
        assert BookingEvent.query.filter_by(booking_id=booking.id, action='analytics.booking_completed').count() == 1
        assert Booking.query.filter_by(id=booking.id).one().workflow_state == 'CONFIRMED'
