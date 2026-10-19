from datetime import datetime

from flask import Blueprint, abort, render_template, request

from ..services.dispatch_service import respond_to_offer, token_offer


partner_dispatch_bp = Blueprint('partner_dispatch', __name__)


@partner_dispatch_bp.route('/offers/<token>', methods=['GET', 'POST'])
def respond(token):
    offer = token_offer(token)
    if offer is None:
        abort(404)
    if offer.status != 'OFFERED' or offer.expires_at <= datetime.utcnow():
        return render_template('partner/offer_expired.html'), 410

    if request.method == 'POST':
        decision = request.form.get('decision', '')
        try:
            respond_to_offer(token, decision, request.form.get('reason', ''))
            return render_template('partner/offer_response.html', accepted=decision == 'accept')
        except (LookupError, ValueError):
            return render_template('partner/offer_expired.html'), 410

    booking = offer.dispatch_request.booking
    return render_template('partner/offer.html', offer=offer, booking=booking)