"""Add durable dispatch offers and conflict-safe date reservations.

Revision ID: 20261018_dispatch_reservations
Revises: 20261016_booking_cancel_damage
Create Date: 2026-10-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261018_dispatch_reservations'
down_revision = '20261016_booking_cancel_damage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'truck_partners',
        sa.Column('service_types', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    op.create_table(
        'ai_usage_counters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'usage_date', name='uq_ai_usage_user_date'),
    )
    op.create_index('ix_ai_usage_counters_user_id', 'ai_usage_counters', ['user_id'])
    op.create_index('ix_ai_usage_counters_usage_date', 'ai_usage_counters', ['usage_date'])
    op.add_column('booking_assignments', sa.Column('partner_id', sa.Integer(), nullable=True))
    op.add_column('booking_assignments', sa.Column('partner_vehicle_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('booking_assignments') as batch_op:
        batch_op.create_foreign_key('fk_booking_assignments_partner_id', 'truck_partners', ['partner_id'], ['id'])
        batch_op.create_foreign_key('fk_booking_assignments_partner_vehicle_id', 'partner_vehicles', ['partner_vehicle_id'], ['id'])

    op.create_table(
        'dispatch_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING_DISPATCH'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id', name='uq_dispatch_requests_booking_id'),
    )
    op.create_index('ix_dispatch_requests_status', 'dispatch_requests', ['status'])

    op.create_table(
        'dispatch_offers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dispatch_request_id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('partner_vehicle_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OFFERED'),
        sa.Column('candidate_snapshot', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('offered_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.Column('email_status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('email_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_email_error', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['dispatch_request_id'], ['dispatch_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['partner_id'], ['truck_partners.id']),
        sa.ForeignKeyConstraint(['partner_vehicle_id'], ['partner_vehicles.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_dispatch_offers_token_hash'),
    )
    for column in ('dispatch_request_id', 'partner_id', 'partner_vehicle_id', 'status', 'expires_at'):
        op.create_index(f'ix_dispatch_offers_{column}', 'dispatch_offers', [column])
    op.create_index('ix_dispatch_offers_status_expiry', 'dispatch_offers', ['status', 'expires_at'])

    op.create_table(
        'dispatch_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dispatch_request_id', sa.Integer(), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=True),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=40), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dispatch_request_id'], ['dispatch_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['offer_id'], ['dispatch_offers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('dispatch_request_id', 'offer_id', 'event_type', 'created_at'):
        op.create_index(f'ix_dispatch_events_{column}', 'dispatch_events', [column])

    op.create_table(
        'resource_reservations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dispatch_request_id', sa.Integer(), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('truck_id', sa.Integer(), nullable=True),
        sa.Column('partner_vehicle_id', sa.Integer(), nullable=True),
        sa.Column('mover_id', sa.Integer(), nullable=True),
        sa.Column('reservation_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='HELD'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('release_reason', sa.String(length=250), nullable=True),
        sa.CheckConstraint(
            '(truck_id IS NOT NULL AND partner_vehicle_id IS NULL AND mover_id IS NULL) OR '
            '(truck_id IS NULL AND partner_vehicle_id IS NOT NULL AND mover_id IS NULL) OR '
            '(truck_id IS NULL AND partner_vehicle_id IS NULL AND mover_id IS NOT NULL)',
            name='ck_resource_reservation_one_resource',
        ),
        sa.ForeignKeyConstraint(['dispatch_request_id'], ['dispatch_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['offer_id'], ['dispatch_offers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['truck_id'], ['trucks.id']),
        sa.ForeignKeyConstraint(['partner_vehicle_id'], ['partner_vehicles.id']),
        sa.ForeignKeyConstraint(['mover_id'], ['movers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('dispatch_request_id', 'offer_id', 'booking_id', 'reservation_date', 'status'):
        op.create_index(f'ix_resource_reservations_{column}', 'resource_reservations', [column])
    active_predicate = sa.text("status IN ('HELD', 'RESERVED')")
    resource_indexes = {
        'truck_id': 'uq_reserved_truck_day',
        'partner_vehicle_id': 'uq_reserved_partner_vehicle_day',
        'mover_id': 'uq_reserved_mover_day',
    }
    for resource_column, index_name in resource_indexes.items():
        op.create_index(
            index_name, 'resource_reservations',
            [resource_column, 'reservation_date'], unique=True,
            sqlite_where=active_predicate, postgresql_where=active_predicate,
        )


def downgrade():
    op.drop_index('ix_ai_usage_counters_usage_date', table_name='ai_usage_counters')
    op.drop_index('ix_ai_usage_counters_user_id', table_name='ai_usage_counters')
    op.drop_table('ai_usage_counters')
    for index_name in ('uq_reserved_truck_day', 'uq_reserved_partner_vehicle_day', 'uq_reserved_mover_day'):
        op.drop_index(index_name, table_name='resource_reservations')
    for column in ('dispatch_request_id', 'offer_id', 'booking_id', 'reservation_date', 'status'):
        op.drop_index(f'ix_resource_reservations_{column}', table_name='resource_reservations')
    op.drop_table('resource_reservations')
    for column in ('dispatch_request_id', 'offer_id', 'event_type', 'created_at'):
        op.drop_index(f'ix_dispatch_events_{column}', table_name='dispatch_events')
    op.drop_table('dispatch_events')
    op.drop_index('ix_dispatch_offers_status_expiry', table_name='dispatch_offers')
    for column in ('dispatch_request_id', 'partner_id', 'partner_vehicle_id', 'status', 'expires_at'):
        op.drop_index(f'ix_dispatch_offers_{column}', table_name='dispatch_offers')
    op.drop_table('dispatch_offers')
    op.drop_index('ix_dispatch_requests_status', table_name='dispatch_requests')
    op.drop_table('dispatch_requests')
    with op.batch_alter_table('booking_assignments') as batch_op:
        batch_op.drop_constraint('fk_booking_assignments_partner_vehicle_id', type_='foreignkey')
        batch_op.drop_constraint('fk_booking_assignments_partner_id', type_='foreignkey')
        batch_op.drop_column('partner_vehicle_id')
        batch_op.drop_column('partner_id')
    op.drop_column('truck_partners', 'service_types')