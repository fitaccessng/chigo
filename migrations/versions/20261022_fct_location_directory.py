"""Add provenance and postal directory metadata to existing location stores.

Revision ID: 20261022_fct_location_directory
Revises: 20261021_optional_booking_countries
Create Date: 2026-10-22

The migration is additive. Existing verified locations and bookings are retained;
coordinates become nullable so directory entries without verified coordinates
can be represented without fabricated points.
"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry


revision = '20261022_fct_location_directory'
down_revision = '20261021_optional_booking_countries'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('local_places') as batch_op:
        batch_op.add_column(sa.Column('source_name', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('source_identifier', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('raw_source_name', sa.String(length=180), nullable=True))
        batch_op.add_column(sa.Column('postal_code', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column(
            'source_metadata', sa.JSON(), nullable=False, server_default=sa.text("'{}'"),
        ))
        batch_op.add_column(sa.Column(
            'verification_status', sa.String(length=30), nullable=False,
            server_default='unverified',
        ))
        batch_op.alter_column('latitude', existing_type=sa.Float(), nullable=True)
        batch_op.alter_column('longitude', existing_type=sa.Float(), nullable=True)
    op.create_index('ix_local_places_postal_code', 'local_places', ['postal_code'])
    op.create_index('ix_local_places_source_identifier', 'local_places', ['source_identifier'], unique=True)

    with op.batch_alter_table('bookings') as batch_op:
        batch_op.add_column(sa.Column('pickup_postal_code', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('destination_postal_code', sa.String(length=20), nullable=True))

    with op.batch_alter_table('location_records') as batch_op:
        batch_op.add_column(sa.Column('postal_code', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('raw_source_name', sa.String(length=180), nullable=True))
        batch_op.alter_column('latitude', existing_type=sa.Float(), nullable=True)
        batch_op.alter_column('longitude', existing_type=sa.Float(), nullable=True)
        batch_op.alter_column('geom', existing_type=Geometry('POINT', srid=4326), nullable=True)
    op.create_index('ix_location_records_postal_code', 'location_records', ['postal_code'])


def downgrade():
    raise RuntimeError(
        'This migration is intentionally irreversible to preserve imported location '
        'provenance and records that legitimately have no coordinates.'
    )