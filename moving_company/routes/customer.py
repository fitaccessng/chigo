from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import login_required, current_user

from ..extensions import db
from ..models import Booking, BookingItem, BookingPhoto, IncidentReport, Notification, Review
from ..services.booking_engine_service import record_event
from ..services.image_upload_service import delete_image, save_image

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


@customer_bp.route('/bookings/<int:booking_id>')
@login_required
def booking_detail(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    timeline_events = sorted(booking.events, key=lambda event: event.created_at)
    return render_template(
        'customer/booking_detail.html', booking=booking,
        review=Review.query.filter_by(booking_id=booking.id, user_id=current_user.id).first(),
        timeline_events=timeline_events,
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
        if not incident_type or not description or len(description) > 5000 or len(item) > 120:
            flash('Provide an incident type and a description under 5,000 characters.', 'error')
        else:
            db.session.add(IncidentReport(
                booking_id=booking.id,
                item=item or None,
                incident_type=incident_type[:80],
                description=description,
                reported_by=current_user.full_name,
            ))
            db.session.commit()
            flash('Your incident report has been submitted.', 'success')
            return redirect(url_for('customer.booking_detail', booking_id=booking.id))
    return render_template('customer/incident.html', booking=booking)


@customer_bp.route('/profile')
@login_required
def profile():
    from ..models import OAuthIdentity
    google_linked = OAuthIdentity.query.filter_by(user_id=current_user.id, provider='google').first() is not None
    return render_template('customer/profile.html', user=current_user, google_linked=google_linked)
