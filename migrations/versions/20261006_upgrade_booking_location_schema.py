"""Add booking and LocalPlace columns formerly applied at app startup.

Revision ID: 20261006_upgrade_booking_location_schema
Revises: 20261006_baseline_chigo_schema
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = '20261006_upgrade_booking_location_schema'
down_revision = '20261006_baseline_chigo_schema'
branch_labels = None
depends_on = None

BOOKING_COLUMNS = {
    'booking_request_id': sa.String(32), 'workflow_state': sa.String(40),
    'quote_status': sa.String(30), 'workflow_data': sa.JSON(), 'calculation_data': sa.JSON(),
    'estimated_duration_minutes': sa.Integer(), 'flexible_date': sa.Boolean(),
    'flexible_time': sa.Boolean(), 'schedule_feasible': sa.Boolean(),
    'manually_overridden_total': sa.Float(), 'price_override_reason': sa.Text(),
    'pickup_country': sa.String(100), 'destination_country': sa.String(100),
    'pickup_area_council': sa.String(100), 'pickup_district': sa.String(120),
    'pickup_neighborhood': sa.String(120), 'pickup_location_type': sa.String(60),
    'pickup_original_input': sa.String(500), 'destination_area_council': sa.String(100),
    'destination_district': sa.String(120), 'destination_neighborhood': sa.String(120),
    'destination_location_type': sa.String(60), 'destination_original_input': sa.String(500),
    'pickup_normalized_location': sa.String(500), 'destination_normalized_location': sa.String(500),
    'pickup_local_place_id': sa.Integer(), 'destination_local_place_id': sa.Integer(),
    'route_duration_minutes': sa.Float(), 'service_area_status': sa.String(40),
    'route_provider': sa.String(40), 'route_status': sa.String(30), 'route_calculated_at': sa.DateTime(),
    'pickup_area': sa.String(120), 'pickup_formatted_address': sa.Text(),
    'pickup_latitude': sa.Float(), 'pickup_longitude': sa.Float(), 'pickup_place_id': sa.String(255),
    'pickup_provider': sa.String(50), 'pickup_provider_raw_id': sa.String(255),
    'pickup_access': sa.String(30), 'pickup_inside_estate': sa.Boolean(),
    'pickup_narrow_road': sa.Boolean(), 'pickup_parking_close': sa.Boolean(),
    'pickup_floor': sa.String(20), 'pickup_stairs': sa.Boolean(),
    'destination_area': sa.String(120), 'destination_formatted_address': sa.Text(),
    'destination_latitude': sa.Float(), 'destination_longitude': sa.Float(),
    'destination_place_id': sa.String(255), 'destination_provider': sa.String(50),
    'destination_provider_raw_id': sa.String(255), 'destination_access': sa.String(30),
    'destination_inside_estate': sa.Boolean(), 'destination_narrow_road': sa.Boolean(),
    'destination_parking_close': sa.Boolean(), 'destination_floor': sa.String(20),
    'destination_stairs': sa.Boolean(), 'distance_km': sa.Float(),
    'number_of_bedrooms': sa.Integer(), 'number_of_living_rooms': sa.Integer(),
    'number_of_kitchens': sa.Integer(), 'number_of_bathrooms': sa.Integer(),
    'number_of_floors': sa.Integer(), 'estimated_volume_m3': sa.Float(),
    'estimated_weight_kg': sa.Float(), 'recommended_vehicle_type_id': sa.Integer(),
    'assigned_vehicle_type_id': sa.Integer(), 'assigned_partner_vehicle_id': sa.Integer(),
    'vehicle_review_required': sa.Boolean(), 'vehicle_override_reason': sa.Text(),
    'overridden_by': sa.Integer(), 'overridden_at': sa.DateTime(),
    'pickup_nearest_landmark': sa.String(180), 'pickup_nearest_road': sa.String(180),
    'pickup_route_resolution': sa.String(40),
    'destination_nearest_landmark': sa.String(180), 'destination_nearest_road': sa.String(180),
    'destination_route_resolution': sa.String(40),
}

LOCAL_PLACE_COLUMNS = {
    'parent_id': sa.Integer(), 'state': sa.String(100), 'country': sa.String(100),
    'address': sa.Text(), 'search_keywords': sa.Text(), 'popularity_score': sa.Float(),
    'place_type': sa.String(60), 'area_council': sa.String(80), 'district': sa.String(120),
    'neighborhood': sa.String(120), 'area': sa.String(120), 'lga': sa.String(80),
    'aliases': sa.Text(), 'latitude': sa.Float(), 'longitude': sa.Float(),
    'provider': sa.String(50), 'provider_place_id': sa.String(255),
    'verified': sa.Boolean(), 'active': sa.Boolean(),
}


def _sql_type(column_type):
    if isinstance(column_type, str):
        return column_type
    type_name = type(column_type).__name__.upper()
    if type_name == 'STRING':
        return f'VARCHAR({column_type.length})' if column_type.length else 'VARCHAR'
    if type_name == 'INTEGER':
        return 'INTEGER'
    if type_name == 'FLOAT':
        return 'FLOAT'
    if type_name == 'BOOLEAN':
        return 'BOOLEAN'
    if type_name == 'DATETIME':
        return 'TIMESTAMP'
    if type_name == 'TEXT':
        return 'TEXT'
    if type_name == 'JSON':
        return 'JSON'
    raise TypeError(f'Unsupported migration column type: {type_name}')


def _add_missing_columns(table_name, columns):
    for name, column_type in columns.items():
        op.execute(f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {name} {_sql_type(column_type)}')


def upgrade():
    _add_missing_columns('bookings', BOOKING_COLUMNS)
    _add_missing_columns('local_places', LOCAL_PLACE_COLUMNS)
    op.create_index('ix_bookings_booking_request_id', 'bookings', ['booking_request_id'], unique=True, if_not_exists=True)
    op.create_index('ix_local_places_active_normalized_name', 'local_places', ['active', 'normalized_name'], if_not_exists=True)
    op.create_index('ix_local_places_council_normalized_name', 'local_places', ['area_council', 'normalized_name'], if_not_exists=True)


def downgrade():
    raise RuntimeError('This compatibility migration is intentionally irreversible to preserve booking and location data.')
