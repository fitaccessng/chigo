import re
import secrets
from urllib.parse import urlsplit

from flask import current_app, has_request_context, request, session, url_for


PUBLIC_PAGES = {
    'public.home': ('Chigo Relocations | Abuja Moving & Relocation Services', 'Plan a house or office move in Abuja with Chigo Relocations. Coordinate moving, packing, loading, cleaning and storage in one move plan.'),
    'public.services': ('Moving, Packing & Relocation Services in Abuja | Chigo', 'Explore house moving, office relocation, packing, loading, cleaning and storage services from Chigo Relocations in Abuja.'),
    'public.moving_service': ('Moving Services in Abuja | Chigo Relocations', 'Plan residential and commercial moves in Abuja with coordinated transport, loading and move-day support.'),
    'public.packing_service': ('Packing & Loading Services in Abuja | Chigo Relocations', 'Add packing, careful loading and transport to a coordinated Abuja moving plan with Chigo Relocations.'),
    'public.cleaning_service': ('Moving Cleaning Services in Abuja | Chigo Relocations', 'Coordinate move-in or move-out cleaning with your relocation plan in Abuja.'),
    'public.unpacking_service': ('Unpacking Services in Abuja | Chigo Relocations', 'Plan unpacking and item placement alongside your move with Chigo Relocations in Abuja.'),
    'public.storage_service': ('Moving Storage Services in Abuja | Chigo Relocations', 'Coordinate storage duration, access and delivery with your move plan in Abuja.'),
    'public.logistics_service': ('Relocation Logistics in Abuja | Chigo Relocations', 'Plan transport and special handling for office equipment, bulky items and other relocation needs in Abuja.'),
    'public.how_it_works': ('How Moving and Booking Works | Chigo Relocations', 'See how to plan locations, inventory, services, timing and an estimate for a move with Chigo Relocations.'),
    'public.pricing': ('Moving Prices & Estimates in Abuja | Chigo Relocations', 'Review the moving estimate components used for route, property, access and optional services in Abuja.'),
    'public.about': ('About Chigo Relocations | Abuja', 'Learn about Chigo Relocations and its move planning and relocation services in Abuja and Nigeria.'),
    'public.contact': ('Contact Chigo Relocations | Abuja Moving Services', 'Contact Chigo Relocations in Abuja about house moving, office relocation, packing, loading and storage.'),
    'public.faq': ('Moving Services FAQs | Chigo Relocations', 'Answers about planning a move, estimates, packing, scheduling and relocation services with Chigo Relocations.'),
    'public.terms': ('Terms of Service | Chigo Relocations', 'Read the terms that apply when using Chigo Relocations services and website.'),
    'public.privacy': ('Privacy Policy | Chigo Relocations', 'Read how Chigo Relocations handles information submitted through its website and services.'),
    'public.get_a_quote': ('Request a Moving Estimate | Chigo Relocations', 'Start a move plan to provide the route, property, inventory and services needed for an estimate.'),
}

CANONICAL_PATHS = {
    'public.terms': '/terms',
    'public.privacy': '/privacy',
}

ANALYTICS_EVENTS = {
    'booking_started', 'location_completed', 'inventory_completed', 'services_selected',
    'schedule_completed', 'quote_generated', 'checkout_started', 'booking_completed',
    'payment_success',
}

PUBLIC_ENDPOINTS = frozenset(PUBLIC_PAGES)
GA4_ID_PATTERN = re.compile(r'^G-[A-Z0-9]{10}$')
GTM_ID_PATTERN = re.compile(r'^GTM-[A-Z0-9]{7}$')


def canonical_origin():
    origin = str(current_app.config.get('CANONICAL_ORIGIN') or '').strip().rstrip('/')
    parsed = urlsplit(origin)
    if (
        parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
        or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
    ):
        return ''
    return f'https://{parsed.netloc}'


