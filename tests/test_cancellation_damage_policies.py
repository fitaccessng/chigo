from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Booking, IncidentReport, Payment, User
from moving_company.services.booking_engine_service import create_booking_request, calculate_quote
from moving_company.services.booking_policy_service import BOOKING_POLICY_VERSION
from moving_company.services.service_workflow import validate_booking_question_data


PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


@pytest.fixture
def app(tmp_path):
    application = create_app(testing=True)
    application.config.update(WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=str(tmp_path), MAX_IMAGE_SIZE=1024 * 1024)
    yield application
    with application.app_context():
        db.session.remove()


def make_customer(email):
    user = User(first_name='Policy', last_name='Customer', email=email, phone='08012345678')
    user.set_password('password123')
    db.session.add(user)
    db.session.flush()
    return user


def make_booking(customer_id, *, scheduled_date=None, status='Confirmed', completed_at=None, policy=True):
    booking = create_booking_request(
        customer_id=customer_id,
        location={
            'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'destination_address': 'Plot 405 Constitution Avenue', 'destination_city': 'Abuja',
            'destination_state': 'FCT',
        },
    )
    booking.status = status
    booking.workflow_state = 'COMPLETED' if status == 'Completed' else 'QUOTE_ACCEPTED'
    booking.move_date = scheduled_date
    booking.deposit_amount = 100_000
    booking.service_completed_at = completed_at
    booking.calculation_data = {
        'policy_acceptance': {
            'policy_version': BOOKING_POLICY_VERSION if policy else 'chigo-terms-2026-10-16-v1',
            'accepted_at': '2026-10-16T12:00:00+00:00',
            'customer_id': customer_id,
            'policy_snapshot': {
                'same_day_cancellation': 'same-day rule accepted',
                'damage_reporting': '72-hour rule accepted',
            },
        } if policy else {},
    }
    db.session.commit()
    return booking


def login(client, customer_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(customer_id)
        session['_fresh'] = True


def test_customer_same_day_cancellation_requires_confirmation_and_records_only_paid_deposit(app):
    with app.app_context():
        customer = make_customer('cancel-owner@example.com')
        booking = make_booking(customer.id, scheduled_date=date.today())
        db.session.add(Payment(
            booking_id=booking.id, amount=45_000, status='Successful', payment_reference='paid-deposit',
            gateway='test', currency='NGN',
        ))
        db.session.commit()
        booking_id, customer_id = booking.id, customer.id

    client = app.test_client()
    login(client, customer_id)
    page = client.get(f'/customer/bookings/{booking_id}/cancel')
    assert page.status_code == 200
    assert b'Your booking is scheduled for today' in page.data
    assert b'Keep My Booking' in page.data
    assert b'Confirm Cancellation' in page.data
    with app.app_context():
        assert Booking.query.get(booking_id).status == 'Confirmed'

    kept = client.get(f'/customer/bookings/{booking_id}')
    assert kept.status_code == 200
    with app.app_context():
        assert Booking.query.get(booking_id).status == 'Confirmed'

    cancelled = client.post(f'/customer/bookings/{booking_id}/cancel', data={
        'action': 'confirm_cancellation', 'reason': 'Plans changed',
    })
    assert cancelled.status_code == 302
    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.status == 'Cancelled'
        assert booking.cancellation_data['deposit_paid'] == 45_000
        assert booking.cancellation_data['non_refundable_deposit'] == 45_000
        assert booking.cancellation_data['refund_status'].startswith('Deposit non-refundable')
        assert booking.payments[0].status == 'Successful'
        assert booking.cancellation_data['reason'] == 'Plans changed'
        assert booking.cancelled_at is not None


def test_unpaid_same_day_cancellation_does_not_create_or_forfeit_a_deposit(app):
    with app.app_context():
        customer = make_customer('cancel-unpaid@example.com')
        booking = make_booking(customer.id, scheduled_date=date.today())
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)

    response = client.post(f'/customer/bookings/{booking_id}/cancel', data={'action': 'confirm_cancellation'})

    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.cancellation_data['amount_actually_paid'] == 0
        assert booking.cancellation_data['deposit_paid'] == 0
        assert booking.cancellation_data['non_refundable_deposit'] == 0
        assert booking.cancellation_data['refund_status'] == 'No successful payment recorded'
        assert Payment.query.filter_by(booking_id=booking_id).count() == 0


