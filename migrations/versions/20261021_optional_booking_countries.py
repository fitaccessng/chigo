"""Allow unknown booking location countries without fabricated defaults.

Revision ID: 20261021_optional_booking_countries
Revises: 20261020_customer_controls
Create Date: 2026-10-21 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '20261021_optional_booking_countries'
down_revision = '20261020_customer_controls'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bookings') as batch_op:
        batch_op.alter_column(
            'pickup_country', existing_type=sa.String(length=100), nullable=True,
        )
        batch_op.alter_column(
            'destination_country', existing_type=sa.String(length=100), nullable=True,
        )


def downgrade():
    raise RuntimeError(
        'This migration is intentionally irreversible: unknown location countries '
        'must not be replaced with fabricated values during downgrade.'
    )
