"""Add customer account controls and service restrictions.

Revision ID: 20261020_customer_controls
Revises: 20261019_booking_event_idempotency
Create Date: 2026-10-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '20261020_customer_controls'
down_revision = '20261019_booking_event_idempotency'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('users', sa.Column('account_status', sa.String(length=20), nullable=False, server_default='active'))
    op.add_column('users', sa.Column('disabled_services', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.create_index('ix_users_account_status', 'users', ['account_status'])


def downgrade():
    op.drop_index('ix_users_account_status', table_name='users')
    op.drop_column('users', 'disabled_services')
    op.drop_column('users', 'account_status')
    op.drop_column('users', 'is_active')
