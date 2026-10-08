"""Add booking route distance metadata.

Revision ID: 20261008_add_booking_route_metadata
Revises: 20261008_add_location_learning_metadata
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = '20261008_add_booking_route_metadata'
down_revision = '20261008_add_location_learning_metadata'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bookings', sa.Column('distance_source', sa.String(length=80), nullable=True))
    op.add_column('bookings', sa.Column('distance_precision', sa.String(length=30), nullable=True))


def downgrade():
    op.drop_column('bookings', 'distance_precision')
    op.drop_column('bookings', 'distance_source')
