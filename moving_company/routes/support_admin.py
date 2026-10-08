from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import desc

from ..extensions import db
from ..models import SupportConversation, SupportRequest, User
from ..services import support_chat_service as support_chat

support_admin_bp = Blueprint('support_admin', __name__)


def _require_support_agent():
    return current_user.is_authenticated and current_user.role.lower() in {'admin', 'super_admin', 'customer_support', 'operations_manager'}


@support_admin_bp.route('/support')
def support_inbox():
    if not _require_support_agent():
        return redirect(url_for('admin.dashboard'))
    requests = SupportRequest.query.order_by(desc(SupportRequest.created_at)).all()
    conversations = SupportConversation.query.filter(
        SupportConversation.status.in_(['WAITING_FOR_AGENT', 'HUMAN_ACTIVE'])
    ).order_by(desc(SupportConversation.updated_at)).all()
    return render_template(
        'admin/support_inbox.html',
        requests=requests,
        conversations=conversations,
        can_takeover=current_user.role in {'ADMIN', 'SUPPORT'},
    )


@support_admin_bp.route('/support/<int:conversation_id>/takeover', methods=['POST'])
@support_admin_bp.route('/support/<int:conversation_id>/take', methods=['POST'])
def take_over_conversation(conversation_id):
    if not _require_support_agent():
        return jsonify({'success': False, 'error': 'Unauthorized.'}), 403
    conversation = support_chat.take_conversation(conversation_id, current_user.id)
    if conversation is None:
        return jsonify({'success': False, 'error': 'Conversation not found.'}), 404
    return redirect(url_for('support_admin.support_inbox'))


@support_admin_bp.route('/support/<int:conversation_id>/message', methods=['POST'])
@support_admin_bp.route('/support/<int:conversation_id>/messages', methods=['GET', 'POST'])
def send_admin_message(conversation_id):
    if not _require_support_agent():
        return jsonify({'success': False, 'error': 'Unauthorized.'}), 403
    if request.method == 'GET':
        conversation = SupportConversation.query.get(conversation_id)
        if conversation is None or conversation.customer_id is None:
            return jsonify({'success': False, 'error': 'Conversation not found.'}), 404
        try:
            after_id = int(request.args.get('after_id')) if request.args.get('after_id') is not None else 0
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'after_id must be an integer.'}), 400
        messages = [
            {
                'id': item.id,
                'sender_type': item.sender_type,
                'sender_id': item.sender_id,
                'message': item.message,
                'created_at': item.created_at.isoformat(),
            }
            for item in sorted(conversation.messages, key=lambda entry: entry.id)
            if item.id > after_id
        ]
        return jsonify({
            'success': True,
            'conversation_id': conversation.id,
            'status': conversation.status,
            'messages': messages,
        })

    payload = request.get_json(silent=True) or request.form
    message = (payload.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'Message is required.'}), 400
    item = support_chat.add_admin_message(conversation_id, current_user.id, message)
    if item is None:
        return jsonify({'success': False, 'error': 'Conversation not found or not active.'}), 404
    return jsonify({
        'success': True,
        'conversation_id': conversation_id,
        'message': {
            'id': item.id,
            'sender_type': item.sender_type,
            'sender_id': item.sender_id,
            'message': item.message,
            'created_at': item.created_at.isoformat(),
        },
    })


@support_admin_bp.route('/support/<int:conversation_id>/resolve', methods=['POST'])
def resolve_support(conversation_id):
    if not _require_support_agent():
        return jsonify({'success': False, 'error': 'Unauthorized.'}), 403
    support_chat.resolve_conversation(conversation_id)
    return redirect(url_for('support_admin.support_inbox'))
