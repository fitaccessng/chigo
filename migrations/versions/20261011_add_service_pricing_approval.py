"""Track explicit approval of configured pricing values.

Revision ID: 20261011_service_pricing_approval
Revises: 20261011_service_aware_locations
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa


revision = '20261011_service_pricing_approval'
down_revision = '20261011_service_aware_locations'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('service_pricing') as batch_op:
        batch_op.add_column(sa.Column(
            'is_configured', sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade():
    with op.batch_alter_table('service_pricing') as batch_op:
        batch_op.drop_column('is_configured')
