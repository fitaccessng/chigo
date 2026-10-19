from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..models import Payment


BOOKING_POLICY_VERSION = 'chigo-terms-2026-10-16-v2'
SERVICE_TIMEZONE = ZoneInfo('Africa/Lagos')
DAMAGE_REPORT_WINDOW = timedelta(hours=72)
CANCELLABLE_STATUSES = frozenset({'Draft', 'Booking Received', 'Confirmed', 'Assigned'})
COMPLETED_STATUSES = frozenset({'Completed', 'Delivered'})
IN_PROGRESS_STATUSES = frozenset({'In Progress', 'En Route', 'Arrived', 'Loading', 'In Transit', 'Unloading'})


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def accepted_policy_version(booking):
    acceptance = (booking.calculation_data or {}).get('policy_acceptance') or {}
    return acceptance.get('policy_version')


def has_accepted_policy_rule(booking, rule_key):
    acceptance = (booking.calculation_data or {}).get('policy_acceptance') or {}
    snapshot = acceptance.get('policy_snapshot') or {}
    return bool(acceptance.get('policy_version') and snapshot.get(rule_key))


def actual_successful_payments(booking):
    return round(sum(
        float(payment.amount or 0)
        for payment in Payment.query.filter_by(booking_id=booking.id).all()
        if payment.status in {'Successful', 'Paid'}
    ), 2)


def cancellation_preview(booking, now=None):
    now = now or utc_now()
    local_today = as_utc(now).astimezone(SERVICE_TIMEZONE).date()
    is_same_day = bool(booking.move_date and booking.move_date == local_today)
    is_eligible = booking.status in CANCELLABLE_STATUSES
    is_completed = booking.status in COMPLETED_STATUSES or booking.workflow_state in {'COMPLETED', 'REVIEW_PENDING', 'REVIEWED'}
    is_in_progress = booking.status in IN_PROGRESS_STATUSES or booking.workflow_state == 'IN_PROGRESS'
    paid_amount = actual_successful_payments(booking)
    recorded_deposit = round(max(float(booking.deposit_amount or 0), 0), 2)
    paid_deposit = min(paid_amount, recorded_deposit)
    same_day_rule_applies = is_same_day and has_accepted_policy_rule(booking, 'same_day_cancellation') and paid_deposit > 0
    non_refundable_deposit = paid_deposit if same_day_rule_applies else 0.0
    refund_review_amount = round(max(paid_amount - non_refundable_deposit, 0), 2)
    if not paid_amount:
        refund_status = 'No successful payment recorded'
    elif same_day_rule_applies:
        refund_status = 'Deposit non-refundable under accepted same-day cancellation terms'
        if refund_review_amount:
            refund_status += '; remaining payment requires review'
    else:
        refund_status = 'Refund eligibility requires review'
    return {
        'eligible': is_eligible,
        'completed': is_completed,
        'in_progress': is_in_progress,
        'same_day': is_same_day,
        'same_day_rule_applies': same_day_rule_applies,
        'policy_accepted': has_accepted_policy_rule(booking, 'same_day_cancellation'),
        'scheduled_date': booking.move_date.isoformat() if booking.move_date else None,
        'paid_amount': paid_amount,
        'recorded_deposit': recorded_deposit,
        'paid_deposit': paid_deposit,
        'non_refundable_deposit': non_refundable_deposit,
        'refund_review_amount': refund_review_amount,
        'refund_status': refund_status,
        'timezone': str(SERVICE_TIMEZONE),
    }


def cancel_customer_booking(booking, user_id, reason='', now=None):
    now = as_utc(now or utc_now())
    preview = cancellation_preview(booking, now)
    if preview['completed']:
        raise ValueError('Completed bookings cannot be cancelled through the customer cancellation action.')
    if preview['in_progress']:
        raise ValueError('This service is in progress. Contact Chigo Support for operational assistance.')
    if not preview['eligible']:
        raise ValueError('This booking is not eligible for customer cancellation in its current status.')

    cancellation_data = {
        'booking_reference': booking.booking_request_id or booking.booking_number,
        'requested_by_id': user_id,
        'requested_at': now.isoformat(timespec='seconds'),
        'scheduled_service_date': preview['scheduled_date'],
        'reason': str(reason or '').strip()[:1000] or None,
        'policy_version': accepted_policy_version(booking),
        'same_day_rule_applied': preview['same_day_rule_applies'],
        'amount_actually_paid': preview['paid_amount'],
        'deposit_paid': preview['paid_deposit'],
        'non_refundable_deposit': preview['non_refundable_deposit'],
        'refund_review_amount': preview['refund_review_amount'],
        'refund_status': preview['refund_status'],
    }
    booking.status = 'Cancelled'
    booking.workflow_state = 'CANCELLED'
    booking.cancelled_at = now
    booking.cancellation_data = cancellation_data
    return cancellation_data


def damage_report_window(booking, now=None):
    if not has_accepted_policy_rule(booking, 'damage_reporting'):
        return {'policy_applies': False, 'available': False, 'missing_completion_time': False}
    if booking.status not in COMPLETED_STATUSES and booking.workflow_state not in {'COMPLETED', 'REVIEW_PENDING', 'REVIEWED'}:
        return {'policy_applies': True, 'available': False, 'missing_completion_time': False}
    completed_at = as_utc(booking.service_completed_at)
    if completed_at is None:
        return {'policy_applies': True, 'available': False, 'missing_completion_time': True}
    now = as_utc(now or utc_now())
    if completed_at > now:
        return {
            'policy_applies': True, 'available': False,
            'missing_completion_time': False, 'not_started': True,
            'completed_at': completed_at,
            'completed_local': completed_at.astimezone(SERVICE_TIMEZONE),
            'timezone': str(SERVICE_TIMEZONE),
        }
    deadline = completed_at + DAMAGE_REPORT_WINDOW
    return {
        'policy_applies': True,
        'available': now <= deadline,
        'missing_completion_time': False,
        'completed_at': completed_at,
        'deadline': deadline,
        'expired': now > deadline,
        'timezone': str(SERVICE_TIMEZONE),
        'deadline_local': deadline.astimezone(SERVICE_TIMEZONE),
        'completed_local': completed_at.astimezone(SERVICE_TIMEZONE),
    }