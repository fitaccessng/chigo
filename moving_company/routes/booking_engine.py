from datetime import date, datetime
import secrets

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_login import current_user, login_required

from ..extensions import csrf, db
from ..forms import BookingLocationForm
from ..models import Booking, BookingInventory, BookingItem, BookingPhoto, BookingQuoteLine, InventoryItem, ServicePricing
from ..services.booking_engine_service import (
    BULK_ITEM_OPTIONS, STAGES, accept_quote, calculate_quote, confirm_inventory, create_booking_request,
    catalogue_items_for_service, inventory_fingerprint, inventory_requirements, invalidate_from, preview_move_duration, record_event,
    inventory_is_confirmed, recommended_services, replace_inventory, save_inventory_photo, save_vehicle_selection,
    save_workflow_section, suggested_inventory, update_inventory_selections, vehicle_options,
)
from ..services.service_workflow import (
    addon_questions_for, booking_questions_for, get_service_workflow, property_types_for_service,
    validate_property_type_for_service, validate_question_answers,
)
from ..services.morph_cleaning_pricing import (
    APPLIANCE_UNIT_PRICE, BEDFRAME_UNIT_PRICES, COMMERCIAL_MOVE_CLEAN,
    COUCH_UNIT_PRICES, RESIDENTIAL_MOVE_CLEAN,
)
from ..services.image_upload_service import save_image
from ..services.location_service import (
    area_council_for_point, geocode_address, get_local_place, record_location_observation,
    resolve_nearest_postgis_references, route_distance, validate_coordinates,
    validate_service_area,
)
from ..services.payment_provider_service import initialize_payment, process_stripe_webhook, verify_payment
from ..services.seo_analytics import queue_analytics_event, queue_booking_conversion
from ..services.ai_assistance_service import AIAssistanceUnavailable, suggest_booking_details


booking_bp = Blueprint('booking', __name__)
booking_api_bp = Blueprint('booking_api', __name__)
STAGE_STATES = {
    'property': 'LOCATION_COMPLETED', 'inventory': 'PROPERTY_COMPLETED',
    'services': 'INVENTORY_COMPLETED', 'schedule': 'SERVICES_COMPLETED',
    'quote': 'SCHEDULE_COMPLETED', 'payment': 'QUOTE_ACCEPTED', 'review': 'COMPLETED',
}
PROPERTY_TYPES = tuple(dict.fromkeys(type_name for types in (
    ('Apartment', 'House', 'Duplex'),
    ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
    ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate'),
) for type_name in types))
SERVICE_OPTIONS = {
    'packing': ('none', 'partial', 'full'), 'unpacking': ('no', 'yes'),
    'storage': ('none', '1 week', '1 month', '3 months'), 'assembly': ('no', 'yes'),
    'disassembly': ('no', 'yes'), 'cleaning': ('none', 'move-out', 'move-in', 'both'),
    'home_decor': ('none', 'yes'),
}
MOVE_PROPERTY_SERVICES = frozenset({'residential', 'commercial'})
LOCATION_SERVICES = {
    'residential': ('pickup', 'destination'),
    'commercial': ('pickup', 'destination'),
    'logistics': ('pickup', 'destination'),
    'cleaning': ('pickup',),
    'packing': ('pickup',),
    'storage': (),
    'support': (),
}


def service_stages(service_type, booking=None):
    if service_type == 'cleaning':
        stages = ('location', 'property', 'inventory', 'schedule', 'quote')
        if booking and booking.workflow_state in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED'}:
            return (*stages, 'payment')
        return stages
    if service_type == 'support':
        return ('location', 'property', 'inventory', 'quote')
    if service_type == 'logistics':
        stages = ('location', 'inventory', 'services', 'schedule', 'quote')
        if booking and booking.workflow_state in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED'}:
            return (*stages, 'payment')
        return stages
    if service_type == 'packing':
        stages = ('location', 'property', 'inventory', 'services', 'schedule', 'quote')
        if booking and booking.workflow_state in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED'}:
            return (*stages, 'payment')
        return stages
    if service_type == 'storage':
        stages = ('location', 'property', 'inventory', 'services', 'schedule', 'quote')
        if booking and booking.workflow_state in {'QUOTE_ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED'}:
            return (*stages, 'payment')
        return stages
    return STAGES


def location_places_for(service_type, answers=None):
    if service_type != 'storage':
        return LOCATION_SERVICES[service_type]
    answers = answers or {}
    places = []
    if answers.get('storage_collection') == 'yes':
        places.append('pickup')
    if answers.get('storage_retrieval') == 'yes' and answers.get('storage_return_known') == 'yes':
        places.append('destination')
    return tuple(places)
SERVICE_CARD_CONFIG = (
    ('Packing & Protection', 'packing', 'partial', 'Partial packing', 'We safely pack selected rooms and items.', 'packing_partial', 30),
    ('Packing & Protection', 'packing', 'full', 'Full packing', 'Our team packs the complete move inventory.', 'packing_full', 0),
    ('Packing & Protection', 'unpacking', 'yes', 'Unpacking', 'Unpack and place your items at the new property.', 'unpacking', 35),
    ('Furniture', 'disassembly', 'yes', 'Furniture disassembly', 'Prepare large furniture for transport.', 'service_disassembly_yes', 30),
    ('Furniture', 'assembly', 'yes', 'Furniture assembly', 'Reassemble furniture after delivery.', 'service_assembly_yes', 30),
    ('Cleaning', 'cleaning', 'move-out', 'Move-out cleaning', 'Clean the property after items are removed.', 'cleaning_move_out', 90),
    ('Cleaning', 'cleaning', 'move-in', 'Move-in cleaning', 'Prepare the new property before unpacking.', 'cleaning_move_in', 90),
    ('Cleaning', 'cleaning', 'both', 'Move-in and move-out cleaning', 'Cleaning at both ends of the move.', 'cleaning_both', 150),
    ('Home decor', 'home_decor', 'yes', 'Home decor setup', 'Arrange and style your space after the move.', 'home_decor', 120),
    ('Storage', 'storage', '1 week', 'Short-term storage · 1 week', 'Secure storage for a short transition.', 'storage_week', 0),
    ('Storage', 'storage', '1 month', 'Storage · 1 month', 'Storage for a longer move transition.', 'storage_week', 0),
    ('Storage', 'storage', '3 months', 'Storage · 3 months', 'Extended storage while plans settle.', 'storage_week', 0),
)


def service_cards(booking):
    rates = {row.key: float(row.value) for row in ServicePricing.query.all()}
    recommendations = {item['key']: item for item in recommended_services(booking)}
    grouped = {}
    if booking.items:
        requirements = inventory_requirements(booking)
        grouped['Moving & Handling'] = [{
            'key': 'included', 'label': 'Loading and unloading crew',
            'description': 'Your move includes trained loading and unloading based on inventory volume and access.',
            'price': None, 'minutes': 0, 'selected': True, 'required': True,
            'recommendation': {'priority': 'required', 'reason': f"Crew size and handling time are calculated for {requirements['item_count']} inventoried items."},
        }]
        if any(getattr(booking, f'{place}_stairs', False) for place in ('pickup', 'destination')):
            grouped['Moving & Handling'].append({
                'key': 'included', 'label': 'Stair handling',
                'description': 'Crew size and handling time account for stair access at your property.',
                'price': None, 'minutes': 0, 'selected': True, 'required': True,
                'recommendation': {'priority': 'required', 'reason': 'Your location details indicate stair access.'},
            })
        if requirements['special_items']:
            grouped.setdefault('Special Handling', []).append({
                'key': 'included', 'label': 'Special-item handling',
                'description': 'Specialist handling is included for the items you marked or the system identified.',
                'price': requirements['special_items'] * rates.get('special_handling', 0),
                'minutes': requirements['special_items'] * 8, 'selected': True, 'required': True,
                'recommendation': {'priority': 'required', 'reason': f"{requirements['special_items']} item(s) require additional handling."},
            })
    for category, key, value, label, description, rate_key, minutes in SERVICE_CARD_CONFIG:
        if rate_key not in rates:
            continue
        saved = (booking.workflow_data or {}).get('services', {}).get(key)
        recommendation = recommendations.get(key)
        if key == 'packing' and value == 'full' and 'protective_wrapping' in recommendations:
            recommendation = recommendations['protective_wrapping']
        multiplier = {'1 month': 4, '3 months': 12}.get(value, 1)
        card = {
            'key': key, 'value': value, 'label': label, 'description': description,
            'price': rates[rate_key] * multiplier, 'minutes': minutes,
            'selected': saved == value,
            'required': False,
            'priority': recommendation['priority'] if recommendation else 'optional',
            'recommendation': recommendation if recommendation and recommendation.get('value') == value else None,
        }
        grouped.setdefault(category, []).append(card)
    return grouped


def _validated_addon_answers(services, source, *, primary_service=None, prefixed=False):
    selected = {
        key for key in ('packing', 'storage', 'cleaning', 'home_decor')
        if services.get(key) not in {None, '', 'none', 'no'}
        and key != primary_service
    }
    schemas = addon_questions_for(selected)
    if prefixed:
        for addon in {'packing', 'storage', 'cleaning', 'home_decor'} - set(schemas) - ({primary_service} if primary_service else set()):
            stale_keys = [
                key for key, value in source.items()
                if key.startswith(f'addon_{addon}_') and value not in (None, '', [])
            ]
            if stale_keys:
                raise ValueError(f"Select the {addon.replace('_', ' ')} service before submitting its answers.")
    else:
        submitted_addons = set((source or {}).keys())
        unsupported_addons = submitted_addons - set(schemas)
        if unsupported_addons:
            raise ValueError(f"Select the {', '.join(sorted(addon.replace('_', ' ') for addon in unsupported_addons))} service before submitting its answers.")
    validated = {}
    for addon, sections in schemas.items():
        addon_values = {}
        if not prefixed and not isinstance((source or {}).get(addon, {}), dict):
            raise ValueError(f'Answers for {addon.replace("_", " ")} must be an object.')
        submitted = (source or {}).get(addon, {}) if not prefixed else source
        if not prefixed:
            unknown_sections = set(submitted) - set(sections)
            if unknown_sections:
                raise ValueError(f'Unknown {addon.replace("_", " ")} answer sections: {", ".join(sorted(unknown_sections))}.')
        for section, schema in sections.items():
            if prefixed:
                answers = {
                    field['key']: source.get(f'addon_{addon}_{section}_{field["key"]}')
                    for field in schema
                    if f'addon_{addon}_{section}_{field["key"]}' in source
                }
            else:
                answers = submitted.get(section, {})
                if not isinstance(answers, dict):
                    raise ValueError(f'Answers for {addon.replace("_", " ")} {section} must be an object.')
            addon_values[section] = validate_question_answers(schema, answers)
        validated[addon] = addon_values
    return validated


