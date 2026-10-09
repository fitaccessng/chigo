import hashlib
import base64
import secrets
from urllib.parse import urlsplit

from authlib.integrations.base_client.errors import OAuthError
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from authlib.common.security import generate_token

from ..extensions import db
from ..forms import LoginForm, PasswordResetForm, PasswordResetRequestForm, RegistrationForm
from ..models import Booking, OAuthIdentity, User
from .admin import has_admin_permission
from ..services.password_reset_service import generate_reset_token, send_password_reset_email, verify_reset_token
from ..services.welcome_email_service import send_welcome_email
from ..services.google_oauth_service import google_oauth

auth_bp = Blueprint('auth', __name__)


def _safe_internal_path(value):
    if not value or not value.startswith('/') or value.startswith('//') or '\\' in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.username or parsed.password:
        return None
    return value


def _resume_customer_login(user, pending_request_id, next_path=None):
    session.clear()
    login_user(user, fresh=True)
    if pending_request_id:
        booking = Booking.query.filter_by(booking_request_id=pending_request_id).first()
        if booking and booking.customer_id in (None, user.id):
            if booking.customer_id is None:
                booking.customer_id = user.id
                db.session.commit()
            session['active_booking_request_id'] = booking.booking_request_id
            return _booking_resume_url(booking)
    if next_path:
        return redirect(next_path)
    if has_admin_permission(user.role, 'dashboard'):
        return redirect(url_for('admin.dashboard'))
    return redirect(url_for('customer.dashboard'))


def _google_redirect_uri():
    return current_app.config.get('GOOGLE_REDIRECT_URI') or url_for(
        'auth.google_callback', _external=True
    )


def _google_start(flow):
    if not current_app.config.get('GOOGLE_CLIENT_ID') or not current_app.config.get('GOOGLE_CLIENT_SECRET'):
        flash('Google sign-in is not configured yet. Use email and password for now.', 'error')
        return redirect(url_for('auth.login'))

    verifier = generate_token(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode('ascii')
    session['google_pkce_verifier'] = verifier
    session['google_flow'] = flow
    session['google_next'] = _safe_internal_path(request.args.get('next'))
    if flow == 'link':
        session['google_link_user_id'] = int(session.get('_user_id'))
    return google_oauth.google.authorize_redirect(
        redirect_uri=_google_redirect_uri(),
        scope='openid email profile',
        nonce=generate_token(32),
        code_challenge=challenge,
        code_challenge_method='S256',
    )


@auth_bp.route('/google', methods=['GET'])
def google_login():
    return _google_start('login')


@auth_bp.route('/google/link', methods=['GET'])
@login_required
def google_link():
    return _google_start('link')


@auth_bp.route('/google/callback', methods=['GET'])
def google_callback():
    flow = session.pop('google_flow', 'login')
    verifier = session.pop('google_pkce_verifier', None)
    expected_link_user_id = session.pop('google_link_user_id', None)
    next_path = _safe_internal_path(session.pop('google_next', None))
    try:
        if not verifier:
            raise OAuthError(error='invalid_request')
        token = google_oauth.google.authorize_access_token(code_verifier=verifier)
        claims = token.get('userinfo')
        if not claims or not claims.get('sub') or claims.get('email_verified') not in (True, 'true', 'True'):
            raise OAuthError(error='invalid_userinfo')
        subject = str(claims['sub'])
        email = str(claims.get('email', '')).strip().lower()
        if not email or len(email) > 255 or '@' not in email:
            raise OAuthError(error='invalid_userinfo')
    except Exception:
        current_app.logger.info('Google authentication was denied or could not be validated.')
        flash('Google sign-in could not be completed. Please try again or use email and password.', 'error')
        return redirect(url_for('auth.login'))

    identity = OAuthIdentity.query.filter_by(provider='google', subject=subject).first()
    if flow == 'link':
        user_id = session.get('_user_id')
        if not user_id or str(user_id) != str(expected_link_user_id):
            flash('Your session changed during Google linking. Sign in and try again.', 'error')
            return redirect(url_for('auth.login'))
        user = db.session.get(User, int(user_id))
        if user is None or not user.is_active:
            flash('This Chigo account is unavailable. Contact support.', 'error')
            return redirect(url_for('auth.login'))
        if identity and identity.user_id != user.id:
            flash('That Google account is already linked to another Chigo account.', 'error')
            return redirect(url_for('customer.profile'))
        existing_email_user = User.query.filter(db.func.lower(User.email) == email).first()
        if existing_email_user and existing_email_user.id != user.id:
            flash('That Google email belongs to another Chigo account. Sign into that account before linking.', 'error')
            return redirect(url_for('customer.profile'))
        if not identity:
            db.session.add(OAuthIdentity(
                user_id=user.id, provider='google', subject=subject,
                email=email, email_verified=True,
            ))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash('That Google account could not be linked. Please try again.', 'error')
                return redirect(url_for('customer.profile'))
        flash('Google account linked successfully.', 'success')
        return redirect(url_for('customer.profile'))

    if identity:
        user = db.session.get(User, identity.user_id)
        if not user or not user.is_active:
            flash('This Google sign-in is unavailable. Contact support.', 'error')
            return redirect(url_for('auth.login'))
        pending_request_id = session.get('active_booking_request_id')
        return _resume_customer_login(user, pending_request_id, next_path)

    existing_user = User.query.filter(db.func.lower(User.email) == email).first()
    if existing_user:
        flash('A Chigo account already uses this email. Sign in with your password, then link Google in account settings.', 'error')
        return redirect(url_for('auth.login'))

    full_name = str(claims.get('name') or '').strip()
    given_name = str(claims.get('given_name') or '').strip()
    family_name = str(claims.get('family_name') or '').strip()
    if not given_name:
        name_parts = full_name.split(maxsplit=1)
        given_name = name_parts[0] if name_parts else email.split('@', 1)[0][:100]
        family_name = name_parts[1] if len(name_parts) > 1 else ''
    user = User(
        first_name=given_name[:100], last_name=family_name[:100],
        email=email, phone='', role='customer',
    )
    user.set_password(secrets.token_urlsafe(48))
    db.session.add(user)
    try:
        db.session.flush()
        db.session.add(OAuthIdentity(
            user_id=user.id, provider='google', subject=subject,
            email=email, email_verified=True,
        ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That Google account could not be created. Please try again or use email and password.', 'error')
        return redirect(url_for('auth.login'))
    try:
        send_welcome_email(user, url_for('customer.dashboard', _external=True))
    except Exception:
        current_app.logger.exception('Unable to send welcome email after Google registration')
    pending_request_id = session.get('active_booking_request_id')
    return _resume_customer_login(user, pending_request_id, next_path)


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
    next_path = _safe_internal_path(request.values.get('next'))
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
            if next_path:
                return redirect(next_path)
            if has_admin_permission(user.role, 'dashboard'):
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('customer.dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('auth/login.html', form=form, google_next=_safe_internal_path(request.args.get('next')))


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
        try:
            send_welcome_email(user, url_for('customer.dashboard', _external=True))
        except Exception:
            current_app.logger.exception('Unable to send welcome email')
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
    return render_template('auth/register.html', form=form, google_next=_safe_internal_path(request.args.get('next')))


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
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('public.home'))
