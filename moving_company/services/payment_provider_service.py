from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests
from flask import current_app, url_for

from ..extensions import db
from ..models import Booking, Payment
from .booking_engine_service import record_event
from .seo_analytics import queue_booking_conversion


BACHS_GATEWAY = 'bachs'
BACHS_API_VERSION = 'v1'
WEBHOOK_TOLERANCE_SECONDS = 300


def _bachs_api_key():
    return (current_app.config.get('BACHS_API_KEY') or os.getenv('BACHS_API_KEY') or '').strip()


def _bachs_webhook_secret():
    return (current_app.config.get('BACHS_WEBHOOK_SECRET') or os.getenv('BACHS_WEBHOOK_SECRET') or '').strip()


def _bachs_base_url(api_key):
    if api_key.startswith('sk_sandbox_'):
        return f'https://sandbox-api.bachs.io/{BACHS_API_VERSION}'
    if api_key.startswith('sk_live_'):
        return f'https://api.bachs.io/{BACHS_API_VERSION}'
    raise ValueError('Bachs API key is invalid or uses an unsupported environment.')


def _bachs_request(method, path, *, payload=None, idempotency_key=None):
    api_key = _bachs_api_key()
    if not api_key:
        raise ValueError('Online payments are not configured yet. Your quote and booking request have been saved.')
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    try:
        response = requests.request(
            method, f'{_bachs_base_url(api_key)}{path}', headers=headers, json=payload, timeout=15,
        )
    except requests.RequestException as error:
        current_app.logger.warning('Bachs API request failed: %s', error.__class__.__name__)
        raise ValueError('The payment provider could not be reached. Please try again later.') from error
    if not response.ok:
        error_code = None
        try:
            error_code = (response.json().get('error') or {}).get('code')
        except (ValueError, AttributeError):
            pass
        current_app.logger.warning('Bachs API request failed: status=%s code=%s', response.status_code, error_code)
        raise ValueError('The payment provider could not start checkout. Your booking request is unchanged; try again later.')
    try:
        return response.json()
    except ValueError as error:
        current_app.logger.warning('Bachs API returned a non-JSON response.')
        raise ValueError('The payment provider returned an incomplete checkout session. Please try again later.') from error


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError('The accepted quote does not have a payable amount.') from error


def _record_payment_success(payment: Payment, checkout_id: str, amount, currency: str) -> Booking:
    booking = db.get_or_404(Booking, payment.booking_id)
    if (
        checkout_id != payment.payment_reference
        or _money(amount) != _money(payment.amount)
        or str(currency or '').upper() != payment.currency.upper()
    ):
        raise ValueError('Bachs could not confirm the expected payment for this booking.')

    if payment.status != 'Successful':
        payment.status = 'Successful'
        payment.transaction_status = 'SUCCEEDED'
        booking.payment_status = 'Paid'
        booking.status = 'Confirmed'
        booking.workflow_state = 'CONFIRMED'
        record_event(booking, 'payment.succeeded', new={
            'reference': payment.payment_reference, 'amount': payment.amount, 'gateway': BACHS_GATEWAY,
        })
        db.session.commit()
    queue_booking_conversion(booking, 'payment_success')
    queue_booking_conversion(booking, 'booking_completed')
    return booking