def _question_form_values(schema, form, prefix):
    return {
        field['key']: (
            form.getlist(f'{prefix}{field["key"]}')
            if field['type'] == 'multiselect' else form.get(f'{prefix}{field["key"]}')
        )
        for field in schema
        if f'{prefix}{field["key"]}' in form
    }


def _store_inventory_answers(booking, answers, user_id=None):
    workflow_data = dict(booking.workflow_data or {})
    previous = workflow_data.get('inventory_answers', {})
    workflow_data['inventory_answers'] = answers
    booking.workflow_data = workflow_data
    record_event(booking, 'inventory_answers.updated', previous=previous, new=answers, user_id=user_id)


def _get_request(request_id):
    booking = Booking.query.filter_by(booking_request_id=request_id).first_or_404()
    if current_user.is_authenticated:
        if booking.customer_id is None and session.get('active_booking_request_id') == request_id:
            booking.customer_id = current_user.id
            db.session.commit()
        elif booking.customer_id != current_user.id and current_user.role not in {'admin', 'super_admin'}:
            abort(404)
    elif session.get('active_booking_request_id') != request_id:
        abort(404)
    return booking


def _lock_customer_booking_for_edit(booking):
    if current_user.role in {'admin', 'super_admin'}:
        return booking
    booking = db.session.query(Booking).filter_by(id=booking.id).with_for_update().populate_existing().one()
    locked_statuses = {'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'REVIEW_PENDING', 'REVIEWED'}
    if (
        booking.payment_status in {'Paid', 'Deposit Paid'}
        or booking.status in {'Confirmed', 'Assigned', 'In Progress', 'En Route', 'Arrived', 'Loading', 'In Transit', 'Unloading', 'Completed'}
        or booking.workflow_state in locked_statuses
        or booking.quote_status == 'Accepted'
    ):
        abort(409, description='This booking can no longer be edited online. Contact operations to request a reviewed change.')
    return booking


def _reject_locked_booking_api_edit(booking, *, allow_accepted_quote=False):
    if current_user.role in {'admin', 'super_admin'}:
        return None
    db.session.refresh(booking, with_for_update=True)
    if (
        booking.payment_status in {'Paid', 'Deposit Paid'}
        or booking.status in {'Confirmed', 'Assigned', 'In Progress', 'En Route', 'Arrived', 'Loading', 'In Transit', 'Unloading', 'Completed'}
        or booking.workflow_state in {'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'REVIEW_PENDING', 'REVIEWED'}
        or (booking.quote_status == 'Accepted' and not allow_accepted_quote)
    ):
        return jsonify({
            'success': False,
            'error': 'This booking can no longer be edited online. Contact operations to request a reviewed change.',
        }), 409
    return None


def _next_stage(booking):
    if booking.primary_service == 'support' and booking.workflow_state in {'INVENTORY_COMPLETED', 'QUOTE_READY'}:
        return 'quote'
    if booking.primary_service == 'logistics' and booking.workflow_state == 'LOCATION_COMPLETED':
        return 'inventory'
    if booking.primary_service == 'cleaning' and booking.workflow_state == 'INVENTORY_COMPLETED':
        return 'schedule'
    states = {
        'LOCATION_COMPLETED': 'property', 'PROPERTY_COMPLETED': 'inventory',
        'INVENTORY_COMPLETED': 'services', 'SERVICES_COMPLETED': 'schedule',
        'SCHEDULE_COMPLETED': 'quote', 'QUOTE_READY': 'quote',
        'QUOTE_ACCEPTED': 'payment', 'PAYMENT_PENDING': 'payment',
        'CONFIRMED': 'payment', 'IN_PROGRESS': 'payment', 'COMPLETED': 'review',
        'REVIEW_PENDING': 'review', 'REVIEWED': 'review',
    }
    return states.get(booking.workflow_state, 'property')


def _stage_is_unlocked(booking, stage):
    progress = {
        'LOCATION_COMPLETED': 0, 'PROPERTY_COMPLETED': 1, 'INVENTORY_COMPLETED': 2,
        'SERVICES_COMPLETED': 3, 'SCHEDULE_COMPLETED': 4, 'QUOTE_READY': 5,
        'QUOTE_ACCEPTED': 6, 'PAYMENT_PENDING': 6, 'CONFIRMED': 6,
        'IN_PROGRESS': 6, 'COMPLETED': 7, 'REVIEW_PENDING': 7, 'REVIEWED': 7,
    }
    target = STAGES.index(stage)
    return progress.get(booking.workflow_state, -1) >= target - 1


def _location_payload(form, service_type, places=None):
    places = tuple(places) if places is not None else LOCATION_SERVICES[service_type]
    if not places:
        return {
            'route_status': 'NOT_REQUIRED', 'distance_precision': 'not_applicable',
            'service_area_status': 'NOT_REQUIRED', 'distance_km': 0.0,
        }
    fields = (
        'pickup_address', 'pickup_unit', 'pickup_area', 'pickup_area_council', 'pickup_district', 'pickup_neighborhood',
        'pickup_location_type', 'pickup_original_input', 'pickup_city', 'pickup_state', 'pickup_country', 'pickup_formatted_address',
        'pickup_normalized_location', 'pickup_location_id',
        'pickup_latitude', 'pickup_longitude', 'pickup_place_id', 'pickup_provider', 'pickup_provider_raw_id',
        'pickup_notes', 'pickup_access', 'pickup_inside_estate', 'pickup_narrow_road', 'pickup_parking_close', 'pickup_floor', 'pickup_stairs',
        'destination_address', 'destination_unit', 'destination_area', 'destination_area_council', 'destination_district',
        'destination_neighborhood', 'destination_location_type', 'destination_original_input',
        'destination_normalized_location', 'destination_city', 'destination_state', 'destination_country', 'destination_formatted_address',
        'destination_latitude', 'destination_longitude', 'destination_place_id', 'dropoff_location_id', 'destination_provider', 'destination_provider_raw_id',
        'destination_notes', 'destination_access', 'destination_inside_estate', 'destination_narrow_road', 'destination_parking_close', 'destination_floor', 'destination_stairs',
    )
    data = {
        field: getattr(form, field).data for field in fields
        if hasattr(form, field) and field.startswith(tuple(f'{place}_' for place in places))
    }
    for place in places:
        data[f'{place}_address'] = (
            data.get(f'{place}_address') or request.form.get(f'{place}_search', '').strip()
        )
    required = tuple(f'{place}_address' for place in places)
    missing = [field for field in required if not str(data.get(field) or '').strip()]
    if missing:
        raise ValueError('Enter both addresses, city and state to plan a move.' if len(places) == 2 else 'Enter the service location to continue.')
    for place in places:
        data[f'{place}_local_place_id'] = None
        data[f'{place}_original_input'] = (data.get(f'{place}_original_input') or data.get(f'{place}_address') or '')[:500]
        data[f'{place}_normalized_location'] = data[f'{place}_original_input']
        local_place = None
        if data.get(f'{place}_provider') == 'local-reference':
            local_place = get_local_place(data.get(f'{place}_place_id') or data.get(f'{place}_provider_raw_id'))
            if local_place is None:
                raise ValueError(f"The selected {place} location is no longer available. Search and choose it again.")
        if local_place:
            result = local_place
            data[f'{place}_local_place_id'] = local_place.get('local_place_id')
            data[f'{place}_provider'] = local_place.get('provider') or data.get(f'{place}_provider')
            for target, source in (
                ('latitude', 'latitude'), ('longitude', 'longitude'), ('formatted_address', 'formatted_address'),
                ('city', 'city'), ('state', 'state'), ('country', 'country'), ('area', 'area'),
                ('area_council', 'area_council'), ('district', 'district'), ('neighborhood', 'neighborhood'),
                ('location_type', 'location_type'), ('provider', 'provider'), ('provider_place_id', 'provider_place_id'),
            ):
                data[f'{place}_{target}'] = result.get(source) or data.get(f'{place}_{target}')
            data[f'{place}_nearest_landmark'] = result.get('nearest_landmark')
            data[f'{place}_nearest_road'] = result.get('nearest_road')
            data[f'{place}_route_resolution'] = 'exact' if result.get('source') != 'cached-user-location' else 'geocoded'
        else:
            result = geocode_address(data.get(f'{place}_original_input') or data[f'{place}_address'] or '')
            if result:
                if result.get('provider') == 'local-reference':
                    verified_local = get_local_place(result.get('place_id') or result.get('provider_place_id'))
                    if verified_local:
                        data[f'{place}_local_place_id'] = verified_local.get('local_place_id')
                        result = verified_local
                for target, source in (
                    ('formatted_address', 'formatted_address'), ('latitude', 'latitude'), ('longitude', 'longitude'),
                    ('city', 'city'), ('state', 'state'), ('country', 'country'), ('area', 'area'),
                    ('area_council', 'area_council'), ('district', 'district'), ('neighborhood', 'neighborhood'),
                    ('location_type', 'location_type'), ('place_id', 'provider_place_id'),
                    ('provider', 'provider'), ('provider_raw_id', 'provider_place_id'),
                ):
                    data[f'{place}_{target}'] = result.get(source) or data.get(f'{place}_{target}')
            else:
                data[f'{place}_latitude'] = None
                data[f'{place}_longitude'] = None
        coordinates = validate_coordinates(data.get(f'{place}_latitude'), data.get(f'{place}_longitude'))
        if coordinates:
            data[f'{place}_latitude'], data[f'{place}_longitude'] = coordinates
        else:
            data[f'{place}_latitude'] = data[f'{place}_longitude'] = None
        if not data.get(f'{place}_area_council') and coordinates:
            data[f'{place}_area_council'] = area_council_for_point(*coordinates)
        if not local_place:
            nearby = resolve_nearest_postgis_references(*coordinates) if coordinates else {}
            data[f'{place}_area_council'] = nearby.get('area_council') or data.get(f'{place}_area_council')
            data[f'{place}_district'] = data.get(f'{place}_district') or nearby.get('district')
            data[f'{place}_nearest_landmark'] = nearby.get('nearest_landmark') or (result.get('nearest_landmark') if result else None)
            data[f'{place}_nearest_road'] = nearby.get('nearest_road')
            data[f'{place}_route_resolution'] = (
                'exact' if local_place else 'geocoded' if coordinates and result
                else 'nearest_landmark' if nearby.get('nearest_landmark')
                else 'nearest_road' if nearby.get('nearest_road')
                else 'district_fallback' if nearby.get('district') else 'unresolved'
            )
            record_location_observation(
                data[f'{place}_original_input'], result={**(result or {}), **nearby},
                area_council=data.get(f'{place}_area_council'),
                district=data.get(f'{place}_district'),
                latitude=data.get(f'{place}_latitude'), longitude=data.get(f'{place}_longitude'),
                nearest_road=nearby.get('nearest_road'),
                route_resolution=data[f'{place}_route_resolution'],
            )
        data[f'{place}_city'] = data.get(f'{place}_city') or 'Abuja'
        data[f'{place}_state'] = data.get(f'{place}_state') or 'Federal Capital Territory'
        data[f'{place}_country'] = data.get(f'{place}_country') or 'Nigeria'
        hierarchy = [data.get(f'{place}_{key}') for key in ('neighborhood', 'district', 'area', 'area_council')]
        data[f'{place}_normalized_location'] = ', '.join(str(value).strip() for value in hierarchy if value) or str(data.get(f'{place}_formatted_address') or data.get(f'{place}_address') or '').strip()

    route = None
    needs_route = service_type in {'residential', 'commercial', 'logistics'} and len(places) == 2
    if needs_route and all(data.get(f'{place}_{axis}') is not None for place in places for axis in ('latitude', 'longitude')):
        route = route_distance(
            data['pickup_latitude'], data['pickup_longitude'],
            data['destination_latitude'], data['destination_longitude'],
            fallback=True,
        )
    data['distance_km'] = float(route['distance_km']) if route else 0.0
    data['route_duration_minutes'] = float(route['duration_minutes']) if route else None
    data['route_provider'] = route.get('provider') if route else None
    data['distance_source'] = route.get('distance_source') if route else None
    if route and route.get('distance_source') == 'approximate':
        data['distance_source'] = 'approximate-landmark-or-district'
    data['distance_precision'] = route.get('distance_precision') if route else 'unresolved'
    data['route_status'] = 'CALCULATED' if route else 'NOT_REQUIRED' if service_type in {'support', 'storage'} else 'PENDING'
    data['route_calculated_at'] = datetime.utcnow() if route else None
    data['service_area_status'] = 'SERVICEABLE' if needs_route and all(
        validate_service_area(data[f'{place}_latitude'], data[f'{place}_longitude']) == 'SERVICEABLE'
        for place in places
    ) else 'NOT_REQUIRED' if service_type in {'support', 'storage'} else 'PENDING_RESOLUTION' if len(places) == 1 else 'OUTSIDE_SERVICE_AREA'
    return data


def _location_access_payload(form):
    answers = {}
    for place in ('pickup', 'destination'):
        if not getattr(form, f'{place}_address').data:
            continue
        floor = getattr(form, f'{place}_floor').data or 'Ground'
        value = getattr(form, f'{place}_lift_available').data
        if floor != 'Ground':
            if value not in {'yes', 'no'}:
                raise ValueError(f'Confirm whether a lift is available at the above-ground {place} location.')
            answers[f'{place}_lift_available'] = value == 'yes'
        else:
            answers[f'{place}_lift_available'] = None
    return answers


@booking_bp.route('/start', methods=['GET', 'POST'])
@booking_bp.route('/start/<service_type>', methods=['GET', 'POST'])
@login_required
def start(service_type='residential'):
    try:
        service_type = get_service_workflow({'primary_service': service_type})['service_type']
    except ValueError:
        abort(404)
    session.pop('active_booking_request_id', None)
    if service_type == 'cleaning':
        if request.method == 'POST':
            subtype = request.form.get('cleaning_subtype', '')
            if subtype not in {'home', 'office', 'move-in-out', 'fumigation-pest-control'}:
                flash('Choose one of the available cleaning services.', 'error')
                return render_template('move_request/cleaning_options.html', selected=subtype)
            session['booking_cleaning_subtype'] = subtype
            return redirect(url_for('booking.location', service_type=service_type))
        return render_template('move_request/cleaning_options.html', selected='')
    if service_type == 'storage':
        if request.method == 'POST':
            collection = request.form.get('storage_collection')
            retrieval = request.form.get('storage_retrieval')
            return_known = request.form.get('storage_return_known', 'no') if retrieval == 'yes' else 'no'
            if collection not in {'yes', 'no'} or retrieval not in {'yes', 'no'} or return_known not in {'yes', 'no'}:
                flash('Choose whether collection and return delivery are needed.', 'error')
                return render_template('move_request/storage_setup.html', setup=request.form)
            session['booking_storage_setup'] = {
                'storage_collection': collection,
                'storage_retrieval': retrieval,
                'storage_return_known': return_known,
            }
            return redirect(url_for('booking.location', service_type='storage'))
        return render_template('move_request/storage_setup.html', setup={})
    addon = request.args.get('addon')
    initial_services = {
        'cleaning': {'cleaning': 'move-out'},
        'home_decor': {'home_decor': 'yes'},
    }
    if addon is not None and addon not in initial_services:
        abort(404)
    session['booking_initial_services'] = {
        'service_type': service_type,
        'services': initial_services.get(addon, {}),
    }
    booking_session_key = session.setdefault('_analytics_booking_session', secrets.token_urlsafe(12))
    queue_analytics_event('booking_started', dedupe_key=f'booking_started:{booking_session_key}', service_type=service_type)
    return redirect(url_for('booking.location', service_type=service_type))


@booking_bp.route('/')
@login_required
def index():
    request_id = session.get('active_booking_request_id')
    booking = Booking.query.filter_by(booking_request_id=request_id).first() if request_id else None
    if booking:
        return redirect(url_for('booking.workflow', request_id=request_id, stage=_next_stage(booking)))
    return redirect(url_for('booking.location'))


@booking_bp.route('/location', methods=['GET', 'POST'])
@booking_bp.route('/location/<service_type>', methods=['GET', 'POST'])
@login_required
def location(service_type='residential'):
    form = BookingLocationForm()
    workflow = get_service_workflow({'primary_service': service_type})
    location_places = location_places_for(service_type, session.get('booking_storage_setup'))
    if request.method == 'POST':
        if not form.validate():
            flash('Check the location details and try again.', 'error')
        else:
            try:
                location_data = _location_payload(form, service_type, location_places)
                active_request_id = session.get('active_booking_request_id')
                previous_booking = Booking.query.filter_by(
                    booking_request_id=active_request_id, customer_id=current_user.id,
                ).first() if active_request_id else None
                if previous_booking and previous_booking.primary_service == service_type and all(
                    getattr(previous_booking, field) == location_data.get(field)
                    for field in ('pickup_address', 'pickup_city', 'pickup_state', 'destination_address', 'destination_city', 'destination_state')
                ):
                    return redirect(url_for(
                        'booking.workflow', request_id=active_request_id,
                        stage=_next_stage(previous_booking),
                    ))
                booking = create_booking_request(
                    customer_id=current_user.id if current_user.is_authenticated else None,
                    location=location_data,
                    user_id=current_user.id if current_user.is_authenticated else None,
                    service_type=service_type,
                    location_access=_location_access_payload(form),
                    initial_service_answers=(
                        session.get('booking_storage_setup')
                        if service_type == 'storage' else
                        {'morph_cleaning_category': 'fumigation-pest-control'}
                        if service_type == 'cleaning' and session.get('booking_cleaning_subtype') == 'fumigation-pest-control' else
                        {'cleaning_subtype': session.get('booking_cleaning_subtype')}
                        if service_type == 'cleaning' and session.get('booking_cleaning_subtype') else None
                    ),
                    initial_services=(session.get('booking_initial_services') or {}).get('services', {})
                    if (session.get('booking_initial_services') or {}).get('service_type') == service_type else None,
                )
                session.pop('booking_initial_services', None)
                session.pop('booking_cleaning_subtype', None)
                session.pop('booking_storage_setup', None)
                session['active_booking_request_id'] = booking.booking_request_id
                queue_booking_conversion(booking, 'location_completed')
                return redirect(url_for('booking.workflow', request_id=booking.booking_request_id, stage='property'))
            except ValueError as error:
                flash(str(error), 'error')
    return render_template(
        'move_request/engine.html', stage='location', stages=service_stages(service_type), form=form, booking=None,
        service_workflow=workflow, service_type=workflow['service_type'],
        location_places=location_places,
    )


@booking_bp.route('/<request_id>/location', methods=['GET', 'POST'])
@login_required
def edit_location(request_id):
    booking = _get_request(request_id)
    if request.method == 'POST':
        booking = _lock_customer_booking_for_edit(booking)
    stored_answers = ((booking.workflow_data or {}).get('property') or {}).get('service_answers', {})
    location_places = location_places_for(booking.primary_service, stored_answers)
    had_calculation = bool(booking.calculation_data)
    form = BookingLocationForm()
    if request.method == 'GET':
        for field in form._fields:
            if hasattr(booking, field):
                getattr(form, field).data = getattr(booking, field)
    if request.method == 'POST':
        if not form.validate():
            flash('Enter both complete addresses and confirm each city and state.', 'error')
        else:
            try:
                location_data = _location_payload(form, booking.primary_service, location_places)
                location_access = _location_access_payload(form)
                previous = {'pickup': booking.pickup_formatted_address or booking.pickup_address,
                            'destination': booking.destination_formatted_address or booking.destination_address}
                for field, value in location_data.items():
                    setattr(booking, field, value)
                workflow_data = dict(booking.workflow_data or {})
                workflow_data['location_access'] = location_access
                booking.workflow_data = workflow_data
                invalidate_from(booking, 'location', current_user.id if current_user.is_authenticated else None)
                record_event(booking, 'location.updated', previous=previous,
                             new={'pickup': booking.pickup_formatted_address or booking.pickup_address,
                                  'destination': booking.destination_formatted_address or booking.destination_address},
                             user_id=current_user.id if current_user.is_authenticated else None)
                db.session.commit()
                if had_calculation:
                    flash('Your location changed. Vehicle, workforce, schedule, and quote estimates will be recalculated.', 'success')
                return redirect(url_for('booking.workflow', request_id=request_id, stage='property'))
            except ValueError as error:
                db.session.rollback()
                flash(str(error), 'error')
    return render_template('move_request/engine.html', stage='location', stages=service_stages(booking.primary_service), form=form, booking=booking,
                           service_workflow=get_service_workflow(booking), service_type=booking.primary_service,
                           location_places=location_places,
                           unlocked_stages=['location'] + [item for item in STAGES[1:] if _stage_is_unlocked(booking, item)])


@booking_bp.route('/resume/<request_id>')
@login_required
def resume(request_id):
    booking = Booking.query.filter_by(booking_request_id=request_id, customer_id=current_user.id).first_or_404()
    session['active_booking_request_id'] = booking.booking_request_id
    return redirect(url_for('booking.workflow', request_id=request_id, stage=_next_stage(booking)))


@booking_bp.route('/<request_id>/<stage>', methods=['GET', 'POST'])
@login_required
def workflow(request_id, stage):
    if stage not in STAGES[1:]:
        abort(404)
    booking = _get_request(request_id)
    if request.method == 'POST':
        booking = _lock_customer_booking_for_edit(booking)
    stages = service_stages(booking.primary_service, booking)
    if stage not in stages and not (booking.primary_service == 'logistics' and stage == 'property'):
        return redirect(url_for('booking.workflow', request_id=request_id, stage=_next_stage(booking)))
    if booking.primary_service == 'logistics' and stage == 'inventory' and booking.workflow_state == 'LOCATION_COMPLETED':
        save_workflow_section(booking, 'property', {}, current_user.id)
    if (stage in {'services', 'schedule', 'quote'}
            and booking.primary_service in {'residential', 'commercial'}
            and not inventory_is_confirmed(booking)):
        flash('Review and confirm your inventory before continuing to vehicle recommendations.', 'error')
        return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))
    if not _stage_is_unlocked(booking, stage):
        return redirect(url_for('booking.workflow', request_id=request_id, stage=_next_stage(booking)))
    if stage == 'services':
        try:
            validate_question_answers(
                booking_questions_for(booking, 'inventory'),
                (booking.workflow_data or {}).get('inventory_answers') or {},
            )
        except ValueError:
            flash('Complete the service-specific inventory questions before choosing additional services.', 'error')
            return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))
    if stage == 'review':
        if not current_user.is_authenticated or booking.customer_id != current_user.id:
            abort(404)
        return redirect(url_for('customer.submit_review', booking_id=booking.id))
    had_calculation = bool(booking.calculation_data)

    if request.method == 'POST':
        try:
            if stage == 'property':
                property_type = request.form.get('property_type', '')
                requires_property_type = booking.primary_service in MOVE_PROPERTY_SERVICES
                if request.form.get('action') == 'choose_property_type' and requires_property_type:
                    property_type = validate_property_type_for_service(booking.primary_service, property_type)
                    workflow_data = dict(booking.workflow_data or {})
                    previous = workflow_data.get('property', {})
                    workflow_data['property'] = {'property_type': property_type}
                    booking.workflow_data = workflow_data
                    record_event(booking, 'property.type_selected', previous=previous, new=workflow_data['property'],
                                 user_id=current_user.id if current_user.is_authenticated else None)
                    db.session.commit()
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='property'))
                if requires_property_type:
                    property_type = validate_property_type_for_service(booking.primary_service, property_type)
                service_schema = booking_questions_for(booking, 'property')
                service_answers = validate_question_answers(
                    service_schema, _question_form_values(service_schema, request.form, 'service_'),
                )
                values = {'service_answers': service_answers}
                if requires_property_type:
                    values['property_type'] = property_type
                    if booking.primary_service == 'residential':
                        has_bedrooms = property_type in {
                            'Apartment', 'Flat / Apartment', 'Self-contained', 'Duplex', 'House',
                            'Detached House', 'Semi-detached House', 'Bungalow', 'Terrace House',
                        }
                        values['bedrooms'] = request.form.get('bedrooms', type=int, default=0) if has_bedrooms else 0
                        values['bathrooms'] = request.form.get('bathrooms', type=int, default=0) if has_bedrooms else 0
                        values['floors'] = request.form.get('floors', type=int, default=1)
                        values['approximate_size_m2'] = request.form.get('approximate_size_m2', type=float)
                    else:
                        values['business_work_areas'] = service_answers['business_work_areas']
                values['service_answers'] = service_answers
                if any(values.get(key, 0) < 0 for key in ('bedrooms', 'bathrooms', 'business_work_areas')):
                    raise ValueError('Property and business work-area counts must be non-negative.')
                if booking.primary_service == 'residential' and values.get('floors', 1) < 1:
                    raise ValueError('Pickup property floors or storeys must be at least 1.')
                save_workflow_section(booking, 'property', values, current_user.id if current_user.is_authenticated else None)
                if had_calculation:
                    flash('Property details changed. Downstream handling, duration, and quote estimates were invalidated.', 'success')
                return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

            if stage == 'inventory':
                if request.form.get('action') == 'delete_selection':
                    selection_id = request.form.get('delete_selection_id', type=int)
                    selection = BookingInventory.query.filter_by(id=selection_id, booking_id=booking.id).first_or_404()
                    replace_inventory(booking, [
                        {'inventory_item_id': item.inventory_item_id, 'custom_name': item.custom_name,
                         'name': item.display_name, 'quantity': item.quantity, 'category': item.category,
                         'size': item.size, 'notes': item.notes, 'fragile': item.fragile,
                         'large': item.large, 'special_handling': item.special_handling,
                         'details': item.details, 'estimated_volume_m3': item.estimated_volume_m3,
                         'estimated_weight_kg': item.estimated_weight_kg}
                        for item in booking.inventory if item.id != selection_id
                    ], current_user.id if current_user.is_authenticated else None)
                    if had_calculation:
                        flash('Inventory changed. Downstream estimates were recalculated.', 'success')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'add_catalogue':
                    additions = []
                    for field_name, value in request.form.items():
                        if not field_name.startswith('inventory_item_id_'):
                            continue
                        catalogue_id = field_name.removeprefix('inventory_item_id_')
                        quantity_field = f'inventory_item_quantity_{catalogue_id}'
                        try:
                            quantity = int(request.form.get(quantity_field, 0))
                        except (TypeError, ValueError):
                            raise ValueError('Inventory quantity must be a whole number from 1 to 500.') from None
                        if quantity < 1 or quantity > 500:
                            raise ValueError('Inventory quantity must be between 1 and 500.')
                        additions.append((int(catalogue_id), quantity))
                    if not additions:
                        raise ValueError('Choose an inventory item to add.')
                    items = [
                        {
                            'inventory_item_id': item.inventory_item_id,
                            'custom_name': item.custom_name,
                            'name': item.display_name,
                            'quantity': item.quantity,
                            'category': item.category,
                            'size': item.size,
                            'notes': item.notes,
                            'fragile': item.fragile,
                            'large': item.large,
                            'special_handling': item.special_handling,
                            'details': item.details,
                            'estimated_volume_m3': item.estimated_volume_m3,
                            'estimated_weight_kg': item.estimated_weight_kg,
                        }
                        for item in booking.inventory
                    ]
                    for catalogue_id, quantity in additions:
                        matching = next((item for item in items if item['inventory_item_id'] == catalogue_id), None)
                        if matching:
                            matching['quantity'] = quantity
                        else:
                            catalog_item = InventoryItem.query.filter_by(id=catalogue_id, active=True).first()
                            if not catalog_item:
                                raise ValueError('Choose an active catalogue item.')
                            items.append({
                                'inventory_item_id': catalog_item.id,
                                'name': catalog_item.name,
                                'quantity': quantity,
                                'category': catalog_item.category,
                                'size': 'medium',
                                'fragile': catalog_item.fragile,
                                'special_handling': catalog_item.special_handling,
                                'details': catalog_item.description,
                                'estimated_volume_m3': catalog_item.estimated_volume_m3,
                                'estimated_weight_kg': catalog_item.estimated_weight_kg,
                            })
                    replace_inventory(booking, items, current_user.id if current_user.is_authenticated else None)
                    if had_calculation:
                        flash('Inventory changed. Downstream estimates were recalculated.', 'success')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'update_selection':
                    selection_ids = request.form.getlist('selection_id')
                    quantities = request.form.getlist('quantity[]')
                    if not selection_ids:
                        raise ValueError('Select an inventory item to update.')
                    if len(selection_ids) != len(quantities):
                        raise ValueError('Inventory row fields are incomplete.')
                    changes = []
                    for selection_id, quantity in zip(selection_ids, quantities):
                        try:
                            quantity = int(quantity)
                        except (TypeError, ValueError):
                            raise ValueError('Inventory quantities must be whole numbers from 1 to 500.') from None
                        if quantity < 1 or quantity > 500:
                            raise ValueError('Inventory quantities must be between 1 and 500.')
                        item = next((item for item in booking.inventory if item.id == int(selection_id)), None)
                        if item is None:
                            raise ValueError('One or more selected inventory items are no longer available.')
                        changes.append((item.id, quantity, item.size))
                    update_inventory_selections(
                        booking, changes, current_user.id if current_user.is_authenticated else None,
                    )
                    if had_calculation:
                        flash('Inventory changed. Downstream estimates were recalculated.', 'success')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'upload_photo':
                    selection_ids = request.form.getlist('selection_id')
                    uploaded = False
                    for selection_id in selection_ids:
                        try:
                            item_id = int(selection_id)
                        except (TypeError, ValueError):
                            raise ValueError('Choose a valid inventory item for the photo.') from None
                        photo = request.files.get(f'photo_{item_id}')
                        if photo and photo.filename:
                            save_inventory_photo(
                                booking, item_id, photo,
                                current_user.id if current_user.is_authenticated else None,
                            )
                            uploaded = True
                    if not uploaded:
                        raise ValueError('Choose an image before uploading it.')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'add_bulk':
                    property_data = (booking.workflow_data or {}).get('property', {})
                    property_type = property_data.get('property_type') or booking.property_type
                    bulk_options = BULK_ITEM_OPTIONS.get(property_type, {})
                    bulk_name = request.form.get('bulk_item_type', '')
                    bulk_unit = request.form.get('bulk_unit', '')
                    if bulk_name not in bulk_options or bulk_options[bulk_name] != bulk_unit:
                        raise ValueError('Choose a valid bulk inventory type and unit.')
                    try:
                        quantity = int(request.form.get('bulk_quantity', ''))
                    except (TypeError, ValueError):
                        raise ValueError('Bulk quantity must be a whole number from 1 to 500.') from None
                    if quantity < 1 or quantity > 500:
                        raise ValueError('Bulk quantity must be between 1 and 500.')
                    estimates = {}
                    for field, limit in (('bulk_volume_m3', 100), ('bulk_weight_kg', 10000)):
                        raw_value = request.form.get(field, '').strip()
                        if not raw_value:
                            estimates[field] = None
                            continue
                        try:
                            estimate = float(raw_value)
                        except (TypeError, ValueError):
                            raise ValueError(f'Enter a valid approximate {field.removeprefix("bulk_").replace("_", " ")}.') from None
                        if estimate <= 0 or estimate > limit:
                            raise ValueError(f'Approximate {field.removeprefix("bulk_").replace("_", " ")} must be greater than zero and no more than {limit}.')
                        estimates[field] = estimate
                    display_name = f'{bulk_name} ({bulk_unit})'
                    items = [{
                        'inventory_item_id': item.inventory_item_id,
                        'custom_name': item.custom_name,
                        'name': item.display_name,
                        'quantity': item.quantity,
                        'category': item.category,
                        'size': item.size,
                        'notes': item.notes,
                        'fragile': item.fragile,
                        'large': item.large,
                        'special_handling': item.special_handling,
                        'details': item.details,
                        'estimated_volume_m3': item.estimated_volume_m3,
                        'estimated_weight_kg': item.estimated_weight_kg,
                    } for item in booking.inventory]
                    items.append({
                        'name': display_name,
                        'custom_name': display_name,
                        'quantity': quantity,
                        'category': 'Bulk stock' if property_type == 'Warehouse' else 'Construction materials',
                        'size': 'medium',
                        'notes': f'Unit: {bulk_unit}',
                        'fragile': request.form.get('bulk_fragile') == 'on',
                        'special_handling': request.form.get('bulk_special_handling') == 'on',
                        'estimated_volume_m3': estimates['bulk_volume_m3'],
                        'estimated_weight_kg': estimates['bulk_weight_kg'],
                    })
                    replace_inventory(booking, items, current_user.id if current_user.is_authenticated else None)
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'add_custom':
                    custom_names = request.form.getlist('custom_item_name[]')
                    custom_quantities = request.form.getlist('custom_item_quantity[]')
                    items = [
                        {
                            'inventory_item_id': item.inventory_item_id,
                            'custom_name': item.custom_name,
                            'name': item.display_name,
                            'quantity': item.quantity,
                            'category': item.category,
                            'size': item.size,
                            'notes': item.notes,
                            'fragile': item.fragile,
                            'large': item.large,
                            'special_handling': item.special_handling,
                            'details': item.details,
                            'estimated_volume_m3': item.estimated_volume_m3,
                            'estimated_weight_kg': item.estimated_weight_kg,
                        }
                        for item in booking.inventory
                    ]
                    for index, custom_name in enumerate(custom_names):
                        name = custom_name.strip()
                        if not name:
                            continue
                        items.append({
                            'custom_name': name, 'name': name,
                            'quantity': int(custom_quantities[index]) if index < len(custom_quantities) else 1,
                            'category': 'Custom item', 'size': 'medium',
                        })
                    if not items:
                        raise ValueError('Enter a custom item name.')
                    replace_inventory(booking, items, current_user.id if current_user.is_authenticated else None)
                    if had_calculation:
                        flash('Inventory changed. Downstream estimates were recalculated.', 'success')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))

                if request.form.get('action') == 'confirm_inventory':
                    if inventory_is_confirmed(booking):
                        return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))
                    if not booking.inventory:
                        flash('Add at least one inventory item before confirming your inventory.', 'error')
                        return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))
                    inventory_schema = booking_questions_for(booking, 'inventory')
                    inventory_answers = validate_question_answers(
                        inventory_schema, _question_form_values(inventory_schema, request.form, 'inventory_'),
                    )
                    selection_ids = request.form.getlist('selection_id[]') or request.form.getlist('selection_id')
                    quantities = request.form.getlist('quantity[]')
                    if not selection_ids or len(selection_ids) != len(quantities):
                        raise ValueError('Review each selected item and enter a quantity from 1 to 500.')
                    existing = {str(item.id): item for item in booking.inventory}
                    changes = []
                    for selection_id, raw_quantity in zip(selection_ids, quantities):
                        item = existing.get(selection_id)
                        if item is None:
                            raise ValueError('One or more selected items no longer belong to this booking.')
                        try:
                            quantity = int(raw_quantity)
                        except (TypeError, ValueError):
                            raise ValueError(f'Enter a whole-number quantity for {item.display_name}.') from None
                        if str(raw_quantity).strip() != str(quantity) or not 1 <= quantity <= 500:
                            raise ValueError(f'Quantity for {item.display_name} must be between 1 and 500.')
                        changes.append((item.id, quantity, item.size))
                    heavy_items = []
                    heavy_names = request.form.getlist('heavy_item_name[]')
                    heavy_types = request.form.getlist('heavy_item_type[]')
                    heavy_quantities = request.form.getlist('heavy_item_quantity[]')
                    heavy_weights = request.form.getlist('heavy_item_weight_kg[]')
                    heavy_lengths = request.form.getlist('heavy_item_length_cm[]')
                    heavy_widths = request.form.getlist('heavy_item_width_cm[]')
                    heavy_heights = request.form.getlist('heavy_item_height_cm[]')
                    heavy_special = request.form.getlist('heavy_item_special_handling[]')
                    if inventory_answers.get('heavy_or_oversized') != 'yes':
                        heavy_names = []
                        heavy_types = heavy_quantities = heavy_weights = []
                        heavy_lengths = heavy_widths = heavy_heights = heavy_special = []
                    row_values = [heavy_types, heavy_quantities, heavy_weights, heavy_lengths, heavy_widths, heavy_heights, heavy_special]
                    if any(len(values) != len(heavy_names) for values in row_values):
                        raise ValueError('Complete all fields for each heavy or oversized item.')
                    if len(heavy_names) > 25:
                        raise ValueError('Add no more than 25 heavy or oversized item rows.')
                    for index, raw_name in enumerate(heavy_names):
                        item_name = raw_name.strip()
                        item_type = heavy_types[index]
                        if item_type == 'other' and not item_name:
                            raise ValueError('Enter a name for each item marked Other.')
                        if item_type != 'other':
                            item_name = item_type.replace('-', ' ').title()
                        if not item_name:
                            continue
                        try:
                            quantity = int(heavy_quantities[index])
                        except (TypeError, ValueError):
                            raise ValueError(f'Enter a whole-number quantity for {item_name}.') from None
                        if not 1 <= quantity <= 500:
                            raise ValueError(f'Quantity for {item_name} must be between 1 and 500.')
                        row = {'name': item_name[:120], 'quantity': quantity,
                               'special_handling': heavy_special[index] == 'yes'}
                        weight_raw = heavy_weights[index].strip()
                        if weight_raw:
                            try:
                                weight = float(weight_raw)
                            except (TypeError, ValueError):
                                raise ValueError(f'Enter a valid approximate weight for {item_name}.') from None
                            if not 0 < weight <= 10000:
                                raise ValueError(f'Approximate weight for {item_name} must be greater than 0 and no more than 10,000 kg.')
                            row['weight_kg'] = weight
                        dimension_values = [heavy_lengths[index].strip(), heavy_widths[index].strip(), heavy_heights[index].strip()]
                        if any(dimension_values) and not all(dimension_values):
                            raise ValueError(f'Enter all three dimensions for {item_name}, or leave them all blank.')
                        if all(dimension_values):
                            dimensions = []
                            for raw_dimension in dimension_values:
                                try:
                                    dimension = float(raw_dimension)
                                except (TypeError, ValueError):
                                    raise ValueError(f'Enter valid dimensions for {item_name}.') from None
                                if not 0 < dimension <= 100000:
                                    raise ValueError(f'Dimensions for {item_name} must be greater than 0 and no more than 100,000 cm.')
                                dimensions.append(dimension)
                            row['dimensions_cm'] = dict(zip(('length', 'width', 'height'), dimensions))
                        heavy_items.append(row)
                    if inventory_answers.get('heavy_or_oversized') == 'yes' and not heavy_items:
                        raise ValueError('Add at least one heavy or oversized item, or change the answer to No.')
                    update_inventory_selections(
                        booking, changes, current_user.id if current_user.is_authenticated else None,
                    )
                    _store_inventory_answers(
                        booking, inventory_answers, current_user.id if current_user.is_authenticated else None,
                    )
                    workflow_data = dict(booking.workflow_data or {})
                    previous_heavy_items = workflow_data.get('heavy_items', [])
                    workflow_data['heavy_items'] = heavy_items
                    booking.workflow_data = workflow_data
                    record_event(booking, 'inventory.heavy_items_updated', previous=previous_heavy_items,
                                 new=heavy_items, user_id=current_user.id if current_user.is_authenticated else None)
                    newly_confirmed = confirm_inventory(
                        booking, current_user.id if current_user.is_authenticated else None,
                    )
                    if newly_confirmed:
                        queue_booking_conversion(booking, 'inventory_completed')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))

                if request.form.get('action') == 'save_and_continue':
                    if booking.primary_service in {'residential', 'commercial'}:
                        raise ValueError('Review and confirm the inventory before continuing.')
                    if inventory_is_confirmed(booking):
                        target_stage = 'schedule' if booking.primary_service == 'cleaning' else 'quote' if booking.primary_service == 'support' else 'services'
                        return redirect(url_for('booking.workflow', request_id=request_id, stage=target_stage))
                    inventory_schema = booking_questions_for(booking, 'inventory')
                    inventory_answers = validate_question_answers(
                        inventory_schema, _question_form_values(inventory_schema, request.form, 'inventory_'),
                    )
                    selection_ids = request.form.getlist('selection_id[]')
                    if booking.primary_service in {'cleaning', 'support', 'logistics', 'packing'}:
                        if booking.primary_service == 'packing' and selection_ids:
                            quantities = request.form.getlist('quantity[]')
                            if len(selection_ids) != len(quantities):
                                raise ValueError('Review each selected item and enter a quantity from 1 to 500.')
                            existing = {str(item.id): item for item in booking.inventory}
                            changes = []
                            for selection_id, raw_quantity in zip(selection_ids, quantities):
                                item = existing.get(selection_id)
                                if item is None:
                                    raise ValueError('One or more selected items no longer belong to this booking.')
                                try:
                                    quantity = int(raw_quantity)
                                except (TypeError, ValueError):
                                    raise ValueError('Inventory quantities must be whole numbers from 1 to 500.') from None
                                if str(raw_quantity).strip() != str(quantity) or not 1 <= quantity <= 500:
                                    raise ValueError(f'Quantity for {item.display_name} must be between 1 and 500.')
                                changes.append((item.id, quantity, item.size))
                            update_inventory_selections(
                                booking, changes, current_user.id if current_user.is_authenticated else None,
                            )
                        if booking.primary_service == 'support':
                            from ..models import SupportRequest
                            if SupportRequest.query.filter_by(booking_id=booking.id).first():
                                return redirect(url_for('booking.workflow', request_id=request_id, stage='quote'))
                        service_uploads = [upload for upload in request.files.getlist('service_photos') if upload and upload.filename]
                        if len(booking.photos) + len(service_uploads) > current_app.config['MAX_BOOKING_ATTACHMENTS']:
                            raise ValueError('This booking has reached its image limit.')
                        saved_photos = []
                        for upload in service_uploads:
                            saved = save_image(upload, 'booking', current_app.config['MAX_IMAGE_SIZE'])
                            saved_photos.append(BookingPhoto(
                                booking_id=booking.id,
                                uploaded_by_id=current_user.id,
                                file_name=saved['file_url'], original_filename=saved['original_filename'],
                                mime_type=saved['mime_type'], file_size=saved['file_size'],
                                category=f'service_{booking.primary_service}',
                                sort_order=len(booking.photos) + len(saved_photos),
                            ))
                        db.session.add_all(saved_photos)
                        _store_inventory_answers(booking, inventory_answers, current_user.id)
                        if booking.primary_service == 'cleaning':
                            confirm_inventory(booking, current_user.id)
                            save_workflow_section(booking, 'services', {}, current_user.id)
                            return redirect(url_for('booking.workflow', request_id=request_id, stage='schedule'))
                        if booking.primary_service == 'packing':
                            confirm_inventory(booking, current_user.id)
                            return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))
                        if booking.primary_service == 'logistics':
                            confirm_inventory(booking, current_user.id)
                            return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))

                        from ..services.support_chat_service import create_conversation, request_human
                        conversation = create_conversation(current_user.id, booking_id=booking.id)
                        request_human(
                            conversation.id, current_user.id,
                            'Urgency: {urgency}. Category: {category}. {description} On-site: {onsite}. Location: {location}'.format(
                                urgency=(booking.workflow_data.get('property') or {}).get('service_answers', {}).get('support_urgency', 'unspecified'),
                                category=(booking.workflow_data.get('property') or {}).get('service_answers', {}).get('support_category', 'other'),
                                description=(booking.workflow_data.get('property') or {}).get('service_answers', {}).get('support_description', ''),
                                onsite=(booking.workflow_data.get('property') or {}).get('service_answers', {}).get('onsite_required', 'no'),
                                location=(booking.workflow_data.get('property') or {}).get('service_answers', {}).get('support_location', 'not required'),
                            ),
                        )
                        booking.status = 'Support Request Submitted'
                        booking.quote_status = 'Quote Required'
                        booking.workflow_state = 'QUOTE_READY'
                        confirm_inventory(booking, current_user.id)
                        booking.calculation_data = {
                            'manual_review_flags': ['Support availability and callback timing must be confirmed by the operations team.'],
                            'estimate_type': 'quote_required',
                        }
                        db.session.commit()
                        return redirect(url_for('booking.workflow', request_id=request_id, stage='quote'))

                    quantities = request.form.getlist('quantity[]')
                    if not selection_ids or len(selection_ids) != len(quantities):
                        raise ValueError('Keep at least one inventory item and provide a quantity for each.')
                    existing = {str(item.id): item for item in booking.inventory}
                    changes = []
                    for selection_id, raw_quantity in zip(selection_ids, quantities):
                        item = existing.get(selection_id)
                        if item is None:
                            raise ValueError('One or more selected inventory items are no longer available.')
                        try:
                            quantity = int(raw_quantity)
                        except (TypeError, ValueError):
                            raise ValueError('Inventory quantities must be whole numbers from 1 to 500.') from None
                        if quantity < 1 or quantity > 500:
                            raise ValueError('Inventory quantities must be between 1 and 500.')
                        changes.append((item.id, quantity, item.size))
                    update_inventory_selections(
                        booking, changes, current_user.id if current_user.is_authenticated else None,
                    )
                    _store_inventory_answers(booking, inventory_answers, current_user.id if current_user.is_authenticated else None)
                    if booking.primary_service == 'packing':
                        confirm_inventory(booking, current_user.id)
                        return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))
                    selected_vehicle = request.form.get('vehicle_type_id')
                    if selected_vehicle:
                        save_vehicle_selection(booking, selected_vehicle, current_user.id if current_user.is_authenticated else None)
                    db.session.commit()
                    queue_booking_conversion(booking, 'inventory_completed')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))

                catalogue_ids = request.form.getlist('inventory_item_id[]')
                quantities = request.form.getlist('inventory_quantity[]')
                custom_names = request.form.getlist('custom_item_name[]')
                custom_quantities = request.form.getlist('custom_item_quantity[]')
                items = []
                for index, catalogue_id in enumerate(catalogue_ids):
                    try:
                        item_id = int(catalogue_id)
                    except (TypeError, ValueError):
                        raise ValueError('Choose a valid catalogue item.') from None
                    items.append({'inventory_item_id': item_id, 'quantity': int(quantities[index]) if index < len(quantities) else 1})
                for index, custom_name in enumerate(custom_names):
                    if not custom_name.strip():
                        continue
                    items.append({
                        'custom_name': custom_name.strip(), 'name': custom_name.strip(),
                        'quantity': int(custom_quantities[index]) if index < len(custom_quantities) else 1,
                        'category': 'Custom item', 'size': 'medium',
                    })
                if not items:
                    legacy_names = request.form.getlist('item_name[]') or request.form.getlist('item_name')
                    legacy_quantities = request.form.getlist('item_quantity[]') or request.form.getlist('item_quantity')
                    legacy_categories = request.form.getlist('item_category[]') or request.form.getlist('item_category')
                    legacy_sizes = request.form.getlist('item_size[]') or request.form.getlist('item_size')
                    items = [{
                        'name': name, 'quantity': int(legacy_quantities[index]) if index < len(legacy_quantities) else 1,
                        'category': legacy_categories[index] if index < len(legacy_categories) else 'Other',
                        'size': legacy_sizes[index] if index < len(legacy_sizes) else 'medium',
                    } for index, name in enumerate(legacy_names) if name.strip()]
                replace_inventory(booking, items, current_user.id if current_user.is_authenticated else None)
                raw_inventory_answers = _question_form_values(
                    booking_questions_for(booking, 'inventory'), request.form, 'inventory_',
                )
                if raw_inventory_answers:
                    inventory_answers = validate_question_answers(
                        booking_questions_for(booking, 'inventory'), raw_inventory_answers,
                    )
                    _store_inventory_answers(booking, inventory_answers, current_user.id if current_user.is_authenticated else None)
                    db.session.commit()
                legacy_photo = request.files.get('item_photo_0')
                if legacy_photo and legacy_photo.filename:
                    saved = save_image(legacy_photo, f'inventory/{booking.id}', current_app.config['MAX_IMAGE_SIZE'])
                    if booking.items and booking.inventory:
                        booking.items[0].photo_path = saved['file_url']
                        db.session.add(BookingPhoto(
                            booking_id=booking.id, booking_item_id=booking.items[0].id,
                            booking_inventory_id=booking.inventory[0].id,
                            uploaded_by_id=current_user.id if current_user.is_authenticated else None,
                            file_name=saved['file_url'], original_filename=saved['original_filename'],
                            mime_type=saved['mime_type'], file_size=saved['file_size'], category='inventory',
                            sort_order=0, is_primary=True,
                        ))
                        db.session.commit()
                if booking.primary_service in {'residential', 'commercial', 'packing'}:
                    flash('Your inventory draft is saved. Review and confirm it before continuing.', 'success')
                    return redirect(url_for('booking.workflow', request_id=request_id, stage='inventory'))
                selected_vehicle = request.form.get('vehicle_type_id')
                if selected_vehicle:
                    save_vehicle_selection(booking, selected_vehicle, current_user.id if current_user.is_authenticated else None)
                queue_booking_conversion(booking, 'inventory_completed')
                if had_calculation:
                    flash('Inventory or vehicle selection changed. Vehicle requirements, workforce, service time, and quote were recalculated.', 'success')
                return redirect(url_for('booking.workflow', request_id=request_id, stage='services'))

            if stage == 'services':
                services = {}
                for key, allowed in SERVICE_OPTIONS.items():
                    value = request.form.get(key, 'none' if key in {'packing', 'storage', 'cleaning', 'home_decor'} else 'no')
                    if value not in allowed:
                        raise ValueError(f'Choose a valid option for {key.replace("_", " ")}.')
                    if value not in {'none', 'no'}:
                        services[key] = value
                for key in request.form.getlist('recommendation'):
                    if key in {'packing', 'assembly', 'disassembly'}:
                        services[key] = 'partial' if key == 'packing' else 'yes'
                addon_answers = _validated_addon_answers(
                    services, request.form, primary_service=booking.primary_service, prefixed=True,
                )
                selected_vehicle = request.form.get('vehicle_type_id')
                if booking.primary_service in {'residential', 'commercial'}:
                    if not selected_vehicle:
                        selected_vehicle = next(
                            (str(option['id']) for option in vehicle_options(booking) if option['recommended']),
                            None,
                        )
                    if not selected_vehicle:
                        raise ValueError('No eligible vehicle recommendation is available. Contact operations to review your inventory.')
                service_photo_files = []
                for addon in addon_answers:
                    service_photo_files.extend((addon, upload) for upload in request.files.getlist(f'{addon}_photos') if upload and upload.filename)
                if len(booking.photos) + len(service_photo_files) > current_app.config['MAX_BOOKING_ATTACHMENTS']:
                    raise ValueError('This booking has reached its image limit.')
                saved_service_photos = []
                for addon, upload in service_photo_files:
                    saved = save_image(upload, 'booking', current_app.config['MAX_IMAGE_SIZE'])
                    saved_service_photos.append(BookingPhoto(
                        booking_id=booking.id, uploaded_by_id=current_user.id if current_user.is_authenticated else None,
                        file_name=saved['file_url'], original_filename=saved['original_filename'],
                        mime_type=saved['mime_type'], file_size=saved['file_size'],
                        category=f'service_{addon}',
                        sort_order=BookingPhoto.query.filter_by(booking_id=booking.id, category=f'service_{addon}').count(),
                    ))
                save_workflow_section(booking, 'services', services, current_user.id if current_user.is_authenticated else None)
                if selected_vehicle:
                    save_vehicle_selection(booking, selected_vehicle, current_user.id if current_user.is_authenticated else None)
                workflow_data = dict(booking.workflow_data or {})
                previous_addon_answers = workflow_data.get('addon_answers', {})
                workflow_data['addon_answers'] = addon_answers
                active_addons = {
                    key for key in ('packing', 'storage', 'cleaning', 'home_decor')
                    if services.get(key) not in {None, '', 'none', 'no'} and key != booking.primary_service
                }
                for addon in set(previous_addon_answers) - active_addons:
                    record_event(booking, 'addon_answers.cleared', previous=previous_addon_answers[addon],
                                 new={}, user_id=current_user.id if current_user.is_authenticated else None)
                booking.workflow_data = workflow_data
                db.session.add_all(saved_service_photos)
                record_event(booking, 'addon_answers.updated', previous=previous_addon_answers,
                             new=addon_answers, user_id=current_user.id if current_user.is_authenticated else None)
                booking.workflow_state = 'SERVICES_COMPLETED'
                db.session.commit()
                queue_booking_conversion(booking, 'services_selected')
                if had_calculation:
                    flash('Services changed. Estimated duration and quote were invalidated; review the updated schedule.', 'success')
                return redirect(url_for('booking.workflow', request_id=request_id, stage='schedule'))

            if stage == 'schedule':
                selected_date = date.fromisoformat(request.form.get('move_date', ''))
                selected_time = request.form.get('preferred_time', '')
                if selected_date < date.today() or selected_time not in {'Morning', 'Afternoon', 'Evening'}:
                    raise ValueError('Choose a future date and valid preferred time.')
                selected_vehicle = request.form.get('vehicle_type_id')
                if not selected_vehicle and booking.primary_service in {'residential', 'commercial', 'logistics'}:
                    selected_vehicle = str(((booking.workflow_data or {}).get('vehicle') or {}).get('selected_type_id') or '')
                if not selected_vehicle and booking.primary_service in {'residential', 'commercial', 'logistics'}:
                    raise ValueError('Choose a vehicle type before continuing to your quote.')
                saved_vehicle_id = ((booking.workflow_data or {}).get('vehicle') or {}).get('selected_type_id')
                if selected_vehicle and str(saved_vehicle_id or '') != str(selected_vehicle):
                    save_vehicle_selection(
                        booking, selected_vehicle,
                        current_user.id if current_user.is_authenticated else None,
                    )
                save_workflow_section(booking, 'schedule', {
                    'date': selected_date.isoformat(), 'time': selected_time,
                    'flexible_date': request.form.get('flexible_date') == 'on',
                    'flexible_time': request.form.get('flexible_time') == 'on',
                    'access_hours': request.form.get('access_hours', '')[:250],
                }, current_user.id if current_user.is_authenticated else None)
                queue_booking_conversion(booking, 'schedule_completed')
                if had_calculation:
                    flash('Schedule changed. Availability and quote are being recalculated from the current move details.', 'success')
                return redirect(url_for('booking.workflow', request_id=request_id, stage='quote'))

            if stage == 'quote' and request.form.get('action') == 'accept_quote':
                accept_quote(
                    booking, current_user.id if current_user.is_authenticated else None,
                    policy_acknowledged=request.form.get('policy_acknowledged') == 'on',
                )
                if booking.primary_service == 'home_decor' and booking.home_decor_request:
                    decor_request = booking.home_decor_request
                    previous = decor_request.status
                    decor_request.status = 'Approved'
                    booking.workflow_data = {**(booking.workflow_data or {}), 'home_decor_request': {
                        'reference': decor_request.public_reference, 'status': decor_request.status,
                    }}
                    record_event(booking, 'home_decor.quote_accepted', previous={'status': previous},
                                 new={'status': decor_request.status}, user_id=current_user.id)
                    db.session.commit()
                return redirect(url_for('booking.workflow', request_id=request_id, stage='payment'))
            raise ValueError('This action is not valid for this workflow step.')
        except (ValueError, TypeError) as error:
            db.session.rollback()
            flash(str(error), 'error')

    if stage == 'quote' and booking.primary_service == 'home_decor' and booking.home_decor_request:
        decor_request = booking.home_decor_request
        if decor_request.status == 'Quotation Prepared' and booking.quote_status == 'Ready':
            previous = decor_request.status
            decor_request.status = 'Awaiting Client Approval'
            booking.workflow_data = {**(booking.workflow_data or {}), 'home_decor_request': {
                'reference': decor_request.public_reference, 'status': decor_request.status,
            }}
            record_event(booking, 'home_decor.quote_sent_for_approval',
                         previous={'status': previous}, new={'status': decor_request.status},
                         user_id=current_user.id if current_user.is_authenticated else None)
            db.session.commit()
    if stage == 'quote' and booking.workflow_state == 'SCHEDULE_COMPLETED' and booking.primary_service != 'home_decor':
        try:
            calculate_quote(booking, current_user.id if current_user.is_authenticated else None)
            queue_booking_conversion(booking, 'quote_generated')
        except ValueError as error:
            flash(str(error), 'error')
    safety_factor = ServicePricing.query.filter_by(key='volume_safety_factor').first()
    property_data = (booking.workflow_data or {}).get('property', {})
    property_answers = property_data.get('service_answers', {})
    if booking.primary_service == 'packing':
        inventory_property_type = 'Office' if property_answers.get('packing_for') == 'business' else 'Apartment'
    elif booking.primary_service in {'storage', 'logistics'}:
        inventory_property_type = None
    else:
        inventory_property_type = property_data.get('property_type') or booking.property_type
    estate_unit_type = request.args.get('unit_type', '')
    render_stages = stages
    if booking.primary_service == 'logistics' and stage == 'property':
        render_stages = ('location', 'property', 'inventory', 'services', 'schedule', 'quote')
    return render_template(
        'move_request/engine.html', stage=stage, stages=render_stages, booking=booking,
        now_date=date.today().isoformat(),
        unlocked_stages=['location'] + [item for item in STAGES[1:] if _stage_is_unlocked(booking, item)],
        service_workflow=get_service_workflow(booking), service_type=booking.primary_service,
        property_types=property_types_for_service(booking.primary_service), service_options=SERVICE_OPTIONS,
        recommendations=recommended_services(booking),
        service_questions=booking_questions_for(booking, 'property'),
        inventory_questions=booking_questions_for(booking, 'inventory'),
        addon_questions=addon_questions_for(
            {'packing', 'storage', 'cleaning', 'home_decor'} - {booking.primary_service},
        ),
        service_cards=service_cards(booking),
        inventory_suggestions=suggested_inventory(booking),
        inventory_catalog=catalogue_items_for_service(
            booking.primary_service, property_type=inventory_property_type, unit_type=estate_unit_type,
        ),
        inventory_property_type=inventory_property_type,
        estate_unit_type=estate_unit_type,
        bulk_item_options=BULK_ITEM_OPTIONS.get(inventory_property_type, {}),
        inventory_selection=booking.inventory,
        inventory_item_estimates={
            item.id: (item.estimated_volume_m3, item.estimated_weight_kg) for item in booking.inventory
        } if booking.inventory else {},
        inventory_summary=(
            inventory_requirements(booking)
            if booking.inventory or booking.items
            else {'item_count': 0, 'total_volume_m3': 0, 'total_weight_kg': 0}
        ),
        inventory_safety_factor=float(safety_factor.value) if safety_factor else 1.15,
        vehicle_options=vehicle_options(booking),
        schedule_estimate=preview_move_duration(booking) if stage == 'schedule' and booking.items else None,
        quote_lines=BookingQuoteLine.query.filter_by(booking_id=booking.id).order_by(BookingQuoteLine.id).all(),
        cleaning_residential_rates=RESIDENTIAL_MOVE_CLEAN,
        cleaning_commercial_rates=COMMERCIAL_MOVE_CLEAN,
        cleaning_couch_rates=COUCH_UNIT_PRICES,
        cleaning_bedframe_rates=BEDFRAME_UNIT_PRICES,
        cleaning_appliance_rate=APPLIANCE_UNIT_PRICE,
    )


