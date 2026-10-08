"""Add isolated demo ownership metadata and mover team relationships.

Revision ID: 20261007_add_demo_fleet_metadata
Revises: 20261006_upgrade_booking_location_schema
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa


revision = '20261007_add_demo_fleet_metadata'
down_revision = '20261006_expand_location_resolution'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('truck_partners', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('truck_partners', sa.Column('seed_source', sa.String(length=40), nullable=False, server_default='production'))
    op.add_column('trucks', sa.Column('payload_capacity_kg', sa.Float(), nullable=True))
    op.add_column('trucks', sa.Column('dimensions_m', sa.String(length=80), nullable=True))
    op.add_column('trucks', sa.Column('driver_required', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('trucks', sa.Column('fuel_assumption', sa.String(length=120), nullable=True))
    op.add_column('trucks', sa.Column('image', sa.String(length=255), nullable=True))
    op.add_column('trucks', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('trucks', sa.Column('seed_source', sa.String(length=40), nullable=False, server_default='production'))
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('team_leader', sa.String(length=120), nullable=True),
        sa.Column('current_assignment', sa.String(length=160), nullable=True),
        sa.Column('jobs_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('availability', sa.String(length=30), nullable=False, server_default='Available'),
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('seed_source', sa.String(length=40), nullable=False, server_default='production'),
    )
    op.add_column('movers', sa.Column('jobs_completed', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('movers', sa.Column('current_assignment', sa.String(length=160), nullable=True))
    op.add_column('movers', sa.Column('team_id', sa.Integer(), nullable=True))
    op.add_column('movers', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('movers', sa.Column('seed_source', sa.String(length=40), nullable=False, server_default='production'))
    op.create_foreign_key('fk_movers_team_id_teams', 'movers', 'teams', ['team_id'], ['id'])
    op.create_index('ix_movers_team_id', 'movers', ['team_id'])
    op.create_index('ix_teams_is_demo', 'teams', ['is_demo'])
    op.create_index('ix_trucks_is_demo', 'trucks', ['is_demo'])
    op.create_index('ix_truck_partners_is_demo', 'truck_partners', ['is_demo'])


def downgrade():
    raise RuntimeError('Demo fleet metadata migration is intentionally irreversible to preserve operational data.')
