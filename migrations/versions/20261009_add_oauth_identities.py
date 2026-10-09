"""Add provider identities for external authentication.

Revision ID: 20261009_add_oauth_identities
Revises: 20261008_add_support_chat
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa


revision = '20261009_add_oauth_identities'
down_revision = '20261008_add_support_chat'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'oauth_identities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('email_verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'subject', name='uq_oauth_identity_provider_subject'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_oauth_identity_user_provider'),
    )
    op.create_index(op.f('ix_oauth_identities_user_id'), 'oauth_identities', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_oauth_identities_user_id'), table_name='oauth_identities')
    op.drop_table('oauth_identities')