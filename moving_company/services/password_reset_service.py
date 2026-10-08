import smtplib
from email.message import EmailMessage

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..extensions import db
from ..models import User

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
    mail_server = current_app.config.get('MAIL_SERVER')
    if not mail_server:
        if current_app.testing:
            current_app.extensions.setdefault('outbox', []).append({'to': user.email, 'reset_url': reset_url})
            return
        raise RuntimeError('Password reset email is not configured. Set MAIL_SERVER and mail credentials.')

    message = EmailMessage()
    message['Subject'] = 'Reset your Chigo Relocations password'
    message['From'] = current_app.config['MAIL_DEFAULT_SENDER']
    message['To'] = user.email
    message.set_content(
        'We received a request to reset your Chigo Relocations password. '
        f'Use this link within {current_app.config["PASSWORD_RESET_MAX_AGE"] // 60} minutes:\n\n'
        f'{reset_url}\n\nIf you did not request this change, you can ignore this email.'
    )

    mail_port = current_app.config['MAIL_PORT']
    mail_username = current_app.config.get('MAIL_USERNAME')
    mail_password = current_app.config.get('MAIL_PASSWORD')
    if current_app.config.get('MAIL_USE_TLS'):
        with smtplib.SMTP(mail_server, mail_port, timeout=10) as smtp:
            smtp.starttls()
            if mail_username and mail_password:
                smtp.login(mail_username, mail_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP_SSL(mail_server, mail_port, timeout=10) as smtp:
            if mail_username and mail_password:
                smtp.login(mail_username, mail_password)
            smtp.send_message(message)
