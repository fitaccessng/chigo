"""Add service intent to bookings.

Revision ID: 20261008_add_service_intent
Revises: 20261007_add_demo_fleet_metadata
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa


revision = '20261008_add_service_intent'
down_revision = '20261007_add_demo_fleet_metadata'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bookings', sa.Column('primary_service', sa.String(length=30), nullable=False, server_default='residential'))
    op.create_index(op.f('ix_bookings_primary_service'), 'bookings', ['primary_service'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_bookings_primary_service'), table_name='bookings')
    op.drop_column('bookings', 'primary_service')
