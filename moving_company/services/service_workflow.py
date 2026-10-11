from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVICE_TYPES = frozenset({'residential', 'commercial', 'packing', 'storage', 'logistics'})
PROPERTY_TYPES_BY_SERVICE = {
    'residential': ('Apartment', 'House', 'Duplex'),
    'commercial': ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
    'packing': ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate'),
    'storage': ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate'),
    'logistics': ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
}

SERVICE_WORKFLOWS = {
    'residential': {
        'label': 'Residential Moving',
        'description': 'Move your home with the right property, inventory, and handling details.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {
            'property': ('property_type', 'bedrooms', 'bathrooms', 'floors', 'approximate_size_m2', 'elevator', 'loading_access', 'stairs'),
            'inventory': ('inventory_items', 'fragile_items', 'special_handling'),
        },
    },
    'commercial': {
        'label': 'Commercial Moving',
        'description': 'Move your business with the right property, inventory, equipment, and handling details.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {
            'property': ('business_name', 'business_type', 'workstations', 'rooms', 'loading_access', 'elevator'),
            'inventory': ('office_inventory', 'equipment', 'fragile_items', 'special_handling'),
        },
    },
    'packing': {
        'label': 'Packing Service',
        'description': 'Tell us what needs packing so we can recommend the right packing service.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {
            'property': ('packing_scope', 'rooms', 'box_count', 'fragile_items', 'materials_provided'),
            'inventory': ('packing_items', 'fragile_items', 'special_handling'),
        },
    },
    'storage': {
        'label': 'Storage',
        'description': 'Tell us what you need to store and how long you need storage.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {
            'property': ('storage_duration', 'access_frequency', 'pickup_method', 'delivery_method'),
            'inventory': ('storage_items', 'storage_duration', 'access_frequency', 'pickup_method', 'delivery_method'),
        },
    },
    'logistics': {
        'label': 'Logistics',
        'description': 'Tell us what needs to be transported, where it is going, and what vehicle or handling is required.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {
            'property': ('item_type', 'quantity', 'weight_kg', 'dimensions', 'fragile', 'recipient_name'),
            'inventory': ('item_type', 'quantity', 'weight_kg', 'dimensions', 'fragile', 'recipient_name'),
        },
    },
}

SERVICE_RECOMMENDATIONS = {
    'residential': (
        ('professional_packing', 'recommended', 'Professional Packing', 'Your inventory is large enough to benefit from trained packing.'),
        ('fragile_protection', 'recommended', 'Fragile Item Protection', 'Your inventory includes fragile items.'),
        ('furniture_assembly', 'optional', 'Furniture Assembly', 'You selected furniture that may need reassembly.'),
    ),
    'commercial': (
        ('office_packing', 'recommended', 'Professional Office Packing', 'Your business inventory includes equipment and workstations.'),
        ('special_handling', 'recommended', 'Special Handling', 'Your inventory includes equipment requiring specialist care.'),
        ('equipment_protection', 'recommended', 'Equipment Protection', 'The move includes valuable or fragile equipment.'),
    ),
    'packing': (
        ('transport', 'recommended', 'Moving & Transportation', 'Packing has a pickup and destination; add transportation to complete the service bundle.'),
        ('materials', 'recommended', 'Packing Materials', 'The selected packing scope requires protective materials.'),
    ),
    'storage': (
        ('storage_duration', 'required', 'Secure Storage', 'Storage duration and capacity are required for this service.'),
        ('pickup_delivery', 'recommended', 'Pickup and Delivery', 'Your storage plan includes a pickup or delivery requirement.'),
    ),
    'logistics': (
        ('vehicle_fit', 'required', 'Vehicle Recommendation', 'The item dimensions and weight determine the transport vehicle.'),
        ('special_handling', 'recommended', 'Special Handling', 'The item is fragile or requires specialist handling.'),
    ),
}

SERVICE_COMPATibilities = {
    'residential': frozenset({'residential', 'packing', 'storage', 'cleaning', 'home_decor'}),
    'commercial': frozenset({'commercial', 'packing', 'storage', 'cleaning', 'home_decor'}),
    'packing': frozenset({'packing', 'residential', 'commercial', 'transport'}),
    'storage': frozenset({'storage', 'residential', 'commercial', 'logistics'}),
    'logistics': frozenset({'logistics', 'packing', 'storage'}),
}

