"""Add persisted customer support conversations and escalation requests.

Revision ID: 20261008_add_support_chat
Revises: 20261008_add_booking_route_metadata
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = '20261008_add_support_chat'
down_revision = '20261008_add_booking_route_metadata'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'support_conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=True),
        sa.Column('assigned_agent_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('human_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('human_requested_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['users.id']),
        sa.ForeignKeyConstraint(['assigned_agent_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'support_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('sender_type', sa.String(length=20), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['support_conversations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'support_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('email_status', sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['support_conversations.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_support_conversations_customer_id'), 'support_conversations', ['customer_id'], unique=False)
    op.create_index(op.f('ix_support_conversations_status'), 'support_conversations', ['status'], unique=False)
    op.create_index(op.f('ix_support_messages_conversation_id'), 'support_messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_support_requests_status'), 'support_requests', ['status'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_support_requests_status'), table_name='support_requests')
    op.drop_index(op.f('ix_support_messages_conversation_id'), table_name='support_messages')
    op.drop_index(op.f('ix_support_conversations_status'), table_name='support_conversations')
    op.drop_index(op.f('ix_support_conversations_customer_id'), table_name='support_conversations')
    op.drop_table('support_requests')
    op.drop_table('support_messages')
    op.drop_table('support_conversations')
