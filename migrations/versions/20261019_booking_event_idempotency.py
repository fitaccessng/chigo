"""Add unique idempotency keys to booking events.

Revision ID: 20261019_booking_event_idempotency
Revises: 20261018_dispatch_reservations
Create Date: 2026-10-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '20261019_booking_event_idempotency'
down_revision = '20261018_dispatch_reservations'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('booking_events', sa.Column('idempotency_key', sa.String(length=160), nullable=True))
    op.create_unique_constraint(
        'uq_booking_events_idempotency_key', 'booking_events', ['idempotency_key'],
    )


def downgrade():
    op.drop_constraint('uq_booking_events_idempotency_key', 'booking_events', type_='unique')
    op.drop_column('booking_events', 'idempotency_key')