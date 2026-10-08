from __future__ import annotations

from datetime import datetime

from flask import current_app
from sqlalchemy import desc

from ..extensions import db
from ..models import Booking, LocalPlace, SupportConversation, SupportMessage, SupportRequest, User
from .support_email_service import send_human_escalation_email


SUPPORT_KNOWLEDGE = {
    'chigo': (
        'Chigo is a relocation and moving platform for residential, commercial, packing, storage, '
        'and logistics needs. It coordinates location selection, inventory, service choices, quotes, '
        'scheduling, payment, and move execution.'
    ),
    'residential': (
        'Residential moves can include furniture, electronics, appliances, fragile items, heavy items, '
        'oversized items, and household essentials. Additional services may include packing, loading, '
        'unloading, cleaning, and special handling.'
    ),
    'commercial': (
        'Commercial relocation can include office equipment, furniture, electronics, appliances, '
        'storage, and construction equipment. Chigo supports commercial move planning and coordination.'
    ),
    'packing': (
        'Packing is a service option for household and commercial moves. Packing requirements and the '
        'items to be packed can be selected during the booking process.'
    ),
    'storage': (
        'Storage is available as a selected service. The exact storage arrangement, duration, and rate '
        'depend on the booking and the applicable pricing rules.'
    ),
    'logistics': (
        'Logistics covers planning and coordination for relocation services, including location selection, '
        'inventory, vehicle selection, scheduling, and delivery planning.'
    ),
    'location': (
        'Chigo uses customer-entered addresses, local place records, and supported Abuja/FCT locations. '
        'Location selection is saved with the booking so the route and service area can be resolved.'
    ),
    'inventory': (
        'Inventory is selected by category and item. Customers can add furniture, electronics, appliances, '
        'fragile, heavy, oversized, office, and construction items, with quantities and details where needed.'
    ),
    'vehicle': (
        'Vehicle recommendations are based on the move size, inventory, service requirements, and available '
        'fleet. Chigo does not guarantee a specific vehicle until a booking has been confirmed.'
    ),
    'quote': (
        'Quotes may depend on distance, inventory, vehicle type, number of movers, property and access '
        'conditions, packing requirements, special handling, and selected services. Chigo does not invent '
        'prices; a confirmed quote must be recorded in the booking.'
    ),
    'schedule': (
        'Scheduling uses the selected move date, preferred time, service area, property access, and route '
        'status. A booking can be modified before the move is completed, subject to the applicable policy.'
    ),
    'payment': (
        'Payment is completed through the configured payment provider and must be confirmed by the backend. '
        'A payment status is not a promise that a move has started or completed.'
    ),
    'cancel': (
        'A booking can be cancelled through the supported booking workflow. Cancellation timing and any '
        'charges depend on the booking status and applicable Chigo policy.'
    ),
    'modify': (
        'Bookings can be modified when the workflow and booking state permit it. Contact support if your '
        'requested change is not available in the booking interface.'
    ),
    'support': (
        'Customers can ask support questions in this chat, request a human agent, or contact Chigo support '
        'through the customer account and admin workflows.'
    ),
}


def _serialize_context(conversation):
    context = {
        'booking_id': conversation.booking_id,
        'service_type': None,
        'property_type': None,
        'pickup_location': None,
        'dropoff_location': None,
        'inventory': [],
        'selected_services': [],
        'schedule': None,
        'quote_status': None,
        'payment_status': None,
    }
    if conversation.booking_id:
        booking = Booking.query.get(conversation.booking_id)
        if booking:
            context.update({
                'service_type': booking.primary_service,
                'property_type': booking.property_type,
                'pickup_location': booking.pickup_address,
                'dropoff_location': booking.destination_address,
                'inventory': [item.item_description for item in booking.items] if hasattr(booking, 'items') else [],
                'selected_services': [booking.primary_service],
                'schedule': booking.preferred_time,
                'quote_status': booking.quote_status,
                'payment_status': booking.payment_status,
            })
    return context


def _answer_for_question(question, context):
    normalized = question.casefold()
    if any(term in normalized for term in ('human', 'agent', 'person', 'someone')):
        return 'Sure. I can connect you with a Chigo support agent. Please stay in this chat while I create your human support request.'
    if any(term in normalized for term in ('what is chigo', 'what does chigo', 'about chigo')):
        return SUPPORT_KNOWLEDGE['chigo']
    if any(term in normalized for term in ('service', 'offer', 'provide')):
        return SUPPORT_KNOWLEDGE['chigo'] + ' ' + SUPPORT_KNOWLEDGE['residential'] + ' ' + SUPPORT_KNOWLEDGE['commercial']
    if 'residential' in normalized:
        return SUPPORT_KNOWLEDGE['residential']
    if 'commercial' in normalized:
        return SUPPORT_KNOWLEDGE['commercial']
    if any(term in normalized for term in ('packing', 'pack')):
        return SUPPORT_KNOWLEDGE['packing']
    if 'storage' in normalized:
        return SUPPORT_KNOWLEDGE['storage']
    if 'logistics' in normalized:
        return SUPPORT_KNOWLEDGE['logistics']
    if any(term in normalized for term in ('location', 'address', 'abuja', 'fct')):
        return SUPPORT_KNOWLEDGE['location']
    if 'inventory' in normalized:
        return SUPPORT_KNOWLEDGE['inventory']
    if 'vehicle' in normalized or 'truck' in normalized:
        return SUPPORT_KNOWLEDGE['vehicle']
    if any(term in normalized for term in ('price', 'cost', 'quote', 'how much')):
        return SUPPORT_KNOWLEDGE['quote']
    if 'schedule' in normalized or 'date' in normalized:
        return SUPPORT_KNOWLEDGE['schedule']
    if 'payment' in normalized:
        return SUPPORT_KNOWLEDGE['payment']
    if any(term in normalized for term in ('cancel', 'cancelled', 'cancellation')):
        return SUPPORT_KNOWLEDGE['cancel']
    if 'modify' in normalized or 'change' in normalized:
        return SUPPORT_KNOWLEDGE['modify']
    if 'support' in normalized or 'help' in normalized:
        return SUPPORT_KNOWLEDGE['support']
    return (
        'I do not have enough information to confirm that. I can connect you with a Chigo support agent. '
        'Please keep this chat open and request a human if you need a confirmed answer.'
    )


