from __future__ import annotations

from datetime import date, datetime
from math import ceil
from secrets import token_hex

from flask import abort, current_app

from ..extensions import db
from ..models import (
    Booking, BookingAssignment, BookingEvent, BookingInventory, BookingItem, BookingPhoto,
    BookingQuoteLine, InventoryItem, Mover, ServicePricing, Truck, VehicleType,
)
from .image_upload_service import delete_image, save_image
from .inventory_catalogue import (
    ALL_CATALOGUE_ITEMS, BULK_ITEM_OPTIONS, CATEGORY_BY_ITEM,
    INVENTORY_CATALOG, PROPERTY_CATALOGUE,
)
from .location_service import route_distance, validate_coordinates
from .service_workflow import get_service_workflow, normalize_service_type, recommendations_for_booking, validate_service_bundle
from .vehicle_catalogue import ensure_active_vehicle_catalogue


STAGES = ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review')
STAGE_STATES = {
    'location': 'LOCATION_COMPLETED', 'property': 'PROPERTY_COMPLETED',
    'inventory': 'INVENTORY_COMPLETED', 'services': 'SERVICES_COMPLETED',
    'schedule': 'SCHEDULE_COMPLETED', 'quote': 'QUOTE_READY',
}
ITEM_ESTIMATES = {
    '1-seater sofa': (1.1, 35), '2-seater sofa': (1.8, 55), '3-seater sofa': (2.5, 85),
    'l-shaped sofa': (3.8, 125), 'recliner': (1.4, 45), 'armchair': (0.9, 28),
    'single bed': (1.3, 35), 'double bed': (1.8, 55), 'queen bed': (2.1, 65),
    'king bed': (2.5, 80), 'bunk bed': (2.4, 75), 'mattress': (0.8, 25),
    'bed frame': (1.0, 30), 'wardrobe': (1.7, 75), 'chest of drawers': (0.9, 40),
    'dressing table': (0.8, 35), 'refrigerator': (1.5, 80), 'fridge/freezer': (1.7, 90),
    'freezer': (1.2, 65), 'chest freezer': (1.5, 75), 'washing machine': (0.8, 70),
    'dryer': (0.8, 55), 'dishwasher': (0.7, 50), 'microwave': (0.2, 15),
    'oven': (0.7, 45), 'cooker': (0.7, 50), 'gas cooker': (0.7, 50),
    'television': (0.25, 18), 'tv': (0.25, 18), 'computer': (0.25, 15),
    'desktop computer': (0.35, 20), 'monitor': (0.2, 8), 'laptop': (0.08, 3),
    'printer': (0.25, 15), 'photocopier': (1.2, 90), 'generator': (0.9, 100),
    'inverter': (0.35, 30), 'safe': (0.5, 150), 'piano': (2.0, 250),
    'treadmill': (1.2, 110), 'exercise bike': (0.7, 35), 'bicycle': (0.6, 15),
    'motorcycle': (2.0, 180), 'office desk': (1.2, 45), 'executive desk': (2.0, 90),
    'office chair': (0.45, 12), 'conference table': (2.5, 100), 'server rack': (1.2, 120),
    'dining table': (1.4, 55), 'dining chair': (0.35, 8), 'center table': (0.7, 20),
    'bookshelf': (0.8, 30), 'display cabinet': (1.2, 55), 'cabinet': (0.8, 35),
    'box': (0.15, 12), 'storage boxes': (0.15, 12), 'suitcase': (0.15, 8),
}
SIZE_FACTORS = {'small': 0.6, 'medium': 1.0, 'large': 1.5, 'oversized': 2.2}


def record_event(booking: Booking, action: str, previous=None, new=None, user_id=None):
    db.session.add(BookingEvent(
        booking_id=booking.id, user_id=user_id, action=action,
        previous_value=previous, new_value=new,
    ))


def create_booking_request(customer_id=None, location=None, user_id=None, service_type='residential'):
    location = dict(location or {})
    required = ('pickup_address', 'pickup_city', 'pickup_state', 'destination_address', 'destination_city', 'destination_state')
    missing = [field for field in required if not str(location.get(field) or '').strip()]
    if missing:
        raise ValueError(f'Complete both addresses and their city and state before continuing: {", ".join(missing)}.')
    service_type = normalize_service_type(service_type)

    request_id = f"BR-{datetime.utcnow():%Y}-{token_hex(4).upper()}"
    location_fields = (
        'pickup_address', 'pickup_unit', 'pickup_area', 'pickup_city', 'pickup_state', 'pickup_country',
        'pickup_area_council', 'pickup_district', 'pickup_neighborhood', 'pickup_location_type', 'pickup_original_input', 'pickup_normalized_location',
        'pickup_local_place_id', 'pickup_location_id',
        'pickup_nearest_landmark', 'pickup_nearest_road', 'pickup_route_resolution',
        'pickup_formatted_address', 'pickup_latitude', 'pickup_longitude', 'pickup_place_id',
        'pickup_provider', 'pickup_provider_raw_id', 'pickup_notes', 'pickup_access',
        'pickup_inside_estate', 'pickup_narrow_road', 'pickup_parking_close', 'pickup_floor', 'pickup_stairs',
        'destination_address', 'destination_unit', 'destination_area', 'destination_city', 'destination_state', 'destination_country',
        'destination_area_council', 'destination_district', 'destination_neighborhood', 'destination_location_type', 'destination_original_input', 'destination_normalized_location',
        'destination_local_place_id', 'dropoff_location_id',
        'destination_nearest_landmark', 'destination_nearest_road', 'destination_route_resolution',
        'destination_formatted_address', 'destination_latitude', 'destination_longitude', 'destination_place_id',
        'destination_provider', 'destination_provider_raw_id', 'destination_notes', 'destination_access',
        'destination_inside_estate', 'destination_narrow_road', 'destination_parking_close', 'destination_floor', 'destination_stairs',
        'distance_km', 'route_duration_minutes', 'route_provider', 'distance_source', 'distance_precision',
        'route_status', 'route_calculated_at', 'service_area_status',
    )
    booking = Booking(
        booking_request_id=request_id, booking_number=request_id, customer_id=customer_id,
        status='Draft', primary_service=service_type, workflow_state='LOCATION_COMPLETED', quote_status='Draft',
        workflow_data={
            'service_intent': service_type,
            'property': {}, 'services': {}, 'schedule': {},
        }, calculation_data={},
        **{field: location[field] for field in location_fields if field in location},
    )
    db.session.add(booking)
    db.session.flush()
    record_event(booking, 'location.completed', new={
        'pickup': booking.pickup_formatted_address or booking.pickup_address,
        'destination': booking.destination_formatted_address or booking.destination_address,
    }, user_id=user_id)
    db.session.commit()
    return booking


