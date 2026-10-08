from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..extensions import db
from ..models import SupportConversation
from ..services import support_chat_service as support_chat

support_bp = Blueprint('support_chat', __name__)


@support_bp.route('/support/chat/conversations', methods=['POST'])
@login_required
def create_support_conversation():
    payload = request.get_json(silent=True) or {}
    booking_id = payload.get('booking_id')
    if booking_id not in (None, ''):
        try:
            booking_id = int(booking_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Booking ID must be an integer.'}), 400
    conversation = support_chat.create_conversation(
        customer_id=current_user.id,
        booking_id=booking_id,
        service_type=payload.get('service_type'),
    )
    return jsonify({
        'success': True,
        'conversation_id': conversation.id,
        'status': conversation.status,
        'messages': [
            {
                'id': message.id,
                'sender_type': message.sender_type,
                'sender_id': message.sender_id,
                'message': message.message,
                'created_at': message.created_at.isoformat(),
            }
            for message in conversation.messages
        ],
    }), 201


@support_bp.route('/support/chat/<int:conversation_id>/messages', methods=['GET', 'POST'])
@login_required
def support_messages(conversation_id):
    if request.method == 'GET':
        after_id = request.args.get('after_id')
        try:
            after_id = int(after_id) if after_id is not None else None
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'after_id must be an integer.'}), 400
        messages = support_chat.get_conversation_messages(conversation_id, current_user.id, after_id)
        if messages is None:
            return jsonify({'success': False, 'error': 'Conversation not found.'}), 404
        conversation = SupportConversation.query.filter_by(id=conversation_id, customer_id=current_user.id).first()
        return jsonify({
            'success': True,
            'messages': messages,
            'status': conversation.status if conversation else None,
        })

    payload = request.get_json(silent=True) or {}
    message = payload.get('message', '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'Message is required.'}), 400
    result = support_chat.respond_to_message(conversation_id, message, current_user.id)
    if result is None:
        return jsonify({'success': False, 'error': 'Conversation not found or currently handled by a human.'}), 404
    return jsonify(result)


@support_bp.route('/support/chat/<int:conversation_id>/human-request', methods=['POST'])
@login_required
def human_support_request(conversation_id):
    payload = request.get_json(silent=True) or {}
    result = support_chat.request_human(
        conversation_id,
        current_user.id,
        payload.get('reason', 'Customer requested a human agent'),
    )
    if result is None:
        return jsonify({'success': False, 'error': 'Conversation not found.'}), 404
    return jsonify({
        'success': True,
        'status': result.status,
        'message': "Sure. I'll notify our support team and connect you with a human agent. Please stay in this chat.",
        'conversation_status': result.conversation.status if hasattr(result, 'conversation') else 'WAITING_FOR_AGENT',
    })
