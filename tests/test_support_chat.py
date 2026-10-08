import pytest

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import SupportConversation, SupportMessage, SupportRequest, User


@pytest.fixture
def app():
    app = create_app(testing=True)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        db.create_all()
        customer = User(
            first_name='Kelvin', last_name='Ibeh', email='kelvinibeh3@gmail.com',
            phone='+2348000000000', role='customer',
        )
        customer.set_password('password123')
        admin = User(
            first_name='Support', last_name='Agent', email='support-agent@chigo.com',
            phone='', role='customer_support',
        )
        admin.set_password('password123')
        db.session.add_all([customer, admin])
        db.session.commit()
        yield app
        db.session.remove()


def login(client, email):
    return client.post('/auth/login', data={'email': email, 'password': 'password123'})


def test_support_chat_answers_from_approved_knowledge_and_supports_escalation(app):
    client = app.test_client()
    assert login(client, 'kelvinibeh3@gmail.com').status_code == 302

    created = client.post('/support/chat/conversations', json={
        'name': 'Kelvin Ibeh', 'service_type': 'residential',
        'booking_id': None,
    })
    assert created.status_code == 201
    conversation_id = created.get_json()['conversation_id']
    conversation = SupportConversation.query.get(conversation_id)
    customer = User.query.filter_by(email='kelvinibeh3@gmail.com').one()
    assert conversation.status == 'AI_ASSISTED'
    assert conversation.customer_id == customer.id

    answered = client.post(
        f'/support/chat/{conversation_id}/messages',
        json={'message': 'What services does Chigo offer?'},
    )
    assert answered.status_code == 200
    payload = answered.get_json()
    assert payload['status'] == 'answered'
    assert payload['customer_message'] == 'What services does Chigo offer?'
    assert 'packing' in payload['message'].lower()
    assert 'storage' in payload['message'].lower()
    assert 'logistics' in payload['message'].lower()
    stored_messages = {
        message.sender_type: message.message
        for message in SupportMessage.query.filter_by(conversation_id=conversation_id)
    }
    assert stored_messages['customer'] == 'What services does Chigo offer?'
    assert 'packing' in stored_messages['ai'].lower()

    escalated = client.post(
        f'/support/chat/{conversation_id}/human-request',
        json={'reason': 'I want to speak to a human'},
    )
    assert escalated.status_code == 200
    assert escalated.get_json()['status'] == 'WAITING_FOR_AGENT'
    request = SupportRequest.query.filter_by(conversation_id=conversation_id).one()
    assert request.status == 'WAITING_FOR_AGENT'
    assert request.reason == 'I want to speak to a human'
    assert conversation.human_requested is True
    assert conversation.status == 'WAITING_FOR_AGENT'

    admin_client = app.test_client()
    assert login(admin_client, 'support-agent@chigo.com').status_code == 302
    dashboard = admin_client.get('/admin/dashboard')
    assert dashboard.status_code == 200
    assert b'New Human Support Request' in dashboard.data

    taken = admin_client.post(f'/admin/support/{conversation_id}/take')
    assert taken.status_code == 302
    conversation = SupportConversation.query.get(conversation_id)
    agent = User.query.filter_by(email='support-agent@chigo.com').one()
    assert conversation.status == 'HUMAN_ACTIVE'
    assert conversation.assigned_agent_id == agent.id

    message = admin_client.post(
        f'/admin/support/{conversation_id}/messages',
        json={'message': 'A human agent joined this conversation.'},
    )
    assert message.status_code == 200
    payload = message.get_json()
    assert payload['success'] is True
    assert payload['message']['sender_type'] == 'admin'
    assert payload['message']['message'] == 'A human agent joined this conversation.'
    assert SupportMessage.query.filter_by(conversation_id=conversation_id, sender_type='admin').count() == 1

    admin_history = admin_client.get(f'/admin/support/{conversation_id}/messages')
    assert admin_history.status_code == 200
    assert any(
        item['id'] == payload['message']['id']
        for item in admin_history.get_json()['messages']
    )

    customer_client = app.test_client()
    assert login(customer_client, 'kelvinibeh3@gmail.com').status_code == 302
    history = customer_client.get(f'/support/chat/{conversation_id}/messages')
    assert any(
        'A human agent joined this conversation.' in item['message']
        for item in history.get_json()['messages']
    )