def transition_stage(booking: Booking, stage: str, user_id=None):
    if stage not in STAGE_STATES:
        raise ValueError('Unknown booking stage.')
    index = STAGES.index(stage)
    if index and booking.workflow_state not in {STAGE_STATES[STAGES[index - 1]], STAGE_STATES[stage]}:
        raise ValueError(f'Complete the {STAGES[index - 1]} step before continuing.')
    previous = booking.workflow_state
    booking.workflow_state = STAGE_STATES[stage]
    record_event(booking, f'{stage}.completed', previous=previous, new=booking.workflow_state, user_id=user_id)


def invalidate_from(booking: Booking, stage: str, user_id=None):
    if stage not in STAGES[:5]:
        return
    index = STAGES.index(stage)
    previous = {'workflow_state': booking.workflow_state, 'quote_status': booking.quote_status}
    booking.workflow_state = 'LOCATION_COMPLETED' if index == 0 else STAGE_STATES[STAGES[index - 1]]
    booking.quote_status = 'Draft'
    booking.estimated_total = booking.deposit_amount = booking.balance_amount = 0
    booking.estimated_duration_minutes = None
    booking.schedule_feasible = None
    booking.estimated_volume_m3 = booking.estimated_weight_kg = 0
    booking.recommended_vehicle_type_id = None
    booking.calculation_data = {}
    booking.manually_overridden_total = None
    booking.price_override_reason = None
    booking.vehicle_override_reason = None
    BookingQuoteLine.query.filter_by(booking_id=booking.id).delete()
    record_event(booking, f'{stage}.changed_downstream_invalidated', previous=previous,
                 new={'workflow_state': booking.workflow_state, 'quote_status': booking.quote_status}, user_id=user_id)


def save_workflow_section(booking: Booking, section: str, values: dict, user_id=None):
    if section not in {'property', 'services', 'schedule'}:
        raise ValueError('Unknown booking section.')
    values = {key: value for key, value in values.items() if not (section == 'services' and key == 'additional_hours')}
    workflow_data = dict(booking.workflow_data or {})
    previous = workflow_data.get(section, {})
    workflow_data[section] = dict(values)
    booking.workflow_data = workflow_data
    if section == 'property':
        booking.property_type = values['property_type']
        booking.number_of_bedrooms = int(values.get('bedrooms') or 0)
        booking.number_of_bathrooms = int(values.get('bathrooms') or 0)
        booking.number_of_floors = int(values.get('floors') or 1)
    elif section == 'schedule':
        booking.move_date = date.fromisoformat(values['date'])
        booking.preferred_time = values['time']
        booking.flexible_date = bool(values.get('flexible_date'))
        booking.flexible_time = bool(values.get('flexible_time'))
    record_event(booking, f'{section}.updated', previous=previous, new=values, user_id=user_id)
    invalidate_from(booking, section, user_id)
    booking.workflow_state = STAGE_STATES[section]
    db.session.commit()


def _normalise_inventory_item(item: dict) -> dict:
    name = str(item.get('name') or '').strip()
    try:
        quantity = int(item.get('quantity', 1))
    except (TypeError, ValueError):
        raise ValueError('Inventory quantities must be whole numbers.') from None
    size = str(item.get('size') or 'medium').casefold()

    catalogue_item_id = item.get('inventory_item_id')
    if catalogue_item_id is not None:
        try:
            catalogue_item_id = int(catalogue_item_id)
        except (TypeError, ValueError):
            raise ValueError('Choose a valid catalogue item.') from None
    catalogue_item = InventoryItem.query.filter_by(id=catalogue_item_id).first() if catalogue_item_id else None
    if catalogue_item_id and catalogue_item is None:
        raise ValueError('The selected catalogue item is no longer available.')
    if catalogue_item and not catalogue_item.active:
        raise ValueError('The selected catalogue item is inactive.')
    if catalogue_item:
        name = catalogue_item.name

    if not name or quantity < 1 or quantity > 500:
        raise ValueError('Each inventory item needs a name and a quantity from 1 to 500.')
    if size not in SIZE_FACTORS:
        raise ValueError(f'Choose a valid size for {name}.')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    overrides = {}
    for field, limit in (('estimated_volume_m3', 100), ('estimated_weight_kg', 10000)):
        raw_value = item.get(field, details.get(field))
        if raw_value not in (None, ''):
            try:
                number = float(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f'Enter a valid {field.replace("_", " ")} for {name}.') from None
            if number <= 0 or number > limit:
                raise ValueError(f'{field.replace("_", " ").capitalize()} for {name} must be greater than zero and no more than {limit}.')
            overrides[field] = number
    safe_details = {key: value for key, value in details.items() if key in {
        'door_count', 'piano_type', 'foldable', 'approximate_size', 'floor', 'requires_disassembly'
    }}
    safe_details.update(overrides)
    category_key = str(item.get('category') or 'Other').casefold()
    if catalogue_item:
        name = catalogue_item.name
        item_category = catalogue_item.category
        custom_name = None
        estimated_volume = float(catalogue_item.estimated_volume_m3)
        estimated_weight = float(catalogue_item.estimated_weight_kg)
        details = dict(catalogue_item.dimensions_cm or {})
        details.update({
            'estimated_volume_m3': estimated_volume,
            'estimated_weight_kg': estimated_weight,
        })
        safe_details = {key: value for key, value in details.items() if key in {
            'door_count', 'piano_type', 'foldable', 'approximate_size', 'floor', 'requires_disassembly',
            'estimated_volume_m3', 'estimated_weight_kg',
        }}
        safe_details.update(overrides)
    else:
        item_category = str(item.get('category') or 'Other')[:60]
        custom_name = str(item.get('custom_name') or name).strip()
    name_key = name.casefold()
    explicit_fragile_terms = ('glass', 'ceramic', 'porcelain', 'fragile', 'vase', 'bowl', 'figurine')
    return {
        'name': name[:100], 'category': item_category, 'quantity': quantity,
        'size': size, 'notes': str(item.get('notes') or '')[:1000],
        'fragile': bool(item.get('fragile')) or (catalogue_item.fragile if catalogue_item else any(term in name_key for term in explicit_fragile_terms)),
        'large': bool(item.get('large')) or size == 'oversized',
        'special_handling': bool(item.get('special_handling')) or (catalogue_item.special_handling if catalogue_item else 'piano' in name_key or 'piano' in category_key),
        'details': safe_details, 'dimensions_cm': item.get('dimensions_cm') if isinstance(item.get('dimensions_cm'), dict) else None,
        'estimated_volume_m3': float(overrides.get('estimated_volume_m3') or (catalogue_item.estimated_volume_m3 if catalogue_item else 0)),
        'estimated_weight_kg': float(overrides.get('estimated_weight_kg') or (catalogue_item.estimated_weight_kg if catalogue_item else 0)),
        'inventory_item_id': catalogue_item_id, 'custom_name': custom_name,
    }