def respond_to_message(conversation_id, message, customer_id):
    conversation = SupportConversation.query.filter_by(id=conversation_id, customer_id=customer_id).first()
    if conversation is None:
        return None
    if conversation.status == 'HUMAN_ACTIVE':
        return None
    if conversation.status == 'RESOLVED':
        return None
    customer_message = SupportMessage(
        conversation_id=conversation_id,
        sender_type='customer', sender_id=customer_id,
        message=message[:5000],
    )
    db.session.add(customer_message)
    answer = _answer_for_question(message, _serialize_context(conversation))
    ai_message = SupportMessage(
        conversation_id=conversation_id,
        sender_type='ai', sender_id=None,
        message=answer,
    )
    db.session.add(ai_message)
    conversation.updated_at = datetime.utcnow()
    if conversation.status == 'AI_ASSISTED':
        conversation.status = 'AI_ASSISTED'
    db.session.commit()
    return {
        'status': 'answered',
        'customer_message': message,
        'customer_message_id': customer_message.id,
        'message': answer,
        'message_id': ai_message.id,
        'conversation_status': conversation.status,
    }


def request_human(conversation_id, customer_id, reason):
    conversation = SupportConversation.query.filter_by(id=conversation_id, customer_id=customer_id).first()
    if conversation is None:
        return None
    reason = reason.strip()[:500] or 'Customer requested a human agent'
    request = SupportRequest(
        conversation_id=conversation_id,
        customer_id=customer_id,
        booking_id=conversation.booking_id,
        reason=reason,
        status='WAITING_FOR_AGENT',
        created_at=datetime.utcnow(),
    )
    conversation.human_requested = True
    conversation.human_requested_at = datetime.utcnow()
    conversation.status = 'WAITING_FOR_AGENT'
    conversation.updated_at = datetime.utcnow()
    db.session.add(request)
    db.session.commit()
    customer = conversation.customer
    send_human_escalation_email(
        request,
        customer.full_name if customer else 'Chigo customer',
        customer.email if customer else '',
        customer.phone if customer else None,
        current_app.config.get('ADMIN_SUPPORT_URL') or '/admin/support',
    )
    return request


def create_conversation(customer_id, booking_id=None, service_type=None):
    conversation = SupportConversation(
        customer_id=customer_id,
        booking_id=booking_id,
        status='AI_ASSISTED',
        human_requested=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(conversation)
    db.session.flush()
    welcome = SupportMessage(
        conversation_id=conversation.id,
        sender_type='ai', sender_id=None,
        message=(
            'Hi! I’m Chigo’s AI assistant. I’m here to help with Chigo services, bookings, moving, '
            'pricing, locations, and support. I can also connect you with a human agent.'
        ),
    )
    db.session.add(welcome)
    db.session.commit()
    return conversation


def get_conversation_messages(conversation_id, customer_id, after_id=None):
    conversation = SupportConversation.query.filter_by(id=conversation_id, customer_id=customer_id).first()
    if conversation is None:
        return None
    messages = conversation.messages
    if after_id is not None:
        messages = [message for message in messages if message.id > after_id]
    return [
        {
            'id': message.id,
            'sender_type': message.sender_type,
            'sender_id': message.sender_id,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
        }
        for message in sorted(messages, key=lambda item: item.id)
    ]


def take_conversation(conversation_id, agent_id):
    conversation = SupportConversation.query.get(conversation_id)
    if conversation is None or conversation.status == 'RESOLVED':
        return None
    conversation.status = 'HUMAN_ACTIVE'
    conversation.assigned_agent_id = agent_id
    conversation.updated_at = datetime.utcnow()
    request = SupportRequest.query.filter_by(conversation_id=conversation_id).order_by(desc(SupportRequest.created_at)).first()
    if request:
        request.status = 'HUMAN_ACTIVE'
        request.assigned_at = datetime.utcnow()
    db.session.add(SupportMessage(
        conversation_id=conversation_id,
        sender_type='system', sender_id=agent_id,
        message='A Chigo support agent has joined the conversation.',
    ))
    db.session.commit()
    return conversation


def add_admin_message(conversation_id, agent_id, message):
    conversation = SupportConversation.query.get(conversation_id)
    if conversation is None or conversation.status != 'HUMAN_ACTIVE':
        return None
    item = SupportMessage(
        conversation_id=conversation_id,
        sender_type='admin', sender_id=agent_id,
        message=message[:5000],
    )
    db.session.add(item)
    conversation.updated_at = datetime.utcnow()
    db.session.commit()
    return item


def resolve_conversation(conversation_id):
    conversation = SupportConversation.query.get(conversation_id)
    if conversation is None:
        return None
    conversation.status = 'RESOLVED'
    conversation.closed_at = datetime.utcnow()
    conversation.updated_at = datetime.utcnow()
    request = SupportRequest.query.filter_by(conversation_id=conversation_id).order_by(desc(SupportRequest.created_at)).first()
    if request:
        request.status = 'RESOLVED'
        request.resolved_at = datetime.utcnow()
    db.session.commit()
    return conversation