def _valid_google_id(value, pattern):
    if not pattern.fullmatch(value):
        return False
    suffix = value.split('-', 1)[-1]
    return suffix not in {'EXAMPLE1234', 'PLACEHOLDER', 'TEST1234567'} and len(set(suffix)) > 1 and not any(
        marker in suffix for marker in ('PLACEHOLDER', 'EXAMPLE', 'TEST', 'XXXX')
    )


def analytics_ids():
    if str(current_app.config.get('APP_ENV', '')).casefold() != 'production':
        return '', ''
    if not canonical_origin():
        return '', ''
    ga4_id = str(current_app.config.get('GA4_MEASUREMENT_ID') or '').strip().upper()
    gtm_id = str(current_app.config.get('GTM_CONTAINER_ID') or '').strip().upper()
    return (ga4_id if _valid_google_id(ga4_id, GA4_ID_PATTERN) else ''), (gtm_id if _valid_google_id(gtm_id, GTM_ID_PATTERN) else '')


def queue_analytics_event(name, *, dedupe_key=None, service_type=None):
    if name not in ANALYTICS_EVENTS or not any(analytics_ids()) or not has_request_context():
        return
    event = {'name': name, 'key': str(dedupe_key or secrets.token_urlsafe(18)), 'params': {}}
    if service_type in {'residential', 'commercial', 'packing', 'storage', 'logistics'}:
        event['params']['service_type'] = service_type
    pending = session.setdefault('_analytics_pending_events', [])
    if not any(item.get('key') == event['key'] for item in pending):
        pending.append(event)
        session.modified = True


def _seo_context():
    endpoint = request.endpoint or ''
    public = endpoint in PUBLIC_ENDPOINTS
    title, description = PUBLIC_PAGES.get(endpoint, ('Chigo Relocations', ''))
    if endpoint.endswith('_service'):
        service_title = str(request.view_args.get('title') or '').strip()
        if service_title:
            title = f'{service_title} Services in Abuja | Chigo Relocations'
    origin = canonical_origin()
    path = CANONICAL_PATHS.get(endpoint, request.path) if public else ''
    canonical = f'{origin}{path}' if origin and path else ''
    return {
        'seo_public_page': public,
        'seo_title': title,
        'seo_description': description,
        'seo_canonical_url': canonical,
        'seo_origin': origin,
        'seo_page_path': path,
    }


def _analytics_context():
    ga4_id, gtm_id = analytics_ids()
    pending = session.get('_analytics_pending_events', []) if ga4_id or gtm_id else []
    public_page = (request.endpoint or '') in PUBLIC_ENDPOINTS
    enabled = bool((ga4_id or gtm_id) and public_page)
    return {
        'analytics_enabled': enabled,
        'analytics_ga4_id': ga4_id if not gtm_id else '',
        'analytics_gtm_id': gtm_id if public_page else '',
        'analytics_public_pageview': bool(enabled and public_page),
        'analytics_pending_events': pending if enabled else [],
    }


def inject_seo_analytics_context():
    return {**_seo_context(), **_analytics_context()}


def acknowledge_analytics_events(keys):
    if not isinstance(keys, list):
        return
    acknowledged = {str(key) for key in keys}
    pending = session.get('_analytics_pending_events', [])
    remaining = [event for event in pending if str(event.get('key')) not in acknowledged]
    if remaining:
        session['_analytics_pending_events'] = remaining
    else:
        session.pop('_analytics_pending_events', None)
    session.modified = True


def queue_booking_conversion(booking, name):
    if name not in ANALYTICS_EVENTS or not any(analytics_ids()):
        return False
    from ..extensions import db
    from ..models import BookingEvent
    from .booking_engine_service import record_event

    action = f'analytics.{name}'
    event_record = BookingEvent.query.filter_by(booking_id=booking.id, action=action).first()
    if event_record:
        event_key = (event_record.new_value or {}).get('event_id')
    else:
        event_key = secrets.token_urlsafe(18)
        record_event(booking, action, new={'event': name, 'event_id': event_key})
        db.session.commit()
    queue_analytics_event(name, dedupe_key=event_key, service_type=booking.primary_service)
    return event_record is None