def replace_inventory(booking: Booking, inventory: list[dict], user_id=None):
    if not inventory:
        raise ValueError('Add at least one inventory item to continue.')
    if len(inventory) > 250:
        raise ValueError('A booking can contain no more than 250 inventory lines.')
    clean_items = [_normalise_inventory_item(item) for item in inventory]
    selected_keys = {}
    for item in clean_items:
        key = (item['inventory_item_id'], item['custom_name'] or item['name'])
        if key in selected_keys:
            selected_keys[key]['quantity'] += item['quantity']
            continue
        selected_keys[key] = item
    previous_count = BookingInventory.query.filter_by(booking_id=booking.id).count()
    old_selections = {
        (item.inventory_item_id, item.custom_name or item.display_name): item
        for item in booking.inventory
    }
    old_photos = {
        item.id: item.photos[0]
        for item in booking.inventory
        if item.photos
    }
    BookingInventory.query.filter_by(booking_id=booking.id).delete()
    BookingItem.query.filter_by(booking_id=booking.id).delete()
    selection_rows = []
    for item in selected_keys.values():
        item = dict(item)
        item.pop('name', None)
        selection = BookingInventory(booking_id=booking.id, **item)
        db.session.add(selection)
        snapshot = BookingItem(
            booking_id=booking.id, name=selection.display_name, category=selection.category,
            quantity=selection.quantity, notes=selection.notes, fragile=selection.fragile,
            large=selection.large, size=selection.size, special_handling=selection.special_handling,
            details=selection.details, dimensions_cm=selection.dimensions_cm,
            estimated_volume_m3=selection.estimated_volume_m3,
            estimated_weight_kg=selection.estimated_weight_kg,
        )
        db.session.add(snapshot)
        selection_rows.append((selection, snapshot))
    db.session.flush()
    for selection, snapshot in selection_rows:
        old_selection = old_selections.get((selection.inventory_item_id, selection.custom_name))
        old_photo = old_photos.get(old_selection.id) if old_selection else None
        if old_photo is not None:
            db.session.delete(old_photo)
            db.session.add(BookingPhoto(
                booking_id=booking.id, booking_item_id=snapshot.id, booking_inventory_id=selection.id,
                uploaded_by_id=old_photo.uploaded_by_id, file_name=old_photo.file_name,
                original_filename=old_photo.original_filename, mime_type=old_photo.mime_type,
                file_size=old_photo.file_size, category=old_photo.category,
                sort_order=old_photo.sort_order, is_primary=old_photo.is_primary,
                created_at=old_photo.created_at,
            ))
    invalidate_from(booking, 'inventory', user_id)
    record_event(booking, 'inventory.replaced', previous={'line_count': previous_count},
                 new={'line_count': len(selected_keys)}, user_id=user_id)
    booking.workflow_state = STAGE_STATES['inventory']
    db.session.commit()
    booking.items = list(BookingItem.query.filter_by(booking_id=booking.id).all())
    booking.inventory = list(BookingInventory.query.filter_by(booking_id=booking.id).all())


def add_inventory_selection(booking: Booking, item: dict, user_id=None):
    normalized = _normalise_inventory_item(item)
    replace_inventory(booking, [normalized], user_id)
    return BookingInventory.query.filter_by(
        booking_id=booking.id,
        inventory_item_id=normalized['inventory_item_id'],
        custom_name=normalized['custom_name'],
    ).first()


def update_inventory_selections(booking: Booking, changes: list[tuple[int, int, str]], user_id=None):
    if not changes:
        raise ValueError('Select at least one inventory item to update.')
    if len(changes) > 250:
        raise ValueError('A booking can contain no more than 250 inventory lines.')
    allowed_ids = {item.id for item in booking.inventory}
    invalid_ids = {selection_id for selection_id, _, _ in changes if selection_id not in allowed_ids}
    if invalid_ids:
        raise ValueError('One or more inventory selections no longer belong to this booking.')
    updated = {selection_id: (quantity, size) for selection_id, quantity, size in changes}
    selected_items = []
    for item in booking.inventory:
        quantity, size = updated.get(item.id, (item.quantity, item.size))
        if quantity < 1 or quantity > 500:
            raise ValueError('Inventory quantities must be from 1 to 500.')
        normalized_size = size.casefold()
        if normalized_size not in {'small', 'medium', 'large', 'oversized'}:
            raise ValueError('Choose a valid inventory size.')
        unit_volume = float(item.estimated_volume_m3)
        unit_weight = float(item.estimated_weight_kg)
        if item.inventory_item_id:
            catalogue_item = InventoryItem.query.filter_by(id=item.inventory_item_id).first()
            if catalogue_item:
                unit_volume = float(catalogue_item.estimated_volume_m3)
                unit_weight = float(catalogue_item.estimated_weight_kg)
        factor = SIZE_FACTORS.get(normalized_size, 1.0)
        selected_items.append({
            'inventory_item_id': item.inventory_item_id,
            'custom_name': item.custom_name,
            'name': item.display_name,
            'quantity': quantity,
            'category': item.category,
            'size': normalized_size,
            'notes': item.notes,
            'fragile': item.fragile,
            'large': item.large,
            'special_handling': item.special_handling,
            'details': item.details,
            'estimated_volume_m3': round(unit_volume * factor, 3),
            'estimated_weight_kg': round(unit_weight * factor, 1),
        })
    replace_inventory(booking, selected_items, user_id)
    return BookingInventory.query.filter_by(booking_id=booking.id).all()