@booking_bp.route('/<request_id>/inventory/photos/<path:filename>')
@login_required
def inventory_photo(request_id, filename):
    booking = _get_request(request_id)
    if not any(photo.file_name == filename for photo in booking.photos):
        abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@booking_bp.route('/<request_id>/payment', methods=['GET', 'POST'])
@login_required
def payment(request_id):
    booking = _get_request(request_id)
    if request.method == 'POST':
        try:
            payment_record = initialize_payment(booking, current_user.email)
            if payment_record.authorization_url:
                queue_booking_conversion(booking, 'checkout_started')
                return redirect(payment_record.authorization_url)
        except ValueError as error:
            flash(str(error), 'error')
    return render_template('move_request/engine.html', stage='payment', stages=STAGES, booking=booking)


@booking_bp.route('/payment/callback')
def payment_callback():
    try:
        booking = verify_payment(request.args.get('session_id') or '')
    except ValueError as error:
        flash(str(error), 'error')
        return redirect(url_for('customer.bookings'))
    session['active_booking_request_id'] = booking.booking_request_id
    flash('Payment was confirmed and your move is now confirmed.', 'success')
    return redirect(url_for('customer.booking_detail', booking_id=booking.id))


@csrf.exempt
@booking_bp.route('/payment/webhook', methods=['POST'])
def stripe_payment_webhook():
    try:
        process_stripe_webhook(
            request.get_data(cache=False),
            request.headers.get('Stripe-Signature', ''),
        )
    except ValueError as error:
        return jsonify({'received': False, 'error': str(error)}), 400
    return jsonify({'received': True}), 200


