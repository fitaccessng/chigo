"""Add a reusable inventory catalogue and booking selections.

Revision ID: 20261008_add_inventory_catalogue
Revises: 20261008_add_attachment_media
Create Date: 2026-10-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '20261008_add_inventory_catalogue'
down_revision = '20261008_add_attachment_media'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'inventory_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=60), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('image', sa.String(length=255), nullable=True),
        sa.Column('estimated_volume_m3', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('estimated_weight_kg', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('dimensions_cm', sa.JSON(), nullable=True),
        sa.Column('aliases', sa.Text(), nullable=True),
        sa.Column('fragile', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('special_handling', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('vehicle_compatibility', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index(op.f('ix_inventory_items_category_active'), 'inventory_items', ['category', 'active'], unique=False)
    op.create_index(op.f('ix_inventory_items_active_name'), 'inventory_items', ['active', 'name'], unique=False)

    op.create_table(
        'booking_inventory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=True),
        sa.Column('custom_name', sa.String(length=120), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('size', sa.String(length=20), nullable=False, server_default=sa.text("'medium'")),
        sa.Column('estimated_volume_m3', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('estimated_weight_kg', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('category', sa.String(length=60), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('fragile', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('large', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('special_handling', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('dimensions_cm', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id', 'inventory_item_id', 'custom_name'),
    )
    op.create_index(op.f('ix_booking_inventory_booking_id'), 'booking_inventory', ['booking_id'], unique=False)
    op.create_index(op.f('ix_booking_inventory_inventory_item_id'), 'booking_inventory', ['inventory_item_id'], unique=False)

    op.add_column('booking_photos', sa.Column('booking_inventory_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_booking_photos_booking_inventory_id', 'booking_photos', 'booking_inventory',
        ['booking_inventory_id'], ['id'],
    )
    op.create_index(op.f('ix_booking_photos_booking_inventory_id'), 'booking_photos', ['booking_inventory_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_booking_photos_booking_inventory_id'), table_name='booking_photos')
    op.drop_constraint('fk_booking_photos_booking_inventory_id', 'booking_photos', type_='foreignkey')
    op.drop_column('booking_photos', 'booking_inventory_id')
    op.drop_index(op.f('ix_booking_inventory_inventory_item_id'), table_name='booking_inventory')
    op.drop_index(op.f('ix_booking_inventory_booking_id'), table_name='booking_inventory')
    op.drop_table('booking_inventory')
    op.drop_index(op.f('ix_inventory_items_active_name'), table_name='inventory_items')
    op.drop_index(op.f('ix_inventory_items_category_active'), table_name='inventory_items')
    op.drop_table('inventory_items')
