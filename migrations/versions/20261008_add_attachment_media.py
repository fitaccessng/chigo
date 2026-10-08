"""Add contextual booking attachments and ordered vehicle images.

Revision ID: 20261008_add_attachment_media
Revises: 20261008_add_service_intent
Create Date: 2026-10-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '20261008_add_attachment_media'
down_revision = '20261008_add_service_intent'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('booking_photos', sa.Column('booking_item_id', sa.Integer(), nullable=True))
    op.add_column('booking_photos', sa.Column('uploaded_by_id', sa.Integer(), nullable=True))
    op.add_column('booking_photos', sa.Column('original_filename', sa.String(length=255), nullable=True))
    op.add_column('booking_photos', sa.Column('mime_type', sa.String(length=120), nullable=True))
    op.add_column('booking_photos', sa.Column('file_size', sa.Integer(), nullable=True))
    op.add_column('booking_photos', sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('booking_photos', sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.execute("UPDATE booking_photos SET original_filename = file_name, mime_type = 'image/jpeg', file_size = 0")
    op.alter_column('booking_photos', 'file_name', nullable=False)
    op.alter_column('booking_photos', 'original_filename', nullable=False)
    op.alter_column('booking_photos', 'mime_type', nullable=False)
    op.alter_column('booking_photos', 'file_size', nullable=False)
    op.alter_column('booking_photos', 'category', nullable=False, server_default=sa.text("'inventory'"))
    op.alter_column('booking_photos', 'created_at', nullable=False)
    op.create_foreign_key('fk_booking_photos_booking_item_id', 'booking_photos', 'booking_items', ['booking_item_id'], ['id'])
    op.create_foreign_key('fk_booking_photos_uploaded_by_id', 'booking_photos', 'users', ['uploaded_by_id'], ['id'])
    op.create_index(op.f('ix_booking_photos_booking_item_id'), 'booking_photos', ['booking_item_id'], unique=False)
    op.create_index(op.f('ix_booking_photos_uploaded_by_id'), 'booking_photos', ['uploaded_by_id'], unique=False)

    op.create_table(
        'vehicle_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('truck_id', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=120), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['truck_id'], ['trucks.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_vehicle_images_truck_id'), 'vehicle_images', ['truck_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_vehicle_images_truck_id'), table_name='vehicle_images')
    op.drop_table('vehicle_images')
    op.drop_index(op.f('ix_booking_photos_uploaded_by_id'), table_name='booking_photos')
    op.drop_index(op.f('ix_booking_photos_booking_item_id'), table_name='booking_photos')
    op.drop_constraint('fk_booking_photos_uploaded_by_id', 'booking_photos', type_='foreignkey')
    op.drop_constraint('fk_booking_photos_booking_item_id', 'booking_photos', type_='foreignkey')
    op.drop_column('booking_photos', 'is_primary')
    op.drop_column('booking_photos', 'sort_order')
    op.drop_column('booking_photos', 'file_size')
    op.drop_column('booking_photos', 'mime_type')
    op.drop_column('booking_photos', 'original_filename')
    op.drop_column('booking_photos', 'uploaded_by_id')
    op.drop_column('booking_photos', 'booking_item_id')
