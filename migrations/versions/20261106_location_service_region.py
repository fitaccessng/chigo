"""Separate operational service coverage from administrative geography.

Revision ID: 20261106_location_service_region
Revises: 20261022_fct_location_directory
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa


revision = '20261106_location_service_region'
down_revision = '20261022_fct_location_directory'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    local_place_columns = {column['name'] for column in inspector.get_columns('local_places')}
    if 'service_region' not in local_place_columns:
        with op.batch_alter_table('local_places') as batch_op:
            batch_op.add_column(sa.Column('service_region', sa.String(length=80), nullable=True))
    local_place_indexes = {index['name'] for index in inspector.get_indexes('local_places')}
    if 'ix_local_places_service_region' not in local_place_indexes:
        op.create_index('ix_local_places_service_region', 'local_places', ['service_region'])
    op.execute("""
        UPDATE local_places
        SET service_region = 'abuja_fct_metro'
        WHERE upper(state) IN ('FCT', 'FEDERAL CAPITAL TERRITORY')
           OR area_council = 'Nasarawa State (FCT Metro)'
    """)

    if inspector.has_table('location_records'):
        location_columns = {column['name'] for column in inspector.get_columns('location_records')}
        missing_columns = {'lga', 'service_region'} - location_columns
        if missing_columns:
            with op.batch_alter_table('location_records') as batch_op:
                if 'lga' in missing_columns:
                    batch_op.add_column(sa.Column('lga', sa.String(length=120), nullable=True))
                if 'service_region' in missing_columns:
                    batch_op.add_column(sa.Column('service_region', sa.String(length=80), nullable=True))

        location_indexes = {index['name'] for index in inspector.get_indexes('location_records')}
        if 'ix_location_records_service_region' not in location_indexes:
            op.create_index('ix_location_records_service_region', 'location_records', ['service_region'])
        op.execute("""
            UPDATE location_records
            SET service_region = 'abuja_fct_metro'
            WHERE upper(state) IN ('FCT', 'FEDERAL CAPITAL TERRITORY')
               OR area_council = 'Nasarawa State (FCT Metro)'
        """)


def downgrade():
    raise RuntimeError(
        'This migration is intentionally irreversible because it adds administrative '
        'and operational classifications to imported location records.'
    )