@booking_bp.route('/payment/<int:booking_id>', methods=['GET', 'POST'])
@booking_bp.route('/<int:booking_id>/payment', methods=['GET', 'POST'])
@login_required
def booking_payment(booking_id):
    booking = Booking.query.filter_by(id=booking_id, customer_id=current_user.id).first_or_404()
    if not booking.booking_request_id:
        if request.method == 'POST':
            flash('Online payment is only available for requests with a verified quote. Contact operations for this historical booking.', 'error')
            return redirect(url_for('booking.booking_payment', booking_id=booking.id))
        return render_template('move_request/payment_history.html', booking=booking)
    return redirect(url_for('booking.payment', request_id=booking.booking_request_id))


@booking_api_bp.route('/api/bookings/<request_id>')
@login_required
def get_booking_request(request_id):
    booking = _get_request(request_id)
    return jsonify({
        'booking_request_id': booking.booking_request_id, 'state': booking.workflow_state,
        'quote_status': booking.quote_status, 'current_stage': _next_stage(booking),
        'pickup': {'address': booking.pickup_formatted_address or booking.pickup_address, 'latitude': booking.pickup_latitude, 'longitude': booking.pickup_longitude},
        'destination': {'address': booking.destination_formatted_address or booking.destination_address, 'latitude': booking.destination_latitude, 'longitude': booking.destination_longitude},
        'inventory': [{'name': item.display_name, 'quantity': item.quantity, 'size': item.size, 'fragile': item.fragile, 'large': item.large, 'special_handling': item.special_handling} for item in booking.inventory],
        'calculation': booking.calculation_data,
    })