def _question(key, label, input_type, *, required=False, options=(), minimum=None, maximum=None, step=None, condition=None):
    return {
        'key': key, 'label': label, 'type': input_type, 'required': required,
        'options': options, 'min': minimum, 'max': maximum, 'step': step,
        'condition': condition,
    }


SERVICE_QUESTION_SCHEMAS = {
    'residential': {
        'property': (
            _question('destination_property_type', 'Destination property type', 'select', required=True,
                      options=('Apartment', 'House', 'Duplex', 'Other')),
            _question('destination_property_other', 'Describe the destination property', 'text', required=True, maximum=120,
                      condition=('destination_property_type', 'Other')),
            _question('restricted_vehicle_access', 'Is truck access restricted at either property?', 'select', required=True,
                      options=('no', 'yes')),
            _question('long_carry_distance', 'Is there a long carry from the entrance to the vehicle?', 'select', required=True,
                      options=('no', 'yes')),
        ),
        'inventory': (
            _question('household_inventory_notes', 'Household items not covered by the catalogue or special handling notes', 'textarea', maximum=1000),
            _question('heavy_or_oversized', 'Are any household items especially heavy or oversized?', 'select', required=True,
                      options=('no', 'yes')),
        ),
    },
    'commercial': {
        'property': (
            _question('current_workspace_size_m2', 'Current workspace size (m²)', 'number', required=True,
                      minimum=1, maximum=100000, step=0.1),
            _question('destination_workspace_size_m2', 'Destination workspace size (m²)', 'number', required=True,
                      minimum=1, maximum=100000, step=0.1),
            _question('business_work_areas', 'Number of rooms, departments or work areas', 'number', required=True,
                      minimum=1, maximum=500, step=1),
            _question('business_operating_during_move', 'Will the business operate during the move?', 'select', required=True,
                      options=('no', 'yes')),
            _question('shutdown_window', 'Preferred shutdown window', 'text', required=True, maximum=250,
                      condition=('business_operating_during_move', 'yes')),
            _question('loading_hours_restricted', 'Are loading hours restricted?', 'select', required=True,
                      options=('no', 'yes')),
            _question('loading_hours', 'Allowed loading hours', 'text', required=True, maximum=160,
                      condition=('loading_hours_restricted', 'yes')),
            _question('building_move_rules', 'Building-management moving rules', 'textarea', maximum=1000),
            _question('phased_relocation', 'Is a phased relocation required?', 'select', required=True,
                      options=('no', 'yes')),
        ),
        'inventory': (
            _question('business_inventory_notes', 'Business stock, office equipment or inventory notes', 'textarea', maximum=1000),
            _question('it_equipment_handling', 'Does IT equipment need separate careful handling?', 'select', required=True,
                      options=('no', 'yes')),
            _question('confidential_files', 'Are confidential files included?', 'select', required=True,
                      options=('no', 'yes')),
            _question('specialist_equipment', 'Does the inventory include specialist equipment?', 'select', required=True,
                      options=('no', 'yes')),
        ),
    },
    'packing': {
        'property': (
            _question('packing_for', 'What needs packing?', 'select', required=True,
                      options=('home', 'business')),
            _question('packing_scope', 'Packing scope', 'select', required=True,
                      options=('entire-property', 'selected-rooms')),
            _question('packing_rooms', 'Rooms or work areas to pack', 'number', required=True,
                      minimum=1, maximum=500, step=1, condition=('packing_scope', 'selected-rooms'),
            ),
            _question('packing_materials_needed', 'Should Chigo supply packing materials?', 'select', required=True,
                      options=('no', 'yes')),
            _question('packing_materials_available', 'Do you already have boxes or protective materials?', 'select', required=True,
                      options=('no', 'yes'), condition=('packing_materials_needed', 'no')),
        ),
        'inventory': (
            _question('packing_item_notes', 'Which selected items need packing or unpacking?', 'textarea', maximum=1000),
            _question('packing_special_items', 'Fragile or awkward items needing special packing attention', 'textarea', maximum=1000),
        ),
    },
    'storage': {
        'property': (
            _question('storage_duration', 'Expected storage duration', 'select', required=True,
                      options=('1 week', '1 month', '3 months', 'not-sure')),
            _question('storage_collection', 'Do you need collection arranged?', 'select', required=True,
                      options=('yes', 'no')),
            _question('storage_retrieval', 'Will you need later delivery to an address?', 'select', required=True,
                      options=('yes', 'no')),
            _question('storage_retrieval_notes', 'Expected retrieval timing or delivery details', 'textarea', required=True, maximum=500,
                      condition=('storage_retrieval', 'yes')),
        ),
        'inventory': (
            _question('stored_item_summary', 'What items need storage?', 'textarea', required=True, maximum=1000),
            _question('estimated_storage_volume_m3', 'Approximate total storage volume (m³)', 'number', minimum=0.1, maximum=1000, step=0.1),
        ),
    },
    'logistics': {
        'property': (),
        'inventory': (
            _question('shipment_type', 'What are you transporting?', 'select', required=True,
                      options=('Furniture', 'Appliances', 'Boxes or packages', 'Business goods', 'Equipment', 'Other')),
            _question('shipment_description', 'Describe the shipment', 'text', required=True, maximum=200,
                      condition=('shipment_type', 'Other')),
            _question('shipment_quantity', 'Number of items or packages', 'number', required=True,
                      minimum=1, maximum=500, step=1),
            _question('shipment_weight_kg', 'Approximate total weight (kg)', 'number', required=True,
                      minimum=0.1, maximum=10000, step=0.1),
            _question('shipment_length_cm', 'Length (cm)', 'number', required=True, minimum=1, maximum=100000, step=0.1),
            _question('shipment_width_cm', 'Width (cm)', 'number', required=True, minimum=1, maximum=100000, step=0.1),
            _question('shipment_height_cm', 'Height (cm)', 'number', required=True, minimum=1, maximum=100000, step=0.1),
            _question('shipment_ready', 'Is the shipment ready for collection?', 'select', required=True,
                      options=('no', 'yes')),
            _question('shipment_fragile', 'Does it need fragile handling?', 'select', required=True,
                      options=('no', 'yes')),
            _question('loading_assistance', 'Do you need loading and unloading assistance?', 'select', required=True,
                      options=('no', 'yes')),
            _question('shipment_special_equipment', 'Does it require special equipment or specialist handling?', 'select', required=True,
                      options=('no', 'yes')),
        ),
    },
}