def update_inventory_selection(booking: Booking, selection_id: int, quantity: int, size: str = None, user_id=None):
    return update_inventory_selections(booking, [(selection_id, quantity, size or '')], user_id)


def remove_inventory_selection(booking: Booking, selection_id: int, user_id=None):
    selection = BookingInventory.query.filter_by(id=selection_id, booking_id=booking.id).first_or_404()
    db.session.delete(selection)
    replace_inventory(booking, [
        {'inventory_item_id': item.inventory_item_id, 'custom_name': item.custom_name,
         'name': item.display_name, 'quantity': item.quantity, 'category': item.category,
         'size': item.size, 'notes': item.notes, 'fragile': item.fragile,
         'large': item.large, 'special_handling': item.special_handling,
         'details': item.details, 'estimated_volume_m3': item.estimated_volume_m3,
         'estimated_weight_kg': item.estimated_weight_kg}
        for item in booking.inventory if item.id != selection_id
    ], user_id)
    return selection


def save_inventory_photo(booking: Booking, selection_id: int, file_storage, user_id=None) -> BookingPhoto:
    selection = BookingInventory.query.filter_by(id=selection_id, booking_id=booking.id).first_or_404()
    snapshot = BookingItem.query.filter_by(booking_id=booking.id, name=selection.display_name).order_by(BookingItem.id.desc()).first()
    if snapshot is None:
        raise ValueError('The selected inventory item is no longer available for photo upload.')
    saved = save_image(file_storage, f'inventory/{booking.id}', current_app.config['MAX_IMAGE_SIZE'])
    photo = BookingPhoto(
        booking_id=booking.id, booking_item_id=snapshot.id, booking_inventory_id=selection.id,
        uploaded_by_id=user_id, file_name=saved['file_url'], original_filename=saved['original_filename'],
        mime_type=saved['mime_type'], file_size=saved['file_size'], category='inventory',
        sort_order=BookingPhoto.query.filter_by(booking_inventory_id=selection.id).count(), is_primary=False,
    )
    db.session.add(photo)
    db.session.commit()
    return photo


def delete_inventory_photo(booking: Booking, photo_id: int, user_id=None):
    photo = BookingPhoto.query.filter_by(id=photo_id, booking_id=booking.id).first_or_404()
    if photo.uploaded_by_id not in {None, user_id}:
        abort(403)
    delete_image(photo.file_name)
    db.session.delete(photo)
    db.session.commit()
    return photo


def catalogue_items_for_service(service_type: str, query: str = '', property_type: str | None = None,
                                unit_type: str | None = None):
    service_type = normalize_service_type(service_type)
    ensure_inventory_catalogue()
    active = InventoryItem.query.filter_by(active=True).order_by(InventoryItem.sort_order, InventoryItem.name).all()
    allowed_names = None
    if property_type in PROPERTY_CATALOGUE:
        selected_groups = [PROPERTY_CATALOGUE[property_type]]
        if property_type == 'Estate' and unit_type in {'Apartment', 'House', 'Duplex'}:
            selected_groups.append(PROPERTY_CATALOGUE[unit_type])
        allowed_names = {
            name for groups in selected_groups for names in groups.values() for name in names
        }
    elif service_type == 'residential':
        allowed_names = {
            name for property_name in ('Apartment', 'House', 'Duplex')
            for names in PROPERTY_CATALOGUE[property_name].values() for name in names
        }
    elif service_type == 'commercial':
        allowed_names = {
            name for property_name in ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site')
            for names in PROPERTY_CATALOGUE[property_name].values() for name in names
        }
    if allowed_names is not None:
        active = [item for item in active if item.name in allowed_names]
    if service_type == 'commercial':
        preferred = {'office', 'electronics'}
    elif service_type == 'packing':
        preferred = {'living room', 'bedroom', 'kitchen', 'office', 'fragile / valuable', 'miscellaneous'}
    elif service_type == 'storage':
        preferred = {'living room', 'bedroom', 'kitchen', 'office', 'garage / storage', 'miscellaneous'}
    elif service_type == 'logistics':
        preferred = {'office', 'electronics', 'garage / storage', 'fragile / valuable', 'miscellaneous'}
    else:
        preferred = set()
    def score(item):
        return (0 if item.category.casefold() in preferred else 1, item.sort_order, item.name.casefold())
    items = sorted(active, key=score)
    if query:
        needle = query.casefold().strip()
        items = [item for item in items if needle in item.name.casefold() or needle in item.category.casefold() or needle in (item.aliases or '').casefold()]
    grouped = {}
    if property_type in PROPERTY_CATALOGUE:
        item_by_name = {item.name: item for item in items}
        selected_groups = [PROPERTY_CATALOGUE[property_type]]
        if property_type == 'Estate' and unit_type in {'Apartment', 'House', 'Duplex'}:
            selected_groups.append(PROPERTY_CATALOGUE[unit_type])
        for property_groups in selected_groups:
            for category, names in property_groups.items():
                for name in names:
                    if name in item_by_name:
                        grouped.setdefault(category, []).append(item_by_name[name])
    else:
        for item in items:
            grouped.setdefault(item.category, []).append(item)
    return [{'category': category, 'catalog_items': grouped[category]} for category in sorted(grouped)]


def ensure_inventory_catalogue():
    existing_items = {item.name: item for item in InventoryItem.query.all()}
    category_order = {category: index for index, category in enumerate(INVENTORY_CATALOG)}
    for sort_order, name in enumerate(ALL_CATALOGUE_ITEMS):
        category = CATEGORY_BY_ITEM[name]
        item = existing_items.get(name)
        volume, weight = estimate_item(name)
        fragile = category == 'Fragile / Valuable' or any(
            term in name.casefold() for term in ('glass', 'mirror', 'artwork', 'painting', 'chandelier', 'sculpture', 'fragile')
        )
        special_handling = category in {'Heavy Items', 'Special Handling'} or any(
            term in name.casefold() for term in ('piano', 'safe', 'server rack', 'generator', 'machinery', 'forklift', 'motorcycle')
        )
        if item is None:
            item = InventoryItem(name=name)
            db.session.add(item)
            existing_items[name] = item
        values = {
            'category': category,
            'description': f'{name} for {category.casefold()} moving inventory.',
            'estimated_volume_m3': round(volume, 3),
            'estimated_weight_kg': round(weight, 1),
            'aliases': name.casefold(),
            'fragile': fragile,
            'special_handling': special_handling,
            'vehicle_compatibility': 'Heavy vehicle' if special_handling or volume >= 2 else 'All vehicles',
            'sort_order': category_order.get(category, 0) * 1000 + sort_order,
        }
        for field, value in values.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
    db.session.commit()