def test_earlier_cancellation_flags_paid_amount_for_review_without_same_day_forfeiture(app):
    with app.app_context():
        customer = make_customer('cancel-early@example.com')
        booking = make_booking(customer.id, scheduled_date=date.today() + timedelta(days=2))
        db.session.add(Payment(
            booking_id=booking.id, amount=45_000, status='Successful', payment_reference='early-paid',
            gateway='test', currency='NGN',
        ))
        db.session.commit()
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)

    response = client.post(f'/customer/bookings/{booking_id}/cancel', data={'action': 'confirm_cancellation'})

    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.cancellation_data['non_refundable_deposit'] == 0
        assert booking.cancellation_data['refund_review_amount'] == 45_000
        assert booking.cancellation_data['refund_status'] == 'Refund eligibility requires review'
        assert booking.payments[0].status == 'Successful'


def test_same_day_rule_is_not_applied_to_booking_that_accepted_older_terms(app):
    with app.app_context():
        customer = make_customer('cancel-legacy-terms@example.com')
        booking = make_booking(customer.id, scheduled_date=date.today(), policy=False)
        db.session.add(Payment(
            booking_id=booking.id, amount=45_000, status='Successful', payment_reference='legacy-paid',
            gateway='test', currency='NGN',
        ))
        db.session.commit()
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)

    assert client.post(f'/customer/bookings/{booking_id}/cancel', data={'action': 'confirm_cancellation'}).status_code == 302

    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.cancellation_data['policy_version'] is None
        assert booking.cancellation_data['same_day_rule_applied'] is False
        assert booking.cancellation_data['non_refundable_deposit'] == 0
        assert booking.cancellation_data['refund_review_amount'] == 45_000


def test_cancellation_is_owner_scoped_and_rejects_completed_and_repeated_requests(app):
    with app.app_context():
        owner = make_customer('cancel-owner2@example.com')
        other = make_customer('cancel-other@example.com')
        completed = make_booking(owner.id, scheduled_date=date.today(), status='Completed', completed_at=datetime.now(timezone.utc))
        eligible = make_booking(owner.id, scheduled_date=date.today() + timedelta(days=1))
        completed_id, eligible_id, owner_id, other_id = completed.id, eligible.id, owner.id, other.id
    client = app.test_client()
    login(client, other_id)
    assert client.get(f'/customer/bookings/{eligible_id}/cancel').status_code == 404

    login(client, owner_id)
    denied = client.post(f'/customer/bookings/{completed_id}/cancel', data={'action': 'confirm_cancellation'})
    assert denied.status_code == 200
    with app.app_context():
        assert Booking.query.get(completed_id).status == 'Completed'
        assert Booking.query.get(completed_id).cancelled_at is None

    assert client.post(f'/customer/bookings/{eligible_id}/cancel', data={'action': 'confirm_cancellation'}).status_code == 302
    repeated = client.post(f'/customer/bookings/{eligible_id}/cancel', data={'action': 'confirm_cancellation'})
    assert repeated.status_code == 200
    with app.app_context():
        booking = Booking.query.get(eligible_id)
        assert booking.status == 'Cancelled'
        assert sum(event.action == 'customer.cancellation_confirmed' for event in booking.events) == 1
        assert Payment.query.filter_by(booking_id=eligible_id).count() == 0