@booking_api_bp.route('/api/bookings/<request_id>/ai/suggest', methods=['POST'])
@login_required
def api_suggest_booking_details(request_id):
    booking = _get_request(request_id)
    if booking.customer_id != current_user.id:
        abort(404)
    if booking.workflow_state not in {'LOCATION_COMPLETED', 'PROPERTY_COMPLETED'}:
        return jsonify({'success': False, 'error': 'AI suggestions are only available before inventory confirmation.'}), 409
    payload = request.get_json(silent=True) or {}
    if payload.get('external_processing_acknowledged') is not True:
        return jsonify({
            'success': False,
            'error': 'Acknowledge that the move description will be sent to Chigo’s configured AI provider before requesting suggestions.',
        }), 422
    try:
        suggestions = suggest_booking_details(current_user.id, payload.get('description', ''))
        return jsonify({'success': True, 'suggestions': suggestions})
    except (AIAssistanceUnavailable, ValueError) as error:
        return jsonify({'success': False, 'error': str(error)}), 503 if isinstance(error, AIAssistanceUnavailable) else 422


@booking_api_bp.route('/api/bookings/<request_id>/property', methods=['PUT'])
@login_required
def api_update_property(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    if booking.workflow_state not in {'LOCATION_COMPLETED', 'PROPERTY_COMPLETED'}:
        return jsonify({'success': False, 'error': 'Complete location before adding property details.'}), 409
    payload = request.get_json(silent=True) or {}
    requires_property_type = booking.primary_service in MOVE_PROPERTY_SERVICES
    property_type = payload.get('property_type')
    try:
        if requires_property_type:
            property_type = validate_property_type_for_service(booking.primary_service, property_type)
        values = {}
        if requires_property_type:
            values['property_type'] = property_type
            if booking.primary_service == 'residential':
                values.update({key: payload.get(key, 0) for key in ('bedrooms', 'bathrooms')})
                values['approximate_size_m2'] = payload.get('approximate_size_m2')
        answers = payload.get('service_answers') or {}
        if not isinstance(answers, dict):
            raise ValueError('service_answers must be an object.')
        service_answers = validate_question_answers(
            booking_questions_for(booking, 'property'), answers,
        )
        values['service_answers'] = service_answers
        if booking.primary_service == 'commercial':
            values['business_work_areas'] = service_answers['business_work_areas']
        for key in ('bedrooms', 'bathrooms'):
            if key in values:
                values[key] = int(values.get(key) or 0)
                if values[key] < 0:
                    raise ValueError('Property counts must be non-negative.')
        save_workflow_section(booking, 'property', values, current_user.id if current_user.is_authenticated else None)
        return jsonify({'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state})
    except (TypeError, ValueError) as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/inventory', methods=['POST', 'PUT'])
@login_required
def api_update_inventory(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    if booking.workflow_state not in {'PROPERTY_COMPLETED', 'INVENTORY_COMPLETED'}:
        return jsonify({'success': False, 'error': 'Complete property details before adding inventory.'}), 409
    payload = request.get_json(silent=True) or {}
    unsupported = set(payload) - {'items', 'inventory_answers', 'confirmed'}
    if unsupported:
        return jsonify({'success': False, 'error': f'Unknown inventory field: {", ".join(sorted(unsupported))}.'}), 422
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'success': False, 'error': 'Provide inventory as an array of items.'}), 422
    try:
        raw_answers = payload.get('inventory_answers') or {}
        if not isinstance(raw_answers, dict):
            raise ValueError('inventory_answers must be an object.')
        inventory_answers = validate_question_answers(
            booking_questions_for(booking, 'inventory'), raw_answers,
        )
        confirmed = payload.get('confirmed', False)
        if not isinstance(confirmed, bool):
            raise ValueError('confirmed must be a boolean.')
        if confirmed and booking.primary_service in {'residential', 'commercial'}:
            if inventory_answers.get('heavy_or_oversized') == 'yes' and not (booking.workflow_data or {}).get('heavy_items'):
                raise ValueError('Heavy or oversized item details must be saved before confirming inventory.')
        if confirmed and inventory_is_confirmed(booking):
            workflow_data = booking.workflow_data or {}
            previous_fingerprint = workflow_data.get('inventory_confirmation_fingerprint') or inventory_fingerprint(booking)
            if inventory_fingerprint(booking, items=items, answers=inventory_answers) == previous_fingerprint:
                return jsonify({
                    'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state,
                    'item_count': sum(item.quantity for item in booking.inventory),
                })
        replace_inventory(booking, items, current_user.id if current_user.is_authenticated else None)
        _store_inventory_answers(booking, inventory_answers, current_user.id if current_user.is_authenticated else None)
        if confirmed:
            confirm_inventory(booking, current_user.id)
        db.session.commit()
        return jsonify({'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state,
                        'item_count': sum(item.quantity for item in booking.inventory)})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/services', methods=['PUT'])
@login_required
def api_update_services(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    if (booking.workflow_state not in {'INVENTORY_COMPLETED', 'SERVICES_COMPLETED'}
            or (booking.primary_service in {'residential', 'commercial'}
                and not inventory_is_confirmed(booking))):
        return jsonify({'success': False, 'error': 'Add inventory before selecting services.'}), 409
    payload = request.get_json(silent=True) or {}
    unsupported = set(payload) - set(SERVICE_OPTIONS) - {'addon_answers'}
    if unsupported:
        return jsonify({'success': False, 'error': f'Unknown service field: {", ".join(sorted(unsupported))}.'}), 422
    for key, value in payload.items():
        if key in SERVICE_OPTIONS and value not in SERVICE_OPTIONS[key]:
            return jsonify({'success': False, 'error': f'Choose a valid option for {key.replace("_", " ")}.'}), 422
    addon_answers = payload.get('addon_answers') or {}
    if not isinstance(addon_answers, dict):
        return jsonify({'success': False, 'error': 'addon_answers must be an object.'}), 422
    try:
        services = {key: value for key, value in payload.items() if key in SERVICE_OPTIONS}
        validated_answers = _validated_addon_answers(
            services, addon_answers, primary_service=booking.primary_service,
        )
        save_workflow_section(booking, 'services', services, current_user.id if current_user.is_authenticated else None)
        workflow_data = dict(booking.workflow_data or {})
        previous_addon_answers = workflow_data.get('addon_answers', {})
        workflow_data['addon_answers'] = validated_answers
        active_addons = set(validated_answers)
        for addon in set(previous_addon_answers) - active_addons:
            record_event(booking, 'addon_answers.cleared', previous=previous_addon_answers[addon], new={},
                         user_id=current_user.id if current_user.is_authenticated else None)
        booking.workflow_data = workflow_data
        record_event(booking, 'addon_answers.updated', previous=previous_addon_answers,
                     new=validated_answers, user_id=current_user.id if current_user.is_authenticated else None)
        db.session.commit()
        return jsonify({'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state,
                        'recommendations': recommended_services(booking)})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/schedule', methods=['PUT'])
@login_required
def api_update_schedule(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    if booking.workflow_state not in {'SERVICES_COMPLETED', 'SCHEDULE_COMPLETED'}:
        return jsonify({'success': False, 'error': 'Select services before choosing a schedule.'}), 409
    payload = request.get_json(silent=True) or {}
    try:
        selected_date = date.fromisoformat(str(payload.get('date') or ''))
    except ValueError:
        return jsonify({'success': False, 'error': 'Provide a valid schedule date.'}), 422
    selected_time = payload.get('time')
    if selected_date < date.today() or selected_time not in {'Morning', 'Afternoon', 'Evening'}:
        return jsonify({'success': False, 'error': 'Choose a future date and valid preferred time.'}), 422
    try:
        values = {
            'date': selected_date.isoformat(), 'time': selected_time,
            'flexible_date': bool(payload.get('flexible_date')),
            'flexible_time': bool(payload.get('flexible_time')),
            'access_hours': str(payload.get('access_hours') or '')[:250],
        }
        save_workflow_section(booking, 'schedule', values, current_user.id if current_user.is_authenticated else None)
        return jsonify({'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/calculate', methods=['POST'])
@login_required
def api_calculate_quote(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    try:
        calculation = calculate_quote(booking, current_user.id if current_user.is_authenticated else None)
        queue_booking_conversion(booking, 'quote_generated')
        return jsonify({'success': True, 'booking_request_id': request_id, 'calculation': calculation,
                        'quote_lines': [{'code': line.code, 'label': line.label, 'amount': line.amount}
                                        for line in BookingQuoteLine.query.filter_by(booking_id=booking.id).all()]})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/quote/accept', methods=['POST'])
@login_required
def api_accept_quote(request_id):
    booking = _get_request(request_id)
    locked_response = _reject_locked_booking_api_edit(booking)
    if locked_response:
        return locked_response
    payload = request.get_json(silent=True) or {}
    try:
        accept_quote(
            booking, current_user.id if current_user.is_authenticated else None,
            policy_acknowledged=payload.get('policy_acknowledged') is True,
        )
        return jsonify({'success': True, 'booking_request_id': request_id, 'state': booking.workflow_state,
                        'quote_status': booking.quote_status})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/payment', methods=['POST'])
@login_required
def api_initialize_payment(request_id):
    booking = _get_request(request_id)
    # An accepted quote is intentionally immutable, but it must still be
    # possible to collect its payment.  The other locked states remain
    # blocked so this endpoint cannot reopen a paid/confirmed booking.
    locked_response = _reject_locked_booking_api_edit(booking, allow_accepted_quote=True)
    if locked_response:
        return locked_response
    try:
        payment_record = initialize_payment(booking, current_user.email)
        return jsonify({'success': True, 'reference': payment_record.payment_reference,
                        'authorization_url': payment_record.authorization_url,
                        'amount': payment_record.amount, 'currency': payment_record.currency})
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422


@booking_api_bp.route('/api/bookings/<request_id>/payment/status')
@login_required
def api_payment_status(request_id):
    booking = _get_request(request_id)
    return jsonify({'success': True, 'booking_request_id': request_id,
                    'booking_status': booking.status, 'payment_status': booking.payment_status,
                    'transactions': [{'reference': payment.payment_reference, 'status': payment.status,
                                      'amount': payment.amount, 'currency': payment.currency}
                                     for payment in booking.payments]})
