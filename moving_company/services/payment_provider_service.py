from __future__ import annotations

import os

import stripe
from flask import current_app, url_for

from ..extensions import db
from ..models import Booking, Payment
from .booking_engine_service import record_event


def _stripe_secret_key():
    return current_app.config.get('STRIPE_SECRET_KEY') or os.getenv('STRIPE_SECRET_KEY')


def _stripe_webhook_secret():
    return current_app.config.get('STRIPE_WEBHOOK_SECRET') or os.getenv('STRIPE_WEBHOOK_SECRET')


def _stripe_client():
    secret = _stripe_secret_key()
    if not secret:
        raise ValueError('Online payments are not configured yet. Your quote and booking request have been saved.')
    return stripe.StripeClient(secret)


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _record_payment_success(payment: Payment, checkout_session) -> Booking:
    booking = Booking.query.get_or_404(payment.booking_id)
    metadata = _value(checkout_session, 'metadata', {}) or {}
    amount_total = _value(checkout_session, 'amount_total')
    currency = str(_value(checkout_session, 'currency', '') or '').lower()
    if (
        _value(checkout_session, 'id') != payment.payment_reference
        or _value(checkout_session, 'mode') != 'payment'
        or _value(checkout_session, 'payment_status') != 'paid'
        or int(amount_total or 0) != int(round(payment.amount * 100))
        or currency != payment.currency.lower()
        or metadata.get('booking_request_id') != booking.booking_request_id
    ):
        raise ValueError('Stripe could not confirm the expected payment for this booking.')

    if payment.status != 'Successful':
        payment.status = 'Successful'
        booking.payment_status = 'Paid'
        booking.status = 'Confirmed'
        booking.workflow_state = 'CONFIRMED'
        record_event(
            booking, 'payment.succeeded',
            new={'reference': payment.payment_reference, 'amount': payment.amount},
        )
        db.session.commit()
    return booking


def initialize_payment(booking: Booking, customer_email: str):
    if booking.quote_status != 'Accepted' or booking.workflow_state not in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING'}:
        raise ValueError('Accept the current quote before starting payment.')
    if booking.payment_status == 'Paid':
        raise ValueError('This booking request is already paid.')
    client = _stripe_client()
    if not customer_email:
        raise ValueError('Sign in with an email address before paying.')

    amount = float(booking.deposit_amount or 0)
    if amount <= 0:
        raise ValueError('The accepted quote does not have a payable amount.')

    pending = Payment.query.filter_by(booking_id=booking.id, status='Pending', gateway='stripe').first()
    previous_attempts = Payment.query.filter_by(booking_id=booking.id, gateway='stripe').count()
    if pending and pending.authorization_url:
        try:
            checkout_session = client.v1.checkout.sessions.retrieve(pending.payment_reference)
            if _value(checkout_session, 'status') == 'open':
                return pending
            if _value(checkout_session, 'payment_status') == 'paid':
                _record_payment_success(pending, checkout_session)
                raise ValueError('This booking request is already paid.')
            pending.status = 'Expired'
            db.session.commit()
        except stripe.StripeError as error:
            raise ValueError('The payment provider could not be reached. Please try again later.') from error

    try:
        checkout_session = client.v1.checkout.sessions.create(
            {
                'mode': 'payment',
                'payment_method_types': ['card'],
                'customer_email': customer_email,
                'client_reference_id': booking.booking_request_id,
                'line_items': [{
                    'price_data': {
                        'currency': 'ngn',
                        'unit_amount': int(round(amount * 100)),
                        'product_data': {
                            'name': f'Moving booking deposit · {booking.booking_request_id}',
                            'description': 'Deposit to secure your Chigo moving booking.',
                        },
                    },
                    'quantity': 1,
                }],
                'metadata': {'booking_request_id': booking.booking_request_id},
                'success_url': f"{url_for('booking.payment_callback', _external=True)}?session_id={{CHECKOUT_SESSION_ID}}",
                'cancel_url': url_for(
                    'booking.workflow', request_id=booking.booking_request_id,
                    stage='payment', _external=True,
                ),
            },
            options={'idempotency_key': f'chigo-deposit-{booking.id}-{previous_attempts + 1}-{int(round(amount * 100))}'},
        )
    except stripe.StripeError as error:
        raise ValueError('The payment provider could not start checkout. Your booking request is unchanged; try again later.') from error

    session_id = _value(checkout_session, 'id')
    checkout_url = _value(checkout_session, 'url')
    if not session_id or not checkout_url:
        raise ValueError('The payment provider returned an incomplete checkout session. Please try again later.')

    payment = Payment(
        booking_id=booking.id,
        amount=amount,
        status='Pending',
        payment_reference=session_id,
        gateway='stripe',
        authorization_url=checkout_url,
        currency='NGN',
    )
    db.session.add(payment)
    booking.workflow_state = 'PAYMENT_PENDING'
    booking.payment_status = 'Pending'
    record_event(
        booking, 'payment.initialized',
        new={'reference': session_id, 'amount': amount, 'gateway': 'stripe'},
        user_id=booking.customer_id,
    )
    db.session.commit()
    return payment


def verify_payment(reference: str):
    if not reference:
        raise ValueError('A Stripe Checkout session is required.')
    payment = Payment.query.filter_by(payment_reference=reference, gateway='stripe').first()
    if payment is None:
        raise ValueError('This Stripe session is not attached to a booking request.')
    if payment.status == 'Successful':
        return Booking.query.get_or_404(payment.booking_id)

    client = _stripe_client()
    try:
        checkout_session = client.v1.checkout.sessions.retrieve(reference)
    except stripe.StripeError as error:
        raise ValueError('Stripe could not verify this payment yet. Please try again shortly.') from error
    return _record_payment_success(payment, checkout_session)


def process_stripe_webhook(payload: bytes, signature: str) -> bool:
    webhook_secret = _stripe_webhook_secret()
    if not webhook_secret:
        raise ValueError('Stripe webhook verification is not configured.')
    if not signature:
        raise ValueError('Stripe signature header is required.')
    try:
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as error:
        raise ValueError('Stripe webhook signature is invalid.') from error

    event_type = _value(event, 'type')
    if event_type not in {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}:
        return False
    checkout_session = _value(_value(event, 'data', {}), 'object', {})
    if _value(checkout_session, 'payment_status') != 'paid':
        return False
    session_id = _value(checkout_session, 'id')
    payment = Payment.query.filter_by(payment_reference=session_id, gateway='stripe').first()
    if payment is None:
        raise ValueError('Stripe session is not attached to a booking request.')
    _record_payment_success(payment, checkout_session)
    return True
