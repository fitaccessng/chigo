from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for

from ..extensions import csrf
from ..services.seo_analytics import (
    CANONICAL_PATHS, PUBLIC_PAGES, acknowledge_analytics_events, canonical_origin,
)

from ..services.pricing_catalog_service import get_pricing_map

public_bp = Blueprint('public', __name__)


@public_bp.route('/robots.txt')
def robots():
    origin = canonical_origin()
    lines = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /auth/',
        'Disallow: /booking',
        'Disallow: /customer/',
        'Disallow: /admin/',
        'Disallow: /api/',
        'Disallow: /support/',
    ]
    if origin:
        lines.append(f'Sitemap: {origin}/sitemap.xml')
    return Response('\n'.join(lines) + '\n', mimetype='text/plain')


@public_bp.route('/sitemap.xml')
def sitemap():
    origin = canonical_origin()
    if not origin:
        abort(503, description='Configure CANONICAL_ORIGIN to publish the sitemap.')
    urls = []
    for endpoint in PUBLIC_PAGES:
        path = CANONICAL_PATHS.get(endpoint) or url_for(endpoint)
        urls.append(f'<url><loc>{origin}{path}</loc></url>')
    body = '<?xml version="1.0" encoding="UTF-8"?>' \
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(urls) + '</urlset>'
    return Response(body, mimetype='application/xml')


@csrf.exempt
@public_bp.route('/analytics/ack', methods=['POST'])
def analytics_ack():
    payload = request.get_json(silent=True) or {}
    acknowledge_analytics_events(payload.get('keys'))
    return jsonify({'success': True})


@public_bp.route('/')
def home():
    return render_template('public/index.html')


@public_bp.route('/services')
def services():
    return render_template('public/services.html')


@public_bp.route('/services/moving')
def moving_service():
    return render_template('public/service_detail.html', title='Moving')


@public_bp.route('/services/packing')
def packing_service():
    return render_template('public/service_detail.html', title='Packing')


@public_bp.route('/services/cleaning')
def cleaning_service():
    return render_template('public/service_detail.html', title='Cleaning')


@public_bp.route('/services/unpacking')
def unpacking_service():
    return render_template('public/service_detail.html', title='Unpacking')


@public_bp.route('/services/storage')
def storage_service():
    return render_template('public/service_detail.html', title='Storage')


@public_bp.route('/services/logistics')
def logistics_service():
    return render_template('public/service_detail.html', title='Logistics')


@public_bp.route('/how-it-works')
def how_it_works():
    return render_template('public/how_it_works.html')


@public_bp.route('/pricing')
def pricing():
    return render_template('public/pricing.html', pricing=get_pricing_map())


@public_bp.route('/about')
def about():
    return render_template('public/about.html')


@public_bp.route('/contact')
def contact():
    return render_template('public/contact.html')


@public_bp.route('/faq')
def faq():
    return render_template('public/faq.html')


@public_bp.route('/terms')
@public_bp.route('/terms-of-service')
def terms():
    return render_template('public/terms.html')


@public_bp.route('/privacy')
@public_bp.route('/privacy-policy')
def privacy():
    return render_template('public/privacy.html')


@public_bp.route('/get-a-quote')
def get_a_quote():
    return render_template('public/get_a_quote.html')