def estimate_item(name: str, size='medium'):
    normalized = str(name or '').casefold().strip()
    factor = SIZE_FACTORS.get(str(size or 'medium').casefold(), 1.0)
    estimate = ITEM_ESTIMATES.get(normalized)
    if estimate:
        return estimate[0] * factor, estimate[1] * factor
    if any(word in normalized for word in ('sofa', 'couch', 'wardrobe', 'cabinet', 'table', 'desk', 'bed', 'machine', 'fridge', 'freezer', 'generator', 'treadmill', 'motorcycle', 'workbench')):
        volume, weight = 1.2, 55
    elif any(word in normalized for word in ('chair', 'fan', 'mirror', 'monitor', 'printer', 'speaker', 'lamp', 'basket', 'stool')):
        volume, weight = 0.4, 15
    elif any(word in normalized for word in ('box', 'bag', 'book', 'clothing', 'document', 'utensil', 'plate', 'cup')):
        volume, weight = 0.2, 12
    else:
        volume, weight = 0.65, 28
    return volume * factor, weight * factor


def inventory_requirements(booking: Booking):
    volume = weight = 0.0
    total_items = fragile_items = special_items = large_items = 0
    items = list(booking.inventory) if booking.inventory else booking.items
    for item in items:
        factor = SIZE_FACTORS.get(item.size or 'medium', 1.0)
        dimensions = item.dimensions_cm or {}
        details = item.details or {}
        if all(dimensions.get(key) for key in ('length', 'width', 'height')):
            unit_volume = float(dimensions['length']) * float(dimensions['width']) * float(dimensions['height']) / 1_000_000
            unit_weight = float(details.get('estimated_weight_kg') or item.estimated_weight_kg or unit_volume * 25)
        else:
            unit_volume, unit_weight = estimate_item(item.display_name if hasattr(item, 'display_name') else item.name, item.size)
        if details.get('estimated_volume_m3'):
            unit_volume = float(details['estimated_volume_m3'])
        if details.get('estimated_weight_kg'):
            unit_weight = float(details['estimated_weight_kg'])
        if hasattr(item, 'estimated_volume_m3'):
            item.estimated_volume_m3 = round(unit_volume, 3)
            item.estimated_weight_kg = round(unit_weight, 1)
        volume += unit_volume * item.quantity
        weight += unit_weight * item.quantity
        total_items += item.quantity
        fragile_items += item.quantity if item.fragile else 0
        special_items += item.quantity if item.special_handling else 0
        large_items += item.quantity if item.large else 0
    pricing = {row.key: float(row.value) for row in ServicePricing.query.all()}
    volume *= pricing.get('volume_safety_factor', 1.15)
    booking.estimated_volume_m3 = round(volume, 2)
    booking.estimated_weight_kg = round(weight, 1)
    db.session.flush()
    return {
        'item_count': total_items, 'volume_m3': booking.estimated_volume_m3,
        'weight_kg': booking.estimated_weight_kg, 'fragile_items': fragile_items,
        'special_items': special_items, 'large_items': large_items,
        'oversized_items': sum(item.quantity for item in booking.items if item.size == 'oversized'),
        'loading_minutes': ceil(volume / 5 * 60), 'unloading_minutes': ceil(volume / 6 * 60),
    }


def vehicle_options(booking: Booking):
    ensure_active_vehicle_catalogue()
    vehicles = VehicleType.query.filter_by(is_active=True).order_by(VehicleType.capacity_m3, VehicleType.sort_order).all()
    requirements = inventory_requirements(booking) if booking.inventory or booking.items else {'volume_m3': 0, 'weight_kg': 0}
    def count_for(candidate):
        return max(1, ceil(requirements['volume_m3'] / candidate.capacity_m3),
                   ceil(requirements['weight_kg'] / candidate.max_payload_kg))
    def fits(candidate):
        return requirements['volume_m3'] <= candidate.capacity_m3 and requirements['weight_kg'] <= candidate.max_payload_kg
    safe_vehicles = [vehicle for vehicle in vehicles if fits(vehicle)]
    recommendation = min(safe_vehicles or vehicles, key=lambda candidate: (
        count_for(candidate), count_for(candidate) * max(float(candidate.minimum_price or 0), float(candidate.base_price or 0)),
        candidate.sort_order or 0,
    )) if vehicles and (booking.inventory or booking.items) else None
    selected_id = ((booking.workflow_data or {}).get('vehicle') or {}).get('selected_type_id')
    return [{
        'id': vehicle.id, 'name': vehicle.name, 'description': vehicle.description,
        'image': vehicle.image or (
            'images/demo/pickup-demo.svg' if (vehicle.body_type or '').casefold() == 'pickup'
            else 'images/demo/cargo-van-demo.svg' if 'van' in (vehicle.body_type or '').casefold()
            else 'images/demo/truck-demo.svg'
        ),
        'best_for': vehicle.best_for, 'capacity_m3': vehicle.capacity_m3,
        'max_payload_kg': vehicle.max_payload_kg, 'quantity': count_for(vehicle),
        'unit_price': max(float(vehicle.minimum_price or 0), float(vehicle.base_price or 0)),
        'sort_order': vehicle.sort_order or 0, 'fits': fits(vehicle),
        'capacity_exceeded': not fits(vehicle),
        'recommended': recommendation is not None and vehicle.id == recommendation.id,
        'selected': vehicle.id == selected_id if selected_id else recommendation is not None and vehicle.id == recommendation.id,
    } for vehicle in vehicles]


