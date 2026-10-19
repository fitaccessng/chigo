"""Add cancellation metadata, service completion time, and damage report evidence.

Revision ID: 20261016_booking_cancel_damage
Revises: 20261012_home_decor_requests
Create Date: 2026-10-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261016_booking_cancel_damage'
down_revision = '20261012_home_decor_requests'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bookings', sa.Column('cancellation_data', sa.JSON(), nullable=False,
                                        server_default=sa.text("'{}'")))
    op.add_column('bookings', sa.Column('service_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bookings', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('incident_reports') as batch_op:
        batch_op.add_column(sa.Column('customer_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('evidence', sa.JSON(), nullable=False,
                                      server_default=sa.text("'[]'")))
        batch_op.add_column(sa.Column('contact_snapshot', sa.JSON(), nullable=False,
                                      server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('noticed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index('ix_incident_reports_customer_id', ['customer_id'])
        batch_op.create_foreign_key(
            'fk_incident_reports_customer_id_users', 'users', ['customer_id'], ['id'],
        )
    op.execute(sa.text('UPDATE incident_reports SET submitted_at = COALESCE(date_time, CURRENT_TIMESTAMP) WHERE submitted_at IS NULL'))
    with op.batch_alter_table('incident_reports') as batch_op:
        batch_op.alter_column('submitted_at', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade():
    with op.batch_alter_table('incident_reports') as batch_op:
        batch_op.drop_constraint('fk_incident_reports_customer_id_users', type_='foreignkey')
        batch_op.drop_index('ix_incident_reports_customer_id')
        batch_op.drop_column('submitted_at')
        batch_op.drop_column('noticed_at')
        batch_op.drop_column('contact_snapshot')
        batch_op.drop_column('evidence')
        batch_op.drop_column('customer_id')
    op.drop_column('bookings', 'cancelled_at')
    op.drop_column('bookings', 'service_completed_at')
    op.drop_column('bookings', 'cancellation_data')