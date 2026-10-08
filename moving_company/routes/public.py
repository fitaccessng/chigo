from flask import Blueprint, render_template

from ..services.pricing_catalog_service import get_pricing_map

public_bp = Blueprint('public', __name__)


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