def test_damage_submission_is_accepted_at_exact_72_hour_deadline_and_evidence_is_private(app, tmp_path, monkeypatch):
    completed_at = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    deadline = completed_at + timedelta(hours=72)
    with app.app_context():
        customer = make_customer('damage-owner@example.com')
        booking = make_booking(customer.id, status='Completed', completed_at=completed_at)
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)
    monkeypatch.setattr('moving_company.routes.customer.booking_policy_service.utc_now', lambda: deadline)

    page = client.get(f'/customer/bookings/{booking_id}/damage')
    assert page.status_code == 200
    assert b'Report by 13 October 2026 at 01:00 PM Africa/Lagos time' in page.data
    response = client.post(f'/customer/bookings/{booking_id}/damage', data={
        'item': 'Glass table', 'description': 'A corner is chipped.', 'noticed_at': '',
        'evidence': (BytesIO(PNG_BYTES), 'damage.png'),
    })
    assert response.status_code == 302
    with app.app_context():
        incident = IncidentReport.query.filter_by(booking_id=booking_id).one()
        assert incident.submitted_at.replace(tzinfo=timezone.utc) == deadline
        assert incident.customer_id == customer_id
        assert incident.contact_snapshot['email'] == 'damage-owner@example.com'
        assert incident.evidence[0]['original_filename'] == 'damage.png'
        file_path = Path(tmp_path) / incident.evidence[0]['file_url']
        assert file_path.is_file()

    evidence_response = client.get(f'/customer/bookings/{booking_id}/damage-reports/{incident.id}/evidence/0')
    assert evidence_response.status_code == 200
    other = app.test_client()
    with app.app_context():
        second_customer = make_customer('damage-other@example.com')
        other_id = second_customer.id
    login(other, other_id)
    assert other.get(f'/customer/bookings/{booking_id}/damage-reports/{incident.id}/evidence/0').status_code in {302, 404}


def test_damage_submission_after_deadline_is_rejected_and_old_reports_remain_visible(app, monkeypatch):
    completed_at = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    after_deadline = completed_at + timedelta(hours=72, seconds=1)
    with app.app_context():
        customer = make_customer('damage-expired@example.com')
        booking = make_booking(customer.id, status='Completed', completed_at=completed_at)
        old_report = IncidentReport(
            booking_id=booking.id, customer_id=customer.id, item='Door', incident_type='Damage or loss',
            description='Previously submitted damage report.', reported_by=customer.full_name,
            submitted_at=completed_at + timedelta(hours=2), evidence=[], contact_snapshot={},
        )
        db.session.add(old_report)
        db.session.commit()
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)
    monkeypatch.setattr('moving_company.routes.customer.booking_policy_service.utc_now', lambda: after_deadline)

    page = client.get(f'/customer/bookings/{booking_id}')
    assert page.status_code == 200
    assert b'Report Damage' not in page.data
    assert b'The 72-hour damage-reporting window for this booking has expired' in page.data
    report_page = client.get(f'/customer/bookings/{booking_id}/damage')
    assert report_page.status_code == 200
    assert b'contact Chigo Support' in report_page.data
    rejected = client.post(f'/customer/bookings/{booking_id}/damage', data={
        'item': 'New item', 'description': 'Late report',
    })
    assert rejected.status_code == 200
    with app.app_context():
        assert IncidentReport.query.filter_by(booking_id=booking_id).count() == 1
        assert b'Previously submitted damage report.' in client.get(f'/customer/bookings/{booking_id}').data


def test_damage_report_requires_reliable_completion_time_and_rejects_unsafe_upload(app):
    with app.app_context():
        customer = make_customer('damage-missing-time@example.com')
        booking = make_booking(customer.id, status='Completed', completed_at=None)
        booking_id, customer_id = booking.id, customer.id
    client = app.test_client()
    login(client, customer_id)
    page = client.get(f'/customer/bookings/{booking_id}/damage')
    assert b'A reliable service completion time is not recorded' in page.data
    assert b'<form method="post" enctype="multipart/form-data"' not in page.data
    with app.app_context():
        booking = Booking.query.get(booking_id)
        booking.service_completed_at = datetime.now(timezone.utc)
        db.session.commit()
    unsafe = client.post(f'/customer/bookings/{booking_id}/damage', data={
        'item': 'Wall', 'description': 'Damage evidence',
        'evidence': (BytesIO(b'not an image'), 'attack.png'),
    })
    assert unsafe.status_code == 200
    with app.app_context():
        assert IncidentReport.query.filter_by(booking_id=booking_id).count() == 0


