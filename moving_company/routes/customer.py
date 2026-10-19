from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import login_required, current_user

from ..extensions import db
from ..models import Booking, BookingItem, BookingPhoto, IncidentReport, Notification, Review
from ..services.booking_engine_service import record_event
from ..services import booking_policy_service
from ..services.image_upload_service import delete_image, save_image
from ..services.referral_service import record_manual_referral, referral_dashboard_data

customer_bp = Blueprint('customer', __name__)


@customer_bp.route('/dashboard')
@login_required
def dashboard():
    bookings = Booking.query.filter_by(customer_id=current_user.id).order_by(Booking.created_at.desc()).all()
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(5).all()
    return render_template('customer/dashboard.html', bookings=bookings, notifications=notifications)


@customer_bp.route('/bookings')
@login_required
def bookings():
    bookings = Booking.query.filter_by(customer_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template('customer/bookings.html', bookings=bookings)


@customer_bp.route('/referrals', methods=['GET', 'POST'])
@login_required
def referrals():
    if current_user.role != 'customer':
        abort(403)
    if request.method == 'POST':
        try:
            record_manual_referral(current_user, request.form.get('referral_code'))
            flash('Referral code applied to your account.', 'success')
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'error')
        return redirect(url_for('customer.referrals'))
    dashboard_data = referral_dashboard_data(current_user, request.args.get('page', 1, type=int))
    db.session.commit()
    return render_template('customer/referrals.html', **dashboard_data)


@customer_bp.route('/bookings/<int:booking_id>')
@login_required
def booking_detail(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    timeline_events = sorted(
        (event for event in booking.events if not event.action.startswith('analytics.')),
        key=lambda event: event.created_at,
    )
    return render_template(
        'customer/booking_detail.html', booking=booking,
        review=Review.query.filter_by(booking_id=booking.id, user_id=current_user.id).first(),
        timeline_events=timeline_events,
        damage_window=booking_policy_service.damage_report_window(booking),
        cancellation_local_at=(
            booking_policy_service.as_utc(booking.cancelled_at).astimezone(booking_policy_service.SERVICE_TIMEZONE)
            if booking.cancelled_at else None
        ),
    )


@customer_bp.route('/bookings/<int:booking_id>/cancel', methods=['GET', 'POST'])
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    preview = booking_policy_service.cancellation_preview(booking)
    if request.method == 'POST':
        if request.form.get('action') != 'confirm_cancellation':
            abort(400)
        try:
            previous = {'status': booking.status, 'workflow_state': booking.workflow_state}
            cancellation = booking_policy_service.cancel_customer_booking(
                booking, current_user.id, request.form.get('reason', ''),
            )
            from ..services.dispatch_service import release_booking_reservations
            release_booking_reservations(booking.id, 'Booking cancelled by customer.', commit=False)
            record_event(
                booking, 'customer.cancellation_confirmed', previous=previous,
                new=cancellation, user_id=current_user.id,
            )
            db.session.commit()
            flash('Your booking has been cancelled. Payment or refund review details are available below.', 'success')
            return redirect(url_for('customer.booking_detail', booking_id=booking.id))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'error')
            booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
            preview = booking_policy_service.cancellation_preview(booking)
    return render_template('customer/cancel_booking.html', booking=booking, preview=preview)


def _parse_local_datetime(raw_value):
    if not raw_value:
        return None
    try:
        value = datetime.fromisoformat(raw_value)
    except ValueError:
        raise ValueError('Enter a valid date and approximate time the damage was noticed.') from None
    local_zone = ZoneInfo('Africa/Lagos')
    if value.tzinfo is None:
        value = value.replace(tzinfo=local_zone)
    return value.astimezone(timezone.utc)


