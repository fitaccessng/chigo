import smtplib
from datetime import datetime
from email.message import EmailMessage

from flask import current_app


def send_human_escalation_email(request, customer_name, customer_email, customer_phone=None, admin_url=None):
    mail_server = current_app.config.get('MAIL_SERVER')
    recipients = [
        'kelvinibeh3@gmail.com',
        'admin@chigo.com',
        'support@chigo.com',
    ]
    if not mail_server:
        current_app.extensions.setdefault('outbox', []).append({
            'to': recipients,
            'subject': 'Chigo — New Human Support Request',
            'status': 'not_configured',
        })
        return False

    message = EmailMessage()
    message['From'] = current_app.config['MAIL_DEFAULT_SENDER']
    message['To'] = ', '.join(recipients)
    message['Subject'] = 'Chigo — New Human Support Request'
    message.set_content(
        f'New customer support request\n\n'
        f'Customer: {customer_name}\n'
        f'Email: {customer_email}\n'
        f'Phone: {customer_phone or "Not provided"}\n'
        f'Booking: {request.booking_id or "Not provided"}\n'
        f'Service: {request.service_type or "Not provided"}\n'
        f'Request: {request.reason}\n'
        f'Time: {datetime.utcnow().strftime("%m/%d/%Y %I:%M %p UTC")}\n'
        f'Status: {request.status}\n'
        f'Open Support Conversation: {admin_url or "Chigo admin support inbox"}'
    )
    try:
        with smtplib.SMTP(mail_server, current_app.config['MAIL_PORT'], timeout=10) as smtp:
            if current_app.config.get('MAIL_USE_TLS'):
                smtp.starttls()
            if current_app.config.get('MAIL_USERNAME') and current_app.config.get('MAIL_PASSWORD'):
                smtp.login(current_app.config['MAIL_USERNAME'], current_app.config['MAIL_PASSWORD'])
            smtp.send_message(message)
        request.email_status = 'sent'
        return True
    except Exception as error:
        current_app.logger.warning('Human support escalation email failed: %s', error)
        request.email_status = 'failed'
        return False
