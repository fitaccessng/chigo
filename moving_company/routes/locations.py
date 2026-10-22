from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import csrf
from ..services.location_service import (
    canonical_area_council,
    geocode_address,
    get_place_details,
    LocationSearchUnavailable,
    reverse_geocode,
    route_distance,
    save_customer_location,
    search_places,
    validate_coordinates,
    validate_location,
)
from ..services.fct_location_data import FCT_AREA_COUNCILS

locations_bp = Blueprint('locations', __name__)

_AUTOCOMPLETE_LEGACY_FIELDS = (
    'id', 'local_place_id', 'name', 'formatted_address', 'display_name', 'area',
    'area_council', 'district', 'neighborhood', 'location_type', 'type', 'city',
    'state', 'country', 'latitude', 'longitude', 'postal_code', 'place_id',
    'provider_place_id', 'provider', 'provider_raw_id', 'source',
    'coordinate_precision', 'original_query', 'normalized_query', 'confidence',
)
_PUBLIC_ADDRESS_FIELDS = (
    'house_number', 'road', 'street', 'suburb', 'neighbourhood', 'city_district',
    'city', 'town', 'village', 'municipality', 'state', 'state_district',
    'postcode', 'country', 'country_code', 'area_council',
)


def _autocomplete_contract(result):
    components = {
        key: result.get(key) or None
        for key in ('area_council', 'district', 'neighborhood', 'city', 'state', 'country', 'postal_code')
    }
    verified = bool(result.get('verified')) or result.get('verification_status') == 'verified'
    has_coordinates = validate_coordinates(result.get('latitude'), result.get('longitude')) is not None
    public_result = {key: result.get(key) for key in _AUTOCOMPLETE_LEGACY_FIELDS}
    address = result.get('address')
    public_result['address'] = {
        key: address.get(key)
        for key in _PUBLIC_ADDRESS_FIELDS
        if isinstance(address, dict) and address.get(key) is not None
    }
    return {
        **public_result,
        'feature_type': result.get('location_type') or result.get('type') or 'unknown',
        'state_or_territory': result.get('state') or None,
        'parent_name': result.get('district') or result.get('area_council') or None,
        'address_components': components,
        'precision': result.get('coordinate_precision') or result.get('precision') or 'unknown',
        'source_summary': result.get('source') or result.get('provider') or 'unknown',
        'verified': verified,
        'requires_confirmation': not verified or not components['city'] or not components['state'] or not has_coordinates,
    }


@csrf.exempt
@locations_bp.route('/api/locations/autocomplete', methods=['GET'])
@locations_bp.route('/api/locations/search', methods=['GET'])
def autocomplete():
    query = request.args.get('q') or request.args.get('query') or ''
    query = query.strip()
    if len(query) > 160:
        return jsonify({'success': False, 'error': 'Search query must be 160 characters or fewer.'}), 400
    if len(query) < 2:
        return jsonify({'success': True, 'results': []})
    try:
        limit = int(request.args.get('limit', '8'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Limit must be an integer between 1 and 10.'}), 400
    if not 1 <= limit <= 10:
        return jsonify({'success': False, 'error': 'Limit must be an integer between 1 and 10.'}), 400

    area_council = (request.args.get('area_council') or '').strip()
    if area_council and canonical_area_council(area_council) not in {
        council.casefold() for council in FCT_AREA_COUNCILS
    }:
        return jsonify({'success': False, 'error': 'Unknown FCT area council.'}), 400

    latitude = request.args.get('lat')
    longitude = request.args.get('lon')
    if (latitude is None) != (longitude is None):
        return jsonify({'success': False, 'error': 'Latitude and longitude must be provided together.'}), 400
    coordinates = validate_coordinates(latitude, longitude) if latitude is not None else None
    if latitude is not None and coordinates is None:
        return jsonify({'success': False, 'error': 'Coordinates are invalid.'}), 400

    try:
        results = search_places(
            query, limit=limit, area_council=area_council or None,
            latitude=coordinates[0] if coordinates else None,
            longitude=coordinates[1] if coordinates else None,
        )
    except LocationSearchUnavailable as error:
        current_app.logger.error('Location autocomplete unavailable (kind=%s).', error.kind)
        return jsonify({'success': False, 'error': 'Location search is temporarily unavailable. Please retry.'}), 503
    except SQLAlchemyError:
        db_session = getattr(current_app.extensions.get('sqlalchemy'), 'session', None)
        if db_session is not None:
            db_session.rollback()
        current_app.logger.error('Location autocomplete unavailable (kind=database).')
        return jsonify({'success': False, 'error': 'Location search is temporarily unavailable. Please retry.'}), 503
    return jsonify({'success': True, 'results': [_autocomplete_contract(result) for result in results]})


@csrf.exempt
@locations_bp.route('/api/locations/save', methods=['POST'])
def save_location():
    payload = request.get_json(silent=True) or request.form
    name = str(payload.get('name') or payload.get('address') or '').strip()
    try:
        location = save_customer_location(
            name,
            latitude=payload.get('latitude'),
            longitude=payload.get('longitude'),
            source=str(payload.get('source') or 'manual'),
            coordinate_precision=str(payload.get('coordinate_precision') or 'unknown'),
            address=str(payload.get('address') or name),
            city=str(payload.get('city') or '').strip() or None,
            district=str(payload.get('district') or '').strip() or None,
            area_council=str(payload.get('area_council') or '').strip() or None,
            landmark=str(payload.get('landmark') or '').strip() or None,
            location_type=str(payload.get('location_type') or '').strip() or None,
            confidence=float(payload.get('confidence')) if payload.get('confidence') not in (None, '') else None,
            precision_level=str(payload.get('precision_level') or '').strip() or None,
        )
    except (TypeError, ValueError) as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'location': location})


