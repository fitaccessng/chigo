"""Add PostgreSQL/PostGIS location ingestion tables.

Revision ID: 20261006_add_postgis_location_tables
Revises: 20261006_upgrade_booking_location_schema
Create Date: 2026-10-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry


revision = '20261006_add_postgis_location_tables'
down_revision = '20261006_upgrade_booking_location_schema'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS postgis;')
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm;')

    op.create_table(
        'location_sources',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source_name', sa.String(length=120), nullable=False, unique=True),
        sa.Column('source_type', sa.String(length=80), nullable=False, default='osm'),
        sa.Column('source_url', sa.String(length=255), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    op.create_table(
        'administrative_areas',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=180), nullable=False),
        sa.Column('slug', sa.String(length=180), nullable=False, unique=True),
        sa.Column('area_council', sa.String(length=160), nullable=False),
        sa.Column('state', sa.String(length=160), nullable=False, default='Federal Capital Territory'),
        sa.Column('country', sa.String(length=100), nullable=False, default='Nigeria'),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('location_sources.id')),
        sa.Column('geom', Geometry('MULTIPOLYGON', srid=4326, spatial_index=False), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('source_identifier', sa.String(length=160), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_administrative_areas_area_council', 'administrative_areas', ['area_council'])
    op.create_index('idx_administrative_areas_geom', 'administrative_areas', ['geom'], postgresql_using='gist')
    op.create_index('ix_administrative_areas_source_identifier', 'administrative_areas', ['source_identifier'], unique=True)

    op.create_table(
        'location_records',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=180), nullable=False),
        sa.Column('normalized_name', sa.String(length=180), nullable=False),
        sa.Column('place_type', sa.String(length=80), nullable=False, default='district'),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('location_records.id'), nullable=True),
        sa.Column('area_council_id', sa.Integer(), sa.ForeignKey('administrative_areas.id'), nullable=True),
        sa.Column('district_id', sa.Integer(), sa.ForeignKey('location_records.id'), nullable=True),
        sa.Column('area_council', sa.String(length=160), nullable=True),
        sa.Column('district', sa.String(length=160), nullable=True),
        sa.Column('neighborhood', sa.String(length=160), nullable=True),
        sa.Column('area', sa.String(length=160), nullable=True),
        sa.Column('parent_name', sa.String(length=180), nullable=True),
        sa.Column('state', sa.String(length=160), nullable=False, default='Federal Capital Territory'),
        sa.Column('country', sa.String(length=100), nullable=False, default='Nigeria'),
        sa.Column('latitude', sa.Float(), nullable=False, default=0.0),
        sa.Column('longitude', sa.Float(), nullable=False, default=0.0),
        sa.Column('geom', Geometry('POINT', srid=4326, spatial_index=False), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=True),
        sa.Column('search_keywords', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(length=120), nullable=False, default='fct-curated'),
        sa.Column('source_id', sa.String(length=160), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, default=1.0),
        sa.Column('verification_status', sa.String(length=30), nullable=False, default='verified'),
        sa.Column('raw_input', sa.String(length=500), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=False, default=0),
        sa.Column('first_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('bounding_box', sa.JSON(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False, default=True),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('popularity_score', sa.Float(), nullable=False, default=0.0),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('source', 'source_id', name='uq_location_records_source_source_id'),
    )
    op.create_index('ix_location_records_normalized_name', 'location_records', ['normalized_name'])
    op.create_index('ix_location_records_area_council', 'location_records', ['area_council'])
    op.create_index('ix_location_records_parent_id', 'location_records', ['parent_id'])
    op.create_index('ix_location_records_verification_status', 'location_records', ['verification_status'])
    op.create_index('idx_location_records_geom', 'location_records', ['geom'], postgresql_using='gist')
    op.execute('CREATE INDEX ix_location_records_name_trgm ON location_records USING gin (normalized_name gin_trgm_ops);')

    op.create_table(
        'location_aliases',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('location_id', sa.Integer(), sa.ForeignKey('location_records.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alias_name', sa.String(length=180), nullable=False),
        sa.Column('normalized_alias', sa.String(length=180), nullable=False),
        sa.Column('source_name', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('location_id', 'normalized_alias', name='uq_location_aliases_location_alias'),
    )
    op.create_index('ix_location_aliases_normalized_alias', 'location_aliases', ['normalized_alias'])
    op.execute('CREATE INDEX ix_location_aliases_name_trgm ON location_aliases USING gin (normalized_alias gin_trgm_ops);')

    op.create_table(
        'location_provenance',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('location_id', sa.Integer(), sa.ForeignKey('location_records.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('location_sources.id'), nullable=False),
        sa.Column('external_id', sa.String(length=180), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('raw_attributes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('source_id', 'external_id', name='uq_location_provenance_source_external_id'),
    )
    op.create_index('ix_location_provenance_location_id', 'location_provenance', ['location_id'])

    op.create_table(
        'location_observations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('normalized_input', sa.String(length=500), nullable=False, unique=True),
        sa.Column('raw_input', sa.String(length=500), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('geom', Geometry('POINT', srid=4326, spatial_index=False), nullable=True),
        sa.Column('area_council', sa.String(length=160), nullable=True),
        sa.Column('district', sa.String(length=180), nullable=True),
        sa.Column('nearest_landmark', sa.String(length=180), nullable=True),
        sa.Column('resolution_source', sa.String(length=80), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='pending_review'),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_location_observations_geom', 'location_observations', ['geom'], postgresql_using='gist')
    op.execute('CREATE INDEX ix_location_observations_input_trgm ON location_observations USING gin (normalized_input gin_trgm_ops);')


def downgrade():
    op.drop_table('location_observations')
    op.drop_table('location_provenance')
    op.drop_table('location_aliases')
    op.drop_table('location_records')
    op.drop_table('administrative_areas')
    op.drop_table('location_sources')