def estimate_workforce_duration(booking: Booking, requirements: dict, vehicle_count: int):
    crew = max(2, ceil(requirements['volume_m3'] / 8), ceil(requirements['weight_kg'] / 1800), ceil(requirements['fragile_items'] / 8))
    services = (booking.workflow_data or {}).get('services', {})
    floors = sum(1 for floor in (booking.pickup_floor, booking.destination_floor) if floor not in {None, 'Ground'})
    access_count = sum(access in {'Moderate', 'Difficult'} for access in (booking.pickup_access, booking.destination_access))
    stair_factor = floors * (0.2 if any((booking.pickup_stairs, booking.destination_stairs)) else 0.08)
    loading_minutes = max(30, ceil(requirements['volume_m3'] * 12 / crew + requirements['large_items'] * 4 + requirements['special_items'] * 8))
    unloading_minutes = max(25, ceil(requirements['volume_m3'] * 10 / crew + requirements['large_items'] * 3 + requirements['special_items'] * 8))
    handling_minutes = ceil((loading_minutes + unloading_minutes) * stair_factor) + access_count * 15
    travel_minutes = float(booking.route_duration_minutes or (float(booking.distance_km or 0) / 40 * 60))
    vehicle_minutes = travel_minutes * max(1, ceil(vehicle_count / 2))
    service_minutes = {'partial': 30, 'full': max(45, requirements['item_count'] * 4)}.get(services.get('packing'), 0)
    service_minutes += 35 if services.get('unpacking') == 'yes' else 0
    service_minutes += 30 if services.get('disassembly') == 'yes' else 0
    service_minutes += 30 if services.get('assembly') == 'yes' else 0
    service_minutes += {'move-out': 90, 'move-in': 90, 'both': 150}.get(services.get('cleaning'), 0)
    return {
        'movers': crew, 'loading_minutes': loading_minutes, 'unloading_minutes': unloading_minutes,
        'handling_minutes': handling_minutes, 'travel_minutes': ceil(vehicle_minutes),
        'service_minutes': service_minutes,
        'duration_minutes': ceil(loading_minutes + unloading_minutes + handling_minutes + vehicle_minutes + service_minutes),
    }


def preview_move_duration(booking: Booking):
    requirements = inventory_requirements(booking)
    selected = next((option for option in vehicle_options(booking) if option['selected']), None)
    return estimate_workforce_duration(booking, requirements, selected['quantity'] if selected else 1)


def save_vehicle_selection(booking: Booking, vehicle_type_id, user_id=None):
    try:
        vehicle_id = int(vehicle_type_id)
    except (TypeError, ValueError):
        raise ValueError('Choose a valid vehicle option.') from None
    vehicle = VehicleType.query.filter_by(id=vehicle_id, is_active=True).first()
    if vehicle is None:
        raise ValueError('That vehicle option is no longer available.')
    requirements = inventory_requirements(booking) if booking.inventory or booking.items else {'volume_m3': 0, 'weight_kg': 0}
    vehicle_count = max(
        1,
        ceil(requirements['volume_m3'] / vehicle.capacity_m3),
        ceil(requirements['weight_kg'] / vehicle.max_payload_kg),
    )
    if (vehicle_count * vehicle.capacity_m3 < requirements['volume_m3']
            or vehicle_count * vehicle.max_payload_kg < requirements['weight_kg']):
        raise ValueError(f'{vehicle.name} cannot safely carry this inventory. Operations must review the request.')
    workflow_data = dict(booking.workflow_data or {})
    previous = workflow_data.get('vehicle', {})
    workflow_data['vehicle'] = {'selected_type_id': vehicle.id}
    booking.workflow_data = workflow_data
    invalidate_from(booking, 'inventory', user_id)
    booking.workflow_state = STAGE_STATES['inventory']
    record_event(booking, 'vehicle.selected', previous=previous, new=workflow_data['vehicle'], user_id=user_id)
    db.session.commit()


def recommended_services(booking: Booking):
    recommendations = []
    items = list(booking.inventory) if booking.inventory else booking.items
    fragile_count = sum(item.quantity for item in items if item.fragile)
    if fragile_count:
        recommendations.append({'key': 'packing', 'value': 'partial', 'priority': 'recommended', 'label': 'Fragile-item packing', 'reason': f'You have {fragile_count} fragile item(s) that benefit from protective packing.'})
    wardrobes = sum(item.quantity for item in items if 'wardrobe' in item.display_name.casefold())
    if wardrobes:
        recommendations.append({'key': 'disassembly', 'value': 'yes', 'priority': 'recommended', 'label': 'Furniture disassembly', 'reason': f'{wardrobes} wardrobe(s) may need disassembly for safe handling.'})
    if any(item.special_handling for item in items):
        recommendations.append({'key': 'special_handling', 'value': 'required', 'priority': 'required', 'label': 'Special handling', 'reason': 'Your inventory includes items that need specialist handling.'})
    if any(getattr(booking, f'{place}_stairs', False) for place in ('pickup', 'destination')):
        recommendations.append({'key': 'stair_handling', 'value': 'required', 'priority': 'required', 'label': 'Stair handling', 'reason': 'Your location details indicate stairs at one or both addresses.'})
    requirements = inventory_requirements(booking)
    if requirements['item_count'] >= 20 or requirements['volume_m3'] >= 20:
        recommendations.append({'key': 'packing', 'value': 'full', 'priority': 'recommended', 'label': 'Professional packing', 'reason': f'Your inventory contains {requirements["item_count"]} items and is large enough to benefit from a full packing plan.'})
    if requirements['large_items'] >= 3:
        recommendations.append({'key': 'special_handling', 'value': 'required', 'priority': 'recommended', 'label': 'Large-item handling', 'reason': f'You selected {requirements["large_items"]} large items that may need additional crew and handling time.'})
    if float(booking.distance_km or 0) >= 100:
        recommendations.append({'key': 'protective_wrapping', 'value': 'recommended', 'priority': 'recommended', 'label': 'Protective wrapping', 'reason': 'Additional protection is recommended for longer-distance transport.'})
    return recommendations


def suggested_inventory(booking: Booking):
    property_data = (booking.workflow_data or {}).get('property', {})
    property_type = str(property_data.get('property_type') or '').casefold()
    if property_type in {'office', 'shop', 'warehouse', 'construction site'}:
        stations = max(1, int(property_data.get('workstations') or 1))
        suggestions = [
            ('Office desk', 'Office', stations), ('Office chair', 'Office', stations),
            ('Cabinet', 'Office', 1), ('Computer monitor', 'Electronics', stations),
            ('Box', 'Boxes', max(10, stations * 2)),
        ]
    else:
        bedrooms = max(1, int(property_data.get('bedrooms') or 1))
        suggestions = [
            ('Bed', 'Bedroom', bedrooms), ('Mattress', 'Bedroom', bedrooms),
            ('Wardrobe', 'Bedroom', bedrooms), ('Sofa', 'Furniture', 1),
            ('Dining table', 'Furniture', 1), ('Dining chair', 'Furniture', 4),
            ('Refrigerator', 'Appliances', 1), ('Washing machine', 'Appliances', 1),
            ('Microwave', 'Kitchen', 1), ('Box', 'Boxes', max(10, bedrooms * 10)),
        ]
    return suggestions


