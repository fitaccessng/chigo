"""Add location-learning metadata and booking location IDs.

Revision ID: 20261008_add_location_learning_metadata
Revises: 20261008_add_inventory_catalogue
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = '20261008_add_location_learning_metadata'
down_revision = '20261008_add_inventory_catalogue'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('local_places', sa.Column('city', sa.String(length=100)))
    op.add_column('local_places', sa.Column('landmark', sa.String(length=180)))
    op.add_column('local_places', sa.Column('location_type', sa.String(length=60)))
    op.add_column('local_places', sa.Column('confidence', sa.Float))
    op.add_column('local_places', sa.Column('precision_level', sa.String(length=30)))
    op.add_column('bookings', sa.Column('pickup_location_id', sa.Integer(), nullable=True))
    op.add_column('bookings', sa.Column('dropoff_location_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_bookings_pickup_location_id'), 'bookings', ['pickup_location_id'], unique=False)
    op.create_index(op.f('ix_bookings_dropoff_location_id'), 'bookings', ['dropoff_location_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_bookings_dropoff_location_id'), table_name='bookings')
    op.drop_index(op.f('ix_bookings_pickup_location_id'), table_name='bookings')
    op.drop_column('bookings', 'dropoff_location_id')
    op.drop_column('bookings', 'pickup_location_id')
    op.drop_column('local_places', 'precision_level')
    op.drop_column('local_places', 'confidence')
    op.drop_column('local_places', 'location_type')
    op.drop_column('local_places', 'landmark')
    op.drop_column('local_places', 'city')