def test_fumigation_is_separate_and_unpriced_until_review(app):
    with app.app_context():
        customer = make_customer('fumigation@example.com')
        booking = create_booking_request(
            customer_id=customer.id,
            location={'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT'},
            service_type='cleaning',
            initial_service_answers={'morph_cleaning_category': 'fumigation-pest-control'},
        )
        booking.workflow_data = {
            **booking.workflow_data,
            'property': {'service_answers': {
                'morph_cleaning_category': 'fumigation-pest-control',
                'morph_fumigation_property_type': 'house',
                'morph_fumigation_size_sqm': 120,
                'morph_fumigation_pest_type': 'termites',
                'morph_fumigation_treatment_areas': ['outdoor-areas'],
                'morph_fumigation_occupancy': 'vacant',
            }},
            'inventory_answers': {},
            'schedule': {'date': (date.today() + timedelta(days=3)).isoformat(), 'time': 'Morning'},
        }
        booking.move_date = date.today() + timedelta(days=3)
        booking.workflow_state = 'SCHEDULE_COMPLETED'
        db.session.commit()

        calculation = calculate_quote(booking)

        assert calculation['indicative_total'] is None
        assert booking.quote_status == 'Quote Required'
        assert any('not been confirmed for fumigation' in flag for flag in calculation['manual_review_flags'])
        assert calculation['pricing_rule_version'] is None
        assert validate_booking_question_data(booking)['property']['service_answers']['morph_cleaning_category'] == 'fumigation-pest-control'


def test_fumigation_entry_uses_only_its_service_assessment_questions(app):
    with app.app_context():
        customer = make_customer('fumigation-flow@example.com')
        db.session.commit()
        customer_id = customer.id
    client = app.test_client()
    login(client, customer_id)
    catalogue = client.get('/services')
    assert b'Fumigation &amp; Pest Control' in catalogue.data
    assert b'dedicated pest-treatment assessment' in catalogue.data
    chooser = client.post('/booking/start/cleaning', data={'cleaning_subtype': 'fumigation-pest-control'})
    assert chooser.status_code == 302
    location = client.post('/booking/location/cleaning', data={
        'pickup_address': '12 Yakubu Gowon Way', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
    })
    assert location.status_code == 302
    request_id = location.headers['Location'].split('/')[2]
    with app.app_context():
        booking = Booking.query.filter_by(booking_request_id=request_id).one()
        assert booking.workflow_data['property']['service_answers']['morph_cleaning_category'] == 'fumigation-pest-control'
    property_page = client.get(location.headers['Location'])
    assert property_page.status_code == 200
    assert b'Pest or infestation type' in property_page.data
    assert b'Areas requiring treatment' in property_page.data
    assert b'data-condition-value="upholstery-care" data-condition-answer="fumigation-pest-control" hidden' in property_page.data
    with app.app_context():
        booking = Booking.query.filter_by(customer_id=customer_id).one()
        assert booking.workflow_data['property']['service_answers']['morph_cleaning_category'] == 'fumigation-pest-control'


def test_admin_can_correct_missing_completion_time_with_timezone_and_audit(app):
    with app.app_context():
        admin = User(first_name='Policy', last_name='Admin', email='policy-admin@example.com', phone='', role='admin')
        admin.set_password('password123')
        customer = make_customer('completion-customer@example.com')
        db.session.add(admin)
        db.session.flush()
        booking = make_booking(customer.id, status='Completed', completed_at=None)
        admin_id, booking_id = admin.id, booking.id
    client = app.test_client()
    login(client, admin_id)

    response = client.post(f'/admin/bookings/{booking_id}/completion-time', data={
        'service_completed_at': '2026-10-10T15:30',
    })

    assert response.status_code == 302
    with app.app_context():
        booking = Booking.query.get(booking_id)
        assert booking.service_completed_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc)
        assert any(event.action == 'service.completion_time_corrected' for event in booking.events)
