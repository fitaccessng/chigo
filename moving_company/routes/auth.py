from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import login_required, login_user, logout_user

from ..extensions import db
from ..forms import LoginForm, PasswordResetForm, PasswordResetRequestForm, RegistrationForm
from ..models import Booking, User
from .admin import has_admin_permission
from ..services.password_reset_service import generate_reset_token, send_password_reset_email, verify_reset_token

auth_bp = Blueprint('auth', __name__)


def _claim_active_booking(user):
    request_id = session.get('active_booking_request_id')
    if not request_id:
        return None
    booking = Booking.query.filter_by(booking_request_id=request_id, customer_id=None).first()
    if booking:
        booking.customer_id = user.id
        db.session.commit()
        return booking
    return None


def _booking_resume_url(booking):
    next_stage = {
        'LOCATION_COMPLETED': 'property', 'PROPERTY_COMPLETED': 'inventory',
        'INVENTORY_COMPLETED': 'services', 'SERVICES_COMPLETED': 'schedule',
        'SCHEDULE_COMPLETED': 'quote', 'QUOTE_READY': 'quote',
        'QUOTE_ACCEPTED': 'payment', 'PAYMENT_PENDING': 'payment',
        'CONFIRMED': 'payment', 'COMPLETED': 'review', 'REVIEWED': 'review',
    }.get(booking.workflow_state, 'property')
    return redirect(url_for('booking.workflow', request_id=booking.booking_request_id, stage=next_stage))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.check_password(form.password.data):
            pending_request_id = session.get('active_booking_request_id')
            session.clear()
            login_user(user)
            if pending_request_id:
                booking = Booking.query.filter_by(booking_request_id=pending_request_id, customer_id=None).first()
                if booking:
                    booking.customer_id = user.id
                    db.session.commit()
                    session['active_booking_request_id'] = booking.booking_request_id
                    flash('Welcome back.', 'success')
                    return _booking_resume_url(booking)
            flash('Welcome back.', 'success')
            if has_admin_permission(user.role, 'dashboard'):
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('customer.dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return render_template('auth/register.html', form=form)
        pending_request_id = session.get('active_booking_request_id')
        name_parts = form.full_name.data.strip().split(maxsplit=1)
        user = User(
            first_name=name_parts[0],
            last_name=name_parts[1] if len(name_parts) > 1 else '',
            email=email,
            phone='',
            role='customer',
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        session.clear()
        login_user(user)
        if pending_request_id:
            booking = Booking.query.filter_by(booking_request_id=pending_request_id, customer_id=None).first()
            if booking:
                booking.customer_id = user.id
                db.session.commit()
                session['active_booking_request_id'] = booking.booking_request_id
                flash('Your account has been created.', 'success')
                return _booking_resume_url(booking)
        flash('Your account has been created.', 'success')
        return redirect(url_for('customer.dashboard'))
    return render_template('auth/register.html', form=form)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def request_password_reset():
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user:
            try:
                token = generate_reset_token(user)
                send_password_reset_email(user, url_for('auth.reset_password', token=token, _external=True))
            except Exception:
                current_app.logger.exception('Unable to send password reset email')
        flash('If an account exists for that email, a password reset link has been sent.', 'success')
        return redirect(url_for('auth.request_password_reset'))
    return render_template('auth/forgot_password.html', form=form)


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = verify_reset_token(token)
    if user is None:
        flash('That password reset link is invalid or has expired. Request a new one.', 'error')
        return redirect(url_for('auth.request_password_reset'))
    form = PasswordResetForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated. Log in with your new password.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('public.home'))
