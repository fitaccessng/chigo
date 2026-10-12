"""Add persistent Home Decor requests and private attachments.

Revision ID: 20261012_home_decor_requests
Revises: 20261011_service_pricing_approval
Create Date: 2026-10-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '20261012_home_decor_requests'
down_revision = '20261011_service_pricing_approval'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'home_decor_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('public_reference', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('draft_step', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('draft_data', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('status', sa.String(length=40), nullable=False, server_default=sa.text("'Draft'")),
        sa.Column('assigned_staff_id', sa.Integer(), nullable=True),
        sa.Column('internal_notes', sa.Text(), nullable=True),
        sa.Column('inspection_at', sa.DateTime(), nullable=True),
        sa.Column('consultation_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['customer_id'], ['users.id']),
        sa.ForeignKeyConstraint(['assigned_staff_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id'),
        sa.UniqueConstraint('public_reference'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_home_decor_requests_customer_id', 'home_decor_requests', ['customer_id'])
    op.create_index('ix_home_decor_requests_public_reference', 'home_decor_requests', ['public_reference'])
    op.create_index('ix_home_decor_requests_idempotency_key', 'home_decor_requests', ['idempotency_key'])
    op.create_index('ix_home_decor_requests_status', 'home_decor_requests', ['status'])
    op.create_index('ix_home_decor_requests_assigned_staff_id', 'home_decor_requests', ['assigned_staff_id'])

    op.create_table(
        'home_decor_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=120), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['request_id'], ['home_decor_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key'),
    )
    op.create_index('ix_home_decor_attachments_request_id', 'home_decor_attachments', ['request_id'])
    op.create_index('ix_home_decor_attachments_uploaded_by_id', 'home_decor_attachments', ['uploaded_by_id'])


def downgrade():
    op.drop_index('ix_home_decor_attachments_uploaded_by_id', table_name='home_decor_attachments')
    op.drop_index('ix_home_decor_attachments_request_id', table_name='home_decor_attachments')
    op.drop_table('home_decor_attachments')
    op.drop_index('ix_home_decor_requests_assigned_staff_id', table_name='home_decor_requests')
    op.drop_index('ix_home_decor_requests_status', table_name='home_decor_requests')
    op.drop_index('ix_home_decor_requests_idempotency_key', table_name='home_decor_requests')
    op.drop_index('ix_home_decor_requests_public_reference', table_name='home_decor_requests')
    op.drop_index('ix_home_decor_requests_customer_id', table_name='home_decor_requests')
    op.drop_table('home_decor_requests')