def check_availability(booking: Booking, mover_count: int, vehicle_type: VehicleType, vehicle_count=1):
    if not booking.move_date:
        raise ValueError('Choose a move date before checking availability.')
    day_assignments = BookingAssignment.query.join(Booking).filter(Booking.move_date == booking.move_date).all()
    busy_truck_ids = {assignment.truck_id for assignment in day_assignments if assignment.truck_id}

    busy_mover_names = {
        name for assignment in day_assignments
        for name in (assignment.mover_1, assignment.mover_2, assignment.mover_3, assignment.mover_4)
        if name
    }
    available_trucks = [truck for truck in Truck.query.filter_by(status='Available').all()
                        if truck.id not in busy_truck_ids and truck.vehicle_type.casefold() == vehicle_type.name.casefold()]
    available_movers = [mover for mover in Mover.query.filter_by(status='Available').all()
                        if mover.name not in busy_mover_names]
    return {
        'available': len(available_trucks) >= vehicle_count and len(available_movers) >= mover_count,
        'required': {'vehicles': vehicle_count, 'movers': mover_count},
        'available_resources': {'vehicles': len(available_trucks), 'movers': len(available_movers)},
        'date': booking.move_date.isoformat(),
        'reason': None if len(available_trucks) >= vehicle_count and len(available_movers) >= mover_count else 'Available operations resources do not meet this move requirement.',
    }


def calculate_quote(booking: Booking, user_id=None):
    if booking.workflow_state not in {'SCHEDULE_COMPLETED', 'QUOTE_READY'}:
        raise ValueError('Complete location, property, inventory, services, and schedule before requesting a quote.')
    if booking.distance_km is None or float(booking.distance_km) <= 0:
        pickup = validate_coordinates(booking.pickup_latitude, booking.pickup_longitude)
        destination = validate_coordinates(booking.destination_latitude, booking.destination_longitude)
        route = route_distance(*pickup, *destination) if pickup and destination else None
        if route:
            booking.distance_km = route['distance_km']
            booking.route_duration_minutes = route['duration_minutes']
            booking.route_provider = route['provider']
            booking.distance_source = route.get('distance_source')
            booking.distance_precision = route.get('distance_precision', 'fares')
            booking.route_status = 'CALCULATED'
            booking.route_calculated_at = datetime.utcnow()
        else:
            booking.route_status = 'PENDING'
            raise ValueError('Road distance is temporarily unavailable. Your move details are saved; retry the quote shortly or contact operations.')
    if not booking.items:
        raise ValueError('Add inventory before calculating the quote.')

    pricing = {row.key: float(row.value) for row in ServicePricing.query.all()}
    required_rates = ('base_moving_fee', 'price_per_km', 'base_mover_hourly_fee', 'floor_surcharge', 'access_difficulty_surcharge', 'outside_service_area_surcharge', 'tax_rate', 'deposit_percentage')
    missing_rates = [key for key in required_rates if key not in pricing]
    if missing_rates:
        raise ValueError(f'Pricing configuration is incomplete: {", ".join(missing_rates)}.')

    requirements = inventory_requirements(booking)
    ensure_active_vehicle_catalogue()
    vehicles = VehicleType.query.filter_by(is_active=True).order_by(VehicleType.capacity_m3, VehicleType.sort_order).all()
    if not vehicles:
        raise ValueError('No active vehicle type can safely carry this inventory. Operations must review the request.')
    def vehicle_count_for(candidate):
        return max(1, ceil(requirements['volume_m3'] / candidate.capacity_m3),
                   ceil(requirements['weight_kg'] / candidate.max_payload_kg))

    recommended_vehicle = min(
        vehicles,
        key=lambda candidate: (
            vehicle_count_for(candidate),
            vehicle_count_for(candidate) * max(float(candidate.minimum_price or 0), float(candidate.base_price or 0)),
            candidate.sort_order or 0,
        ),
    )
    vehicle_state = (booking.workflow_data or {}).get('vehicle', {})
    selected_id = vehicle_state.get('selected_type_id')
    vehicle = next((candidate for candidate in vehicles if candidate.id == selected_id), recommended_vehicle)
    vehicle_count = vehicle_count_for(vehicle)

    duration = estimate_workforce_duration(booking, requirements, vehicle_count)
    crew = duration['movers']
    services = (booking.workflow_data or {}).get('services', {})
    floors = sum(1 for floor in (booking.pickup_floor, booking.destination_floor) if floor not in {None, 'Ground'})
    access_count = sum(access in {'Moderate', 'Difficult'} for access in (booking.pickup_access, booking.destination_access))
    duration_minutes = duration['duration_minutes']

    lines = []
    def add_line(code, label, quantity, unit_amount):
        amount = round(float(quantity) * float(unit_amount), 2)
        if amount:
            lines.append({'code': code, 'label': label, 'quantity': float(quantity), 'unit_amount': float(unit_amount), 'amount': amount})

    add_line('base_moving', 'Base moving service', 1, pricing['base_moving_fee'])
    vehicle_rate = max(float(vehicle.minimum_price or 0), float(vehicle.base_price or 0))
    add_line('vehicle', f'{vehicle_count} × {vehicle.name}' if vehicle_count > 1 else vehicle.name, vehicle_count, vehicle_rate)
    add_line('travel', 'Travel distance', booking.distance_km, pricing['price_per_km'])
    add_line('labor', f'{crew} movers · {duration_minutes / 60:.1f} hours', crew * duration_minutes / 60, pricing['base_mover_hourly_fee'])
    add_line('floor_access', 'Floor access', floors, pricing['floor_surcharge'])
    add_line('access', 'Difficult access', access_count, pricing['access_difficulty_surcharge'])
    if booking.service_area_status == 'OUTSIDE_SERVICE_AREA':
        add_line('service_area', 'Outside service area', 1, pricing['outside_service_area_surcharge'])
    property_data = (booking.workflow_data or {}).get('property', {})
    property_size = float(property_data.get('approximate_size_m2') or 0)
    if property_data.get('property_type') in {'House', 'Duplex', 'Office', 'Warehouse', 'Construction Site'} or property_size > 150:
        add_line('property_surcharge', 'Property size and handling', 1, pricing.get('property_surcharge', 0))
    add_line('special_handling', 'Special handling', requirements['special_items'], pricing.get('special_handling', 0))

    configured_services = {
        ('packing', 'partial'): ('packing_partial', 1),
        ('packing', 'full'): ('packing_full', 1),
        ('unpacking', 'yes'): ('unpacking', 1),
        ('storage', '1 week'): ('storage_week', 1),
        ('storage', '1 month'): ('storage_week', 4),
        ('storage', '3 months'): ('storage_week', 12),
        ('assembly', 'yes'): ('service_assembly_yes', 1),
        ('disassembly', 'yes'): ('service_disassembly_yes', 1),
        ('cleaning', 'move-out'): ('cleaning_move_out', 1),
        ('cleaning', 'move-in'): ('cleaning_move_in', 1),
        ('cleaning', 'both'): ('cleaning_both', 1),
    }
    for service_name, service_value in services.items():
        configured = configured_services.get((service_name, str(service_value).casefold()))
        if configured is None:
            continue
        key, quantity = configured
        if key not in pricing:
            raise ValueError(f'Pricing is not configured for selected service: {service_name}.')
        add_line(key, f'{service_name.replace("_", " ").title()} · {service_value}', quantity, pricing[key])

    if booking.move_date.weekday() >= 5:
        if 'weekend_surcharge' not in pricing:
            raise ValueError('Weekend pricing is not configured.')
        add_line('weekend', 'Weekend service', 1, pricing['weekend_surcharge'])

    subtotal = round(sum(line['amount'] for line in lines), 2)
    tax = round(subtotal * pricing['tax_rate'] / 100, 2)
    add_line('tax', 'Tax', 1, tax)
    total = round(subtotal + tax, 2)
    if booking.manually_overridden_total is not None:
        override_delta = round(float(booking.manually_overridden_total) - total, 2)
        add_line('manual_adjustment', 'Operations price adjustment', 1, override_delta)
        total = round(float(booking.manually_overridden_total), 2)
    deposit = round(total * pricing['deposit_percentage'] / 100, 2)
    availability = check_availability(booking, crew, vehicle, vehicle_count)
    slot_capacity = {'Morning': 8 * 60, 'Afternoon': 6 * 60, 'Evening': 5 * 60}.get(booking.preferred_time, 8 * 60)
    if duration_minutes > slot_capacity:
        availability['available'] = False
        availability['reason'] = f'Estimated service duration is {duration_minutes // 60}h {duration_minutes % 60}m, longer than the selected time window.'
    previous = {'total': booking.estimated_total, 'vehicle': booking.recommended_vehicle_type.name if booking.recommended_vehicle_type else None}
    BookingQuoteLine.query.filter_by(booking_id=booking.id).delete()
    for line in lines:
        db.session.add(BookingQuoteLine(booking_id=booking.id, **line))
    booking.recommended_vehicle_type_id = vehicle.id
    booking.estimated_volume_m3 = requirements['volume_m3']
    booking.estimated_weight_kg = requirements['weight_kg']
    booking.estimated_duration_minutes = duration_minutes
    booking.schedule_feasible = availability['available']
    booking.estimated_total = total
    booking.deposit_amount = deposit
    booking.balance_amount = round(total - deposit, 2)
    booking.quote_status = 'Ready'
    booking.workflow_state = 'QUOTE_READY'
    booking.calculation_data = {
        'inventory': requirements, 'vehicle': {
            'id': vehicle.id, 'name': vehicle.name, 'quantity': vehicle_count,
            'recommended_id': recommended_vehicle.id, 'recommended_name': recommended_vehicle.name,
            'volume_capacity_m3': vehicle.capacity_m3, 'weight_capacity_kg': vehicle.max_payload_kg,
            'selected_is_recommended': vehicle.id == recommended_vehicle.id,
        },
        'workforce': {'movers': crew}, 'duration_minutes': duration_minutes,
        'route': {'distance_km': booking.distance_km}, 'availability': availability,
        'subtotal': subtotal, 'tax': tax, 'total': total,
        'deposit': deposit, 'balance': booking.balance_amount,
        'recommendations': recommended_services(booking),
    }
    record_event(booking, 'quote.calculated', previous=previous,
                 new={'total': total, 'vehicle': vehicle.name, 'lines': len(lines)}, user_id=user_id)
    db.session.commit()
    return booking.calculation_data