ADDON_QUESTION_SCHEMAS = {
    'packing': SERVICE_QUESTION_SCHEMAS['packing'],
    'storage': SERVICE_QUESTION_SCHEMAS['storage'],
    'cleaning': {
        'property': (
            _question('cleaning_at', 'Which existing booking location needs cleaning?', 'select', required=True,
                      options=('pickup', 'destination', 'both', 'other')),
            _question('cleaning_other_location_notes', 'Describe the cleaning address or location', 'text', required=True, maximum=250,
                      condition=('cleaning_at', 'other')),
            _question('cleaning_property_type', 'Property type to clean', 'select', required=True,
                      options=('Apartment', 'House or duplex', 'Office', 'Shop', 'Other')),
            _question('cleaning_property_other', 'Describe the property type', 'text', required=True, maximum=120,
                      condition=('cleaning_property_type', 'Other')),
            _question('cleaning_bedrooms', 'Bedrooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_property_type', 'Apartment')),
            _question('cleaning_bathrooms', 'Bathrooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_property_type', 'Apartment')),
            _question('cleaning_house_bedrooms', 'Bedrooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_property_type', 'House or duplex')),
            _question('cleaning_house_bathrooms', 'Bathrooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_property_type', 'House or duplex')),
            _question('cleaning_house_size_m2', 'Approximate property size (m²)', 'number', required=True, minimum=1, maximum=100000, step=0.1,
                      condition=('cleaning_property_type', 'House or duplex')),
            _question('cleaning_business_size_m2', 'Approximate property size (m²)', 'number', required=True, minimum=1, maximum=100000, step=0.1,
                      condition=('cleaning_property_type', 'Office')),
            _question('cleaning_occupancy', 'Is the property currently occupied?', 'select', required=True,
                      options=('no', 'yes')),
        ),
        'inventory': (
            _question('cleaning_condition', 'Current cleaning condition', 'select', required=True,
                      options=('light', 'normal', 'heavy')),
            _question('cleaning_areas', 'Areas to clean', 'textarea', required=True, maximum=1000),
            _question('cleaning_scope_notes', 'Cleaning scope or access notes', 'textarea', maximum=1000),
        ),
    },
    'home_decor': {
        'property': (
            _question('decor_at', 'Which existing booking location needs setup?', 'select', required=True,
                      options=('pickup', 'destination', 'both', 'other')),
            _question('decor_other_location_notes', 'Describe the setup address or location', 'text', required=True, maximum=250,
                      condition=('decor_at', 'other')),
            _question('decor_setup_type', 'Setup type', 'select', required=True,
                      options=('new-home-setup', 'room-refresh')),
            _question('decor_rooms', 'Areas to set up', 'multiselect', required=True,
                      options=('living-room', 'bedroom', 'dining-area', 'office', 'other')),
            _question('decor_room_count', 'Number of rooms', 'number', required=True,
                      minimum=1, maximum=100, step=1),
        ),
        'inventory': (
            _question('decor_furniture_available', 'Is the furniture already at the property?', 'select', required=True,
                      options=('no', 'yes')),
            _question('decor_tasks', 'Setup tasks required', 'textarea', required=True, maximum=1000),
            _question('decor_furniture_items', 'Furniture or accessories to arrange', 'textarea', maximum=1000),
            _question('decor_assembly_needed', 'Is furniture assembly required?', 'select', required=True,
                      options=('no', 'yes'), condition=('decor_furniture_available', 'yes')),
            _question('decor_transport_needed', 'Will any new items need transport?', 'select', required=True,
                      options=('no', 'yes')),
            _question('decor_transport_items', 'Describe items requiring transport', 'textarea', required=True, maximum=500,
                      condition=('decor_transport_needed', 'yes')),
            _question('decor_placement_notes', 'Placement preferences or room instructions', 'textarea', maximum=1000),
        ),
    },
}
ADDON_QUESTION_SCHEMAS['packing'] = {
    'property': (
        _question('packing_areas', 'Which rooms or selected items should be packed?', 'textarea', required=True, maximum=1000),
        _question('packing_materials_needed', 'Should Chigo supply packing materials?', 'select', required=True,
                  options=('no', 'yes')),
        _question('packing_materials_available', 'Do you already have boxes or protective materials?', 'select', required=True,
                  options=('no', 'yes'), condition=('packing_materials_needed', 'no')),
    ),
    'inventory': (
        _question('packing_item_notes', 'Items needing special packing attention', 'textarea', maximum=1000),
        _question('packing_special_items', 'Fragile or awkward items needing special packing attention', 'textarea', maximum=1000),
    ),
}
ADDON_QUESTION_SCHEMAS['storage'] = SERVICE_QUESTION_SCHEMAS['storage']


