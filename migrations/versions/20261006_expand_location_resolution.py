"""Persist geocoding, nearest-reference, and routing resolution metadata.

Revision ID: 20261006_expand_location_resolution
Revises: 20261006_add_postgis_location_tables
Create Date: 2026-10-06
"""

from alembic import op

revision = '20261006_expand_location_resolution'
down_revision = '20261006_add_postgis_location_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE location_observations ADD COLUMN IF NOT EXISTS formatted_address TEXT")
    op.execute("ALTER TABLE location_observations ADD COLUMN IF NOT EXISTS provider_reference VARCHAR(255)")
    op.execute("ALTER TABLE location_observations ADD COLUMN IF NOT EXISTS nearest_road VARCHAR(180)")
    op.execute("ALTER TABLE location_observations ADD COLUMN IF NOT EXISTS route_resolution VARCHAR(40)")
    op.execute("ALTER TABLE location_records ADD COLUMN IF NOT EXISTS formatted_address TEXT")
    op.execute("ALTER TABLE location_records ADD COLUMN IF NOT EXISTS provider_reference VARCHAR(255)")
    op.execute("ALTER TABLE location_records ADD COLUMN IF NOT EXISTS nearest_road VARCHAR(180)")
    op.execute("ALTER TABLE location_records ADD COLUMN IF NOT EXISTS route_resolution VARCHAR(40)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_location_records_parent_area_type ON location_records (parent_id, area_council_id, place_type)")


def downgrade():
    raise RuntimeError('Location resolution metadata migration is intentionally irreversible to preserve learned location data.')