@csrf.exempt
@locations_bp.route('/api/locations/place/<place_id>', methods=['GET'])
def place_detail(place_id):
    if not place_id:
        return jsonify({'success': False, 'error': 'Place ID is required.'}), 400
    data = get_place_details(place_id)
    if not data:
        return jsonify({'success': False, 'error': 'Location details could not be fetched.'}), 404
    return jsonify({'success': True, 'location': data})


@csrf.exempt
@locations_bp.route('/api/locations/geocode', methods=['POST'])
def geocode():
    payload = request.get_json(silent=True) or {}
    address = payload.get('address') or payload.get('query') or ''
    if not address:
        return jsonify({'success': False, 'error': 'Address is required.'}), 400
    result = geocode_address(address)
    if not result:
        return jsonify({'success': False, 'error': 'Unable to geocode this address.'}), 404
    return jsonify({'success': True, 'location': result})


@csrf.exempt
@locations_bp.route('/api/locations/reverse-geocode', methods=['POST'])
def reverse_geocode_endpoint():
    payload = request.get_json(silent=True) or {}
    latitude = payload.get('latitude')
    longitude = payload.get('longitude')
    if latitude is None or longitude is None:
        return jsonify({'success': False, 'error': 'Latitude and longitude are required.'}), 400
    result = reverse_geocode(latitude, longitude)
    if not result:
        return jsonify({'success': False, 'error': 'Unable to reverse geocode, fallback=True this point.'}), 404
    return jsonify({'success': True, 'location': result})


@csrf.exempt
@locations_bp.route('/api/locations/route', methods=['POST'])
def route():
    payload = request.get_json(silent=True) or {}
    pickup = payload.get('pickup') or payload.get('origin') or {}
    destination = payload.get('destination') or payload.get('dropoff') or {}
    pickup_latitude = pickup.get('latitude')
    pickup_longitude = pickup.get('longitude')
    destination_latitude = destination.get('latitude')
    destination_longitude = destination.get('longitude')
    pickup_coordinates = validate_coordinates(pickup_latitude, pickup_longitude)
    destination_coordinates = validate_coordinates(destination_latitude, destination_longitude)
    if pickup_coordinates is None or destination_coordinates is None:
        return jsonify({'success': False, 'error': 'Pickup and destination coordinates are required.'}), 400

    route_data = route_distance(*pickup_coordinates, *destination_coordinates)
    if not route_data:
        return jsonify({'success': False, 'error': 'Road distance is temporarily unavailable.'}), 503
    route_data['status'] = 'CALCULATED'
    return jsonify({'success': True, 'route': route_data})


@csrf.exempt
@locations_bp.route('/api/locations/validate', methods=['POST'])
def validate():
    payload = request.get_json(silent=True) or {}
    result = validate_location(payload)
    return jsonify({'success': True, 'status': result})
