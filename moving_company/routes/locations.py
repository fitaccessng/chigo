from flask import Blueprint, jsonify, request

from ..extensions import csrf
from ..services.location_service import (
    geocode_address,
    get_place_details,
    reverse_geocode,
    route_distance,
    save_customer_location,
    search_places,
    validate_coordinates,
    validate_location,
)

locations_bp = Blueprint('locations', __name__)


@csrf.exempt
@locations_bp.route('/api/locations/autocomplete', methods=['GET'])
@locations_bp.route('/api/locations/search', methods=['GET'])
def autocomplete():
    query = request.args.get('q') or request.args.get('query') or ''
    if len(query.strip()) < 2:
        return jsonify({'success': True, 'results': []})
    try:
        limit = max(1, min(int(request.args.get('limit', '8')), 10))
    except ValueError:
        limit = 8
    return jsonify({'success': True, 'results': search_places(query, limit=limit)})


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