def initialize_payment(booking: Booking, customer_email: str):
    if booking.quote_status != 'Accepted' or booking.workflow_state not in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING'}:
        raise ValueError('Accept the current quote before starting payment.')
    if booking.payment_status == 'Paid':
        raise ValueError('This booking request is already paid.')
    if not customer_email:
        raise ValueError('Sign in with an email address before paying.')
    if not _bachs_api_key():
        raise ValueError('Online payments are not configured yet. Your quote and booking request have been saved.')

    amount = _money(booking.deposit_amount)
    if amount <= 0:
        raise ValueError('The accepted quote does not have a payable amount.')
    pending = Payment.query.filter_by(booking_id=booking.id, status='Pending', gateway=BACHS_GATEWAY).first()
    if pending and pending.authorization_url:
        session_data = _bachs_request('GET', f'/checkout-sessions/{pending.payment_reference}')
        if session_data.get('status') == 'open':
            return pending
        pending.status = 'Expired'
        db.session.commit()

    attempt = Payment.query.filter_by(booking_id=booking.id, gateway=BACHS_GATEWAY).count() + 1
    checkout = _bachs_request(
        'POST', '/checkout-sessions',
        payload={
            'pricing': {'currency': 'NGN', 'amount': format(amount, '.2f')},
            'customer': {'email': customer_email},
            'success_url': url_for('booking.payment_callback', _external=True),
            'cancel_url': url_for('booking.workflow', request_id=booking.booking_request_id, stage='payment', _external=True),
        },
        idempotency_key=f'chigo-deposit-{booking.id}-{attempt}-{format(amount, ".2f")}',
    )
    checkout_id = checkout.get('checkout_id')
    checkout_url = checkout.get('checkout_url')
    if not checkout_id or not checkout_url:
        raise ValueError('The payment provider returned an incomplete checkout session. Please try again later.')

    payment = Payment(
        booking_id=booking.id, amount=float(amount), status='Pending', payment_reference=checkout_id,
        gateway=BACHS_GATEWAY, authorization_url=checkout_url, currency='NGN', transaction_status='OPEN',
    )
    db.session.add(payment)
    booking.workflow_state = 'PAYMENT_PENDING'
    booking.payment_status = 'Pending'
    record_event(booking, 'payment.initialized', new={
        'reference': checkout_id, 'amount': float(amount), 'gateway': BACHS_GATEWAY,
    }, user_id=booking.customer_id)
    db.session.commit()
    return payment


def verify_payment(reference: str):
    if not reference:
        raise ValueError('A Bachs Checkout session is required.')
    payment = Payment.query.filter_by(payment_reference=reference, gateway=BACHS_GATEWAY).first()
    if payment is None:
        raise ValueError('This Bachs Checkout session is not attached to a booking request.')
    if payment.status == 'Successful':
        return db.get_or_404(Booking, payment.booking_id)
    _bachs_request('GET', f'/checkout-sessions/{reference}')
    raise ValueError('Payment is being confirmed. Your booking will update when Bachs confirms it.')


def _verify_bachs_webhook(raw_body: bytes, headers):
    secret = _bachs_webhook_secret()
    if not secret:
        raise ValueError('Bachs webhook verification is not configured.')
    signature_header = headers.get('X-Bachs-Signature-V2', '')
    signatures, timestamp = [], None
    if signature_header:
        for part in signature_header.split(','):
            key, separator, value = part.strip().partition('=')
            if not separator:
                continue
            if key == 't':
                timestamp = value
            elif key == 'v1':
                signatures.append(value)
    else:
        timestamp = headers.get('X-Bachs-Timestamp', '')
        signatures = [headers.get('X-Bachs-Signature', '')]
    if not any(signatures):
        raise ValueError('Bachs webhook signature is missing.')
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError) as error:
        raise ValueError('Bachs webhook timestamp is invalid.') from error
    if abs(time.time() - timestamp) > WEBHOOK_TOLERANCE_SECONDS:
        raise ValueError('Bachs webhook timestamp is outside the allowed window.')
    expected = hmac.new(secret.encode(), f'{timestamp}.'.encode() + raw_body, hashlib.sha256).hexdigest()
    if not any(signature and hmac.compare_digest(expected, signature) for signature in signatures):
        raise ValueError('Bachs webhook signature is invalid.')


def process_bachs_webhook(raw_body: bytes, headers) -> bool:
    _verify_bachs_webhook(raw_body, headers)
    try:
        event = json.loads(raw_body)
    except (TypeError, ValueError) as error:
        raise ValueError('Bachs webhook payload is invalid.') from error
    if event.get('type') != 'collection.succeeded':
        return False
    data = event.get('data') or {}
    checkout_id = data.get('checkout_id')
    payment = Payment.query.filter_by(payment_reference=checkout_id, gateway=BACHS_GATEWAY).first()
    if payment is None:
        raise ValueError('Bachs Checkout session is not attached to a booking request.')
    _record_payment_success(payment, checkout_id, data.get('amount'), data.get('currency'))
    return True
