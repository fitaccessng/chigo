"""Add the customer referral programme and promotional-credit ledger.

Revision ID: 20261010_customer_referrals
Revises: 20261009_add_oauth_identities
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa


revision = '20261010_customer_referrals'
down_revision = '20261009_add_oauth_identities'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'referral_campaigns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identifier', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('starts_at', sa.DateTime(), nullable=True),
        sa.Column('ends_at', sa.DateTime(), nullable=True),
        sa.Column('reward_amount_kobo', sa.Integer(), nullable=False, server_default='200000'),
        sa.Column('discount_amount_kobo', sa.Integer(), nullable=False, server_default='200000'),
        sa.Column('minimum_booking_value_kobo', sa.Integer(), nullable=False, server_default='1000000'),
        sa.Column('minimum_payable_kobo', sa.Integer(), nullable=False, server_default='100000'),
        sa.Column('max_referrals_per_customer', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('max_referrals_total', sa.Integer(), nullable=False, server_default='10000'),
        sa.Column('attribution_window_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('reward_expiration_days', sa.Integer(), nullable=False, server_default='365'),
        sa.Column('eligible_services', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('allow_promotion_stacking', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('identifier'),
    )
    op.create_index(op.f('ix_referral_campaigns_identifier'), 'referral_campaigns', ['identifier'], unique=True)
    op.execute(sa.text(
        "INSERT INTO referral_campaigns "
        "(identifier, name, description, active, reward_amount_kobo, discount_amount_kobo, "
        "minimum_booking_value_kobo, minimum_payable_kobo, max_referrals_per_customer, "
        "max_referrals_total, attribution_window_days, reward_expiration_days, eligible_services, "
        "allow_promotion_stacking, created_at, updated_at) "
        "VALUES ('customer-referrals', 'Chigo Refer & Earn', "
        "'Disabled by default pending review of booking contribution margins and refund policy.', "
        "false, 200000, 200000, 1000000, 100000, 20, 10000, 30, 365, '[]', false, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))

    op.create_table(
        'referral_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=24), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index(op.f('ix_referral_codes_user_id'), 'referral_codes', ['user_id'], unique=True)
    op.create_index(op.f('ix_referral_codes_code'), 'referral_codes', ['code'], unique=True)

    op.create_table(
        'referral_attributions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('referral_code_id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=24), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['referral_code_id'], ['referral_codes.id']),
        sa.ForeignKeyConstraint(['campaign_id'], ['referral_campaigns.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index(op.f('ix_referral_attributions_token'), 'referral_attributions', ['token'], unique=True)
    op.create_index(op.f('ix_referral_attributions_referral_code_id'), 'referral_attributions', ['referral_code_id'], unique=False)
    op.create_index(op.f('ix_referral_attributions_campaign_id'), 'referral_attributions', ['campaign_id'], unique=False)

    op.create_table(
        'referrals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('referral_code_id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('referrer_user_id', sa.Integer(), nullable=False),
        sa.Column('referred_user_id', sa.Integer(), nullable=False),
        sa.Column('qualifying_booking_id', sa.Integer(), nullable=True),
        sa.Column('attribution_source', sa.String(length=24), nullable=False),
        sa.Column('attributed_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='registered'),
        sa.Column('terms_snapshot', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['referral_code_id'], ['referral_codes.id']),
        sa.ForeignKeyConstraint(['campaign_id'], ['referral_campaigns.id']),
        sa.ForeignKeyConstraint(['referrer_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['referred_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['qualifying_booking_id'], ['bookings.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('referred_user_id', name='uq_referrals_referred_user'),
        sa.UniqueConstraint('qualifying_booking_id', name='uq_referrals_qualifying_booking'),
    )
    for column in ('referral_code_id', 'campaign_id', 'referrer_user_id', 'referred_user_id', 'status'):
        op.create_index(op.f(f'ix_referrals_{column}'), 'referrals', [column], unique=False)

    with op.batch_alter_table('bookings') as batch_op:
        batch_op.add_column(sa.Column('referral_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('referral_discount_amount', sa.Float(), nullable=False, server_default='0'))
        batch_op.create_foreign_key('fk_bookings_referral_id_referrals', 'referrals', ['referral_id'], ['id'])
    op.create_index(op.f('ix_bookings_referral_id'), 'bookings', ['referral_id'], unique=False)

    op.create_table(
        'referral_rewards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=False),
        sa.Column('beneficiary_user_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('reward_type', sa.String(length=32), nullable=False),
        sa.Column('amount_kobo', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='NGN'),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='pending'),
        sa.Column('idempotency_key', sa.String(length=120), nullable=False),
        sa.Column('earned_at', sa.DateTime(), nullable=True),
        sa.Column('available_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['referral_id'], ['referrals.id']),
        sa.ForeignKeyConstraint(['beneficiary_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_referral_rewards_idempotency'),
        sa.UniqueConstraint('referral_id', 'reward_type', name='uq_referral_rewards_type'),
    )
    for column in ('referral_id', 'beneficiary_user_id', 'booking_id', 'status'):
        op.create_index(op.f(f'ix_referral_rewards_{column}'), 'referral_rewards', [column], unique=False)

    op.create_table(
        'reward_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reward_id', sa.Integer(), nullable=True),
        sa.Column('referral_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(length=32), nullable=False),
        sa.Column('amount_kobo', sa.Integer(), nullable=False),
        sa.Column('previous_status', sa.String(length=24), nullable=True),
        sa.Column('new_status', sa.String(length=24), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('idempotency_key', sa.String(length=140), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['reward_id'], ['referral_rewards.id']),
        sa.ForeignKeyConstraint(['referral_id'], ['referrals.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['users.id']),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_reward_transactions_idempotency'),
    )
    for column in ('reward_id', 'referral_id', 'customer_id', 'booking_id'):
        op.create_index(op.f(f'ix_reward_transactions_{column}'), 'reward_transactions', [column], unique=False)


def downgrade():
    for column in ('reward_id', 'referral_id', 'customer_id', 'booking_id'):
        op.drop_index(op.f(f'ix_reward_transactions_{column}'), table_name='reward_transactions')
    op.drop_table('reward_transactions')
    for column in ('referral_id', 'beneficiary_user_id', 'booking_id', 'status'):
        op.drop_index(op.f(f'ix_referral_rewards_{column}'), table_name='referral_rewards')
    op.drop_table('referral_rewards')
    op.drop_index(op.f('ix_bookings_referral_id'), table_name='bookings')
    with op.batch_alter_table('bookings') as batch_op:
        batch_op.drop_constraint('fk_bookings_referral_id_referrals', type_='foreignkey')
        batch_op.drop_column('referral_discount_amount')
        batch_op.drop_column('referral_id')
    for column in ('referral_code_id', 'campaign_id', 'referrer_user_id', 'referred_user_id', 'status'):
        op.drop_index(op.f(f'ix_referrals_{column}'), table_name='referrals')
    op.drop_table('referrals')
    for column in ('token', 'referral_code_id', 'campaign_id'):
        op.drop_index(op.f(f'ix_referral_attributions_{column}'), table_name='referral_attributions')
    op.drop_table('referral_attributions')
    op.drop_index(op.f('ix_referral_codes_code'), table_name='referral_codes')
    op.drop_index(op.f('ix_referral_codes_user_id'), table_name='referral_codes')
    op.drop_table('referral_codes')
    op.drop_index(op.f('ix_referral_campaigns_identifier'), table_name='referral_campaigns')
    op.drop_table('referral_campaigns')