def validate_question_answers(schema, answers: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only configured answers and enforce required/conditional constraints."""
    submitted = dict(answers or {})
    definitions = {field['key']: field for field in schema}
    unknown = sorted(set(submitted) - set(definitions))
    if unknown:
        raise ValueError(f'Unknown service answer: {", ".join(unknown)}.')

    normalized = {}
    for field in schema:
        key = field['key']
        value = submitted.get(key)
        condition = field['condition']
        visible = condition is None or str(submitted.get(condition[0], '')).casefold() == condition[1].casefold()
        if not visible:
            continue
        if value is None:
            value = ''
        if isinstance(value, str):
            value = value.strip()
        if field['type'] == 'multiselect':
            if value in ('', None):
                value = []
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"Choose one or more options for {field['label'].lower()}.")
            value = list(dict.fromkeys(value))
            if field['required'] and not value:
                raise ValueError(f"{field['label']} is required.")
            if any(option not in field['options'] for option in value):
                raise ValueError(f"Choose valid options for {field['label'].lower()}.")
            if value:
                normalized[key] = value
            continue
        if field['required'] and value == '':
            raise ValueError(f"{field['label']} is required.")
        if value == '':
            continue
        if field['type'] == 'select' and value not in field['options']:
            raise ValueError(f"Choose a valid option for {field['label'].lower()}.")
        if field['type'] == 'number':
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Enter a valid number for {field['label'].lower()}.") from None
            if field['step'] == 1:
                if not number.is_integer():
                    raise ValueError(f"{field['label']} must be a whole number.")
                number = int(number)
            if field['min'] is not None and number < field['min']:
                raise ValueError(f"{field['label']} must be at least {field['min']}.")
            if field['max'] is not None and number > field['max']:
                raise ValueError(f"{field['label']} must be no more than {field['max']}.")
            value = number
        if field['type'] in {'text', 'textarea'} and field['max'] and len(value) > field['max']:
            raise ValueError(f"{field['label']} must be no more than {field['max']} characters.")
        normalized[key] = value
    return normalized


def service_question_schemas_for(service_type: str) -> dict[str, tuple[dict[str, Any], ...]]:
    return SERVICE_QUESTION_SCHEMAS[normalize_service_type(service_type)]


def service_questions_for(service_type: str, section: str = 'property') -> tuple[dict[str, Any], ...]:
    if section not in {'property', 'inventory'}:
        raise ValueError(f'Unknown service question section: {section}.')
    return service_question_schemas_for(service_type)[section]


def addon_questions_for(addons) -> dict[str, tuple[dict[str, Any], ...]]:
    selected = set(addons or ())
    return {key: ADDON_QUESTION_SCHEMAS[key] for key in selected if key in ADDON_QUESTION_SCHEMAS}


def normalize_service_type(service_type: str) -> str:
    normalized = str(service_type or '').strip().casefold()
    if normalized not in SERVICE_TYPES:
        raise ValueError(f'Unsupported service type: {service_type or "empty"}. Choose residential, commercial, packing, storage, or logistics.')
    return normalized


def _booking_value(booking: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    return booking.get(key, default) if isinstance(booking, Mapping) else getattr(booking, key, default)


def validate_booking_question_data(booking: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return normalized active answers; legacy requests without a schema version remain readable."""
    workflow_data = dict(_booking_value(booking, 'workflow_data', {}) or {})
    if workflow_data.get('question_schema_version', 0) < 2:
        return workflow_data

    service_type = normalize_service_type(_booking_value(booking, 'primary_service') or 'residential')
    service_intent = workflow_data.get('service_intent')
    if service_intent != service_type:
        raise ValueError('The primary service for this booking cannot be changed. Start a new booking to choose another service.')
    location_access = workflow_data.get('location_access') or {}
    for place in ('pickup', 'destination'):
        floor = _booking_value(booking, f'{place}_floor') or 'Ground'
        lift_value = location_access.get(f'{place}_lift_available')
        if floor != 'Ground' and lift_value not in {True, False}:
            raise ValueError(f'Confirm whether a lift is available at the above-ground {place} location.')
    property_data = dict(workflow_data.get('property') or {})
    inventory_answers = workflow_data.get('inventory_answers') or {}
    property_answers = property_data.get('service_answers') or {}
    if not isinstance(property_answers, Mapping) or not isinstance(inventory_answers, Mapping):
        raise ValueError('Service property and inventory answers must be objects.')
    property_data['service_answers'] = validate_question_answers(
        service_questions_for(service_type, 'property'), property_answers,
    )
    workflow_data['property'] = property_data
    workflow_data['inventory_answers'] = validate_question_answers(
        service_questions_for(service_type, 'inventory'), inventory_answers,
    )

    service_selections = workflow_data.get('services') or {}
    if not isinstance(service_selections, Mapping):
        raise ValueError('Selected services must be an object.')
    selected_addons = {
        key for key in ('packing', 'storage', 'cleaning', 'home_decor')
        if key != service_type and service_selections.get(key) not in {None, '', 'none', 'no'}
    }
    addon_answers = workflow_data.get('addon_answers') or {}
    if not isinstance(addon_answers, Mapping):
        raise ValueError('Additional service answers must be an object.')
    normalized_addons = {}
    for addon, sections in addon_questions_for(selected_addons).items():
        saved_sections = addon_answers.get(addon) or {}
        if not isinstance(saved_sections, Mapping):
            raise ValueError(f"Answers for {addon.replace('_', ' ')} must be an object.")
        normalized_addons[addon] = {}
        for section, schema in sections.items():
            answers = saved_sections.get(section) or {}
            if not isinstance(answers, Mapping):
                raise ValueError(f"Answers for {addon.replace('_', ' ')} {section} must be an object.")
            normalized_addons[addon][section] = validate_question_answers(schema, answers)
    workflow_data['addon_answers'] = normalized_addons
    return workflow_data


def property_types_for_service(service_type: str) -> tuple[str, ...]:
    normalized = normalize_service_type(service_type)
    if normalized not in {'residential', 'commercial'}:
        return ()
    return PROPERTY_TYPES_BY_SERVICE[normalized]


def validate_property_type_for_service(service_type: str, property_type: str | None) -> str:
    normalized_service = normalize_service_type(service_type)
    allowed = property_types_for_service(normalized_service)
    if property_type is None:
        raise ValueError(f'Choose a valid property type for {normalized_service}.')
    candidate = str(property_type).strip()
    canonical = next((value for value in allowed if value.casefold() == candidate.casefold() or value.casefold().replace(' ', '') == candidate.casefold().replace(' ', '')), None)
    if canonical is None:
        raise ValueError(f"{normalized_service} property type '{candidate or 'Unknown property type'}' is not valid. Choose from: {', '.join(allowed)}.")
    return canonical


def get_service_workflow(booking: Mapping[str, Any] | Any) -> dict[str, Any]:
    service_type = normalize_service_type(_booking_value(booking, 'primary_service') or 'residential')
    workflow = SERVICE_WORKFLOWS[service_type]
    questions = dict(workflow['questions'])
    service_schema = service_question_schemas_for(service_type)
    questions['property'] = tuple(field['key'] for field in service_schema['property'])
    questions['inventory'] = tuple(field['key'] for field in service_schema['inventory'])
    return {
        'service_type': service_type,
        'label': workflow['label'],
        'description': workflow['description'],
        'steps': workflow['steps'],
        'questions': questions,
    }


def recommendations_for_booking(booking: Mapping[str, Any] | Any) -> list[dict[str, str]]:
    service_type = normalize_service_type(_booking_value(booking, 'primary_service') or 'residential')
    workflow_data = _booking_value(booking, 'workflow_data', {}) or {}
    recommendations = []
    for key, priority, label, reason in SERVICE_RECOMMENDATIONS[service_type]:
        if key == 'transport' and service_type == 'packing':
            if workflow_data.get('services', {}).get('transport') != 'yes':
                recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'yes'})
        elif key == 'storage_duration' and service_type == 'storage':
            if workflow_data.get('property', {}).get('storage_duration'):
                recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'required'})
        elif key == 'vehicle_fit' and service_type == 'logistics':
            recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'required'})
        elif key == 'professional_packing' and service_type == 'residential':
            recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'partial'})
        elif key == 'office_packing' and service_type == 'commercial':
            recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'partial'})
        elif key == 'special_handling' and service_type in {'commercial', 'logistics'}:
            recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'required'})
        elif key in {'fragile_protection', 'equipment_protection', 'materials', 'pickup_delivery', 'furniture_assembly'}:
            recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'yes'})
    return recommendations


def validate_service_bundle(booking: Mapping[str, Any] | Any) -> tuple[str, ...]:
    service_type = normalize_service_type(_booking_value(booking, 'primary_service') or 'residential')
    workflow_data = _booking_value(booking, 'workflow_data', {}) or {}
    selected = set(workflow_data.get('services', {}) or {})
    selected.add(service_type)
    invalid = sorted(selected - SERVICE_COMPATibilities[service_type])
    if invalid:
        raise ValueError(f'Incompatible service combination: {", ".join(invalid)}.')
    return tuple(sorted(selected))