@customer_bp.route('/bookings/<int:booking_id>/damage', methods=['GET', 'POST'])
@login_required
def report_damage(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    window = booking_policy_service.damage_report_window(booking)
    if not window.get('policy_applies'):
        abort(404)
    if request.method == 'POST' and not window.get('available'):
        flash('The 72-hour damage-reporting window is not open for this booking.', 'error')
    elif request.method == 'POST':
        item = request.form.get('item', '').strip()
        description = request.form.get('description', '').strip()
        uploads = [upload for upload in request.files.getlist('evidence') if upload and upload.filename]
        if len(item) > 120 or not description or len(description) > 5000 or len(uploads) > 5:
            flash('Provide a description under 5,000 characters and no more than five supporting images.', 'error')
        else:
            stored = []
            try:
                noticed_at = _parse_local_datetime(request.form.get('noticed_at', '').strip())
                for upload in uploads:
                    stored.append(save_image(
                        upload, f'damage-reports/{booking.id}', current_app.config['MAX_IMAGE_SIZE'],
                    ))
                submitted_at = booking_policy_service.utc_now()
                current_window = booking_policy_service.damage_report_window(booking, submitted_at)
                if not current_window.get('available'):
                    raise ValueError('The 72-hour damage-reporting window has expired.')
                incident = IncidentReport(
                    booking_id=booking.id,
                    customer_id=current_user.id,
                    item=item or None,
                    incident_type='Damage or loss',
                    description=description,
                    reported_by=current_user.full_name,
                    date_time=noticed_at.replace(tzinfo=None) if noticed_at else submitted_at.replace(tzinfo=None),
                    noticed_at=noticed_at,
                    submitted_at=submitted_at,
                    contact_snapshot={
                        'name': current_user.full_name,
                        'email': current_user.email,
                        'phone': current_user.phone,
                    },
                    evidence=[{
                        'file_url': photo['file_url'],
                        'original_filename': photo['original_filename'],
                        'mime_type': photo['mime_type'],
                        'file_size': photo['file_size'],
                    } for photo in stored],
                )
                db.session.add(incident)
                db.session.flush()
                record_event(booking, 'damage.report_submitted', new={
                    'incident_id': incident.id,
                    'submitted_at': submitted_at.isoformat(timespec='seconds'),
                    'evidence_count': len(stored),
                }, user_id=current_user.id)
                db.session.commit()
                flash('Your damage report was received and will be reviewed by Chigo.', 'success')
                return redirect(url_for('customer.booking_detail', booking_id=booking.id))
            except (ValueError, OSError) as error:
                db.session.rollback()
                for photo in stored:
                    delete_image(photo.get('file_url'))
                flash(str(error), 'error')
    return render_template(
        'customer/incident.html', booking=booking, damage_report=True,
        damage_window=window,
    )


@customer_bp.route('/bookings/<int:booking_id>/attachments', methods=['POST'])
@login_required
def upload_booking_attachment(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    category = request.form.get('category', 'inventory').strip().casefold()
    allowed_categories = {'inventory', 'property', 'access', 'special_item', 'booking'}
    if category not in allowed_categories:
        flash('Choose a valid image context.', 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    item_id = request.form.get('booking_item_id', '')
    item = None
    if item_id:
        item = BookingItem.query.filter_by(id=int(item_id), booking_id=booking.id).first_or_404()
    files = request.files.getlist('attachments')
    if not files or not any(file.filename for file in files):
        flash('Choose at least one image to upload.', 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    count = BookingPhoto.query.filter_by(booking_id=booking.id, category=category).count()
    if count + len(files) > current_app.config['MAX_BOOKING_ATTACHMENTS']:
        flash('This booking has reached its image limit.', 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    try:
        for index, file_storage in enumerate(files):
            if file_storage.filename:
                saved = save_image(file_storage, 'booking', current_app.config['MAX_IMAGE_SIZE'])
                photo = BookingPhoto(
                    booking_id=booking.id,
                    booking_item_id=item.id if item else None,
                    uploaded_by_id=current_user.id,
                    file_name=saved['file_url'],
                    original_filename=saved['original_filename'],
                    mime_type=saved['mime_type'],
                    file_size=saved['file_size'],
                    category=category,
                    sort_order=count + index,
                )
                db.session.add(photo)
    except (ValueError, OSError) as error:
        db.session.rollback()
        flash(str(error), 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    db.session.commit()
    flash('Images uploaded.', 'success')
    return redirect(url_for('customer.booking_detail', booking_id=booking.id))


@customer_bp.route('/bookings/<int:booking_id>/attachments/<int:photo_id>/delete', methods=['POST'])
@login_required
def delete_booking_attachment(booking_id, photo_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    photo = BookingPhoto.query.filter_by(id=photo_id, booking_id=booking.id).first_or_404()
    delete_image(photo.file_name)
    db.session.delete(photo)
    db.session.commit()
    flash('Image removed.', 'success')
    return redirect(url_for('customer.booking_detail', booking_id=booking.id))


@customer_bp.route('/bookings/<int:booking_id>/attachments/<path:filename>')
@login_required
def serve_booking_attachment(booking_id, filename):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    photo = BookingPhoto.query.filter_by(booking_id=booking.id, file_name=f'booking/{filename}').first_or_404()
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], photo.file_name)


@customer_bp.route('/bookings/<int:booking_id>/review', methods=['GET', 'POST'])
@login_required
def submit_review(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    existing = Review.query.filter_by(booking_id=booking.id, user_id=current_user.id).first()
    if existing:
        flash('A review has already been submitted for this move.', 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    if booking.status.casefold() not in {'completed', 'delivered'}:
        flash('Reviews are available after the move is completed.', 'error')
        return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    if request.method == 'POST':
        try:
            rating = int(request.form.get('rating', ''))
        except ValueError:
            rating = 0
        comment = request.form.get('comment', '').strip()
        if rating not in range(1, 6) or len(comment) > 2000:
            flash('Choose a rating from 1 to 5 and keep your comment under 2,000 characters.', 'error')
        else:
            db.session.add(Review(booking_id=booking.id, user_id=current_user.id, rating=rating, comment=comment))
            if booking.booking_request_id:
                previous = booking.workflow_state
                booking.workflow_state = 'REVIEWED'
                record_event(booking, 'review.submitted', previous=previous,
                             new={'rating': rating, 'comment': comment}, user_id=current_user.id)
            db.session.commit()
            flash('Thank you for reviewing your move.', 'success')
            return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    return render_template('customer/review.html', booking=booking)


@customer_bp.route('/bookings/<int:booking_id>/incidents', methods=['GET', 'POST'])
@login_required
def report_incident(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    if request.method == 'POST':
        incident_type = request.form.get('incident_type', '').strip()
        description = request.form.get('description', '').strip()
        item = request.form.get('item', '').strip()
        if (
            booking_policy_service.has_accepted_policy_rule(booking, 'damage_reporting')
            and incident_type in {'Damaged item', 'Missing item', 'Damage or loss'}
        ):
            return redirect(url_for('customer.report_damage', booking_id=booking.id))
        if not incident_type or not description or len(description) > 5000 or len(item) > 120:
            flash('Provide an incident type and a description under 5,000 characters.', 'error')
        else:
            db.session.add(IncidentReport(
                booking_id=booking.id,
                customer_id=current_user.id,
                item=item or None,
                incident_type=incident_type[:80],
                description=description,
                reported_by=current_user.full_name,
                contact_snapshot={
                    'name': current_user.full_name,
                    'email': current_user.email,
                    'phone': current_user.phone,
                },
            ))
            db.session.commit()
            flash('Your incident report has been submitted.', 'success')
            return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    return render_template('customer/incident.html', booking=booking)


@customer_bp.route('/bookings/<int:booking_id>/damage-reports/<int:incident_id>/evidence/<int:index>')
@login_required
def serve_damage_evidence(booking_id, incident_id, index):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    incident = IncidentReport.query.filter_by(id=incident_id, booking_id=booking.id).first_or_404()
    evidence = incident.evidence or []
    if index < 0 or index >= len(evidence):
        abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], evidence[index]['file_url'])


@customer_bp.route('/profile')
@login_required
def profile():
    from ..models import OAuthIdentity
    google_linked = OAuthIdentity.query.filter_by(user_id=current_user.id, provider='google').first() is not None
    referral_summary = referral_dashboard_data(current_user)
    return render_template(
        'customer/profile.html',
        user=current_user,
        google_linked=google_linked,
        referral_summary=referral_summary,
    )
