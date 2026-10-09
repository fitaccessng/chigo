from ..models import User
from .transactional_email_service import send_branded_email


def send_welcome_email(user: User, dashboard_url: str) -> None:
    first_name = user.first_name or 'there'
    send_branded_email(
        recipient=user.email,
        subject='Welcome to Chigo Relocations',
        preheader='Your Chigo account is ready.',
        heading=f'Welcome, {first_name}.',
        paragraphs=[
            'Your Chigo account is ready. We are glad to help make your next move simpler.',
            'You can request a moving quote, plan your services, and keep track of your booking from your account.',
        ],
        action_label='Open your account',
        action_url=dashboard_url,
    )