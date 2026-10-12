"""Allow bookings to store only service-required locations.

Revision ID: 20261011_service_aware_locations
Revises: 20261010_customer_referrals
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa


revision = '20261011_service_aware_locations'
down_revision = '20261010_customer_referrals'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bookings') as batch_op:
        for column, length in (
            ('pickup_address', 255), ('pickup_city', 100), ('pickup_state', 100),
            ('destination_address', 255), ('destination_city', 100), ('destination_state', 100),
        ):
            batch_op.alter_column(column, existing_type=sa.String(length=length), nullable=True)


def downgrade():
    with op.batch_alter_table('bookings') as batch_op:
        for column, length in (
            ('pickup_address', 255), ('pickup_city', 100), ('pickup_state', 100),
            ('destination_address', 255), ('destination_city', 100), ('destination_state', 100),
        ):
            batch_op.alter_column(column, existing_type=sa.String(length=length), nullable=False)