def accept_quote(booking: Booking, user_id):
    if booking.quote_status != 'Ready' or booking.workflow_state != 'QUOTE_READY':
        raise ValueError('Only a ready quote can be accepted.')
    previous = booking.quote_status
    booking.quote_status = 'Accepted'
    booking.workflow_state = 'QUOTE_ACCEPTED'
    record_event(booking, 'quote.accepted', previous=previous, new=booking.quote_status, user_id=user_id)
    db.session.commit()


def apply_price_override(booking: Booking, amount: float, reason: str, user_id):
    if not reason.strip() or amount < 0:
        raise ValueError('Provide a non-negative override amount and a reason.')
    previous = {'amount': booking.manually_overridden_total or booking.estimated_total}
    booking.manually_overridden_total = round(float(amount), 2)
    booking.price_override_reason = reason.strip()[:1000]
    booking.overridden_by = user_id
    booking.overridden_at = datetime.utcnow()
    pricing = {row.key: float(row.value) for row in ServicePricing.query.all()}
    deposit_percentage = pricing.get('deposit_percentage')
    if deposit_percentage is not None:
        booking.deposit_amount = round(float(amount) * deposit_percentage / 100, 2)
        booking.balance_amount = round(float(amount) - booking.deposit_amount, 2)
    BookingQuoteLine.query.filter_by(booking_id=booking.id, code='manual_adjustment').delete()
    delta = round(float(amount) - float(booking.estimated_total or 0), 2)
    if delta:
        db.session.add(BookingQuoteLine(
            booking_id=booking.id, code='manual_adjustment', label='Operations price adjustment',
            quantity=1, unit_amount=delta, amount=delta, source='manual',
        ))
    record_event(booking, 'quote.price_overridden', previous=previous,
                 new={'amount': booking.manually_overridden_total, 'reason': booking.price_override_reason}, user_id=user_id)
    db.session.commit()