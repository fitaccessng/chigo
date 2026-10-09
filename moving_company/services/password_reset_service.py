from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..extensions import db
from ..models import User
from .transactional_email_service import send_branded_email

_TOKEN_SALT = 'chigo-password-reset-v1'


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def generate_reset_token(user):
    return _serializer().dumps({'user_id': user.id, 'password_hash': user.password_hash}, salt=_TOKEN_SALT)


def verify_reset_token(token):
    try:
        payload = _serializer().loads(
            token,
            salt=_TOKEN_SALT,
            max_age=current_app.config['PASSWORD_RESET_MAX_AGE'],
        )
    except (BadSignature, SignatureExpired):
        return None

    user = db.session.get(User, payload.get('user_id'))
    if user is None or user.password_hash != payload.get('password_hash'):
        return None
    return user


def send_password_reset_email(user, reset_url):
    expires_in_minutes = current_app.config['PASSWORD_RESET_MAX_AGE'] // 60
    send_branded_email(
        recipient=user.email,
        subject='Reset your Chigo Relocations password',
        preheader='Use your secure link to choose a new password.',
        heading='Reset your password',
        paragraphs=[
            f'We received a request to reset your Chigo Relocations password. This link expires in {expires_in_minutes} minutes.',
            'If you did not request a password reset, you can ignore this email. Your password will not change.',
        ],
        action_label='Choose a new password',
        action_url=reset_url,
        metadata={'reset_url': reset_url},
    )
