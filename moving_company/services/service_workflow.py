from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVICE_TYPES = frozenset({'residential', 'commercial', 'packing', 'storage', 'logistics'})
PROPERTY_TYPES_BY_SERVICE = {
    'residential': ('Apartment', 'House', 'Duplex'),
    'commercial': ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
    'packing': ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
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
    'residential': frozenset({'residential', 'packing', 'storage', 'cleaning'}),
    'commercial': frozenset({'commercial', 'packing', 'storage', 'cleaning'}),
    'packing': frozenset({'packing', 'residential', 'commercial', 'transport'}),
    'storage': frozenset({'storage', 'residential', 'commercial', 'logistics'}),
    'logistics': frozenset({'logistics', 'packing', 'storage'}),
}


def normalize_service_type(service_type: str) -> str:
    normalized = str(service_type or '').strip().casefold()
    if normalized not in SERVICE_TYPES:
        raise ValueError(f'Unsupported service type: {service_type or "empty"}. Choose residential, commercial, packing, storage, or logistics.')
    return normalized


def property_types_for_service(service_type: str) -> tuple[str, ...]:
    normalized = normalize_service_type(service_type)
    return PROPERTY_TYPES_BY_SERVICE.get(normalized, PROPERTY_TYPES_BY_SERVICE['residential'])


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
    service_type = normalize_service_type(getattr(booking, 'primary_service', None) or 'residential')
    workflow = SERVICE_WORKFLOWS[service_type]
    return {
        'service_type': service_type,
        'label': workflow['label'],
        'description': workflow['description'],
        'steps': workflow['steps'],
        'questions': dict(workflow['questions']),
    }


def recommendations_for_booking(booking: Mapping[str, Any] | Any) -> list[dict[str, str]]:
    service_type = normalize_service_type(getattr(booking, 'primary_service', None) or 'residential')
    recommendations = []
    for key, priority, label, reason in SERVICE_RECOMMENDATIONS[service_type]:
        if key == 'transport' and service_type == 'packing':
            if (booking.workflow_data or {}).get('services', {}).get('transport') != 'yes':
                recommendations.append({'key': key, 'priority': priority, 'label': label, 'reason': reason, 'value': 'yes'})
        elif key == 'storage_duration' and service_type == 'storage':
            if (booking.workflow_data or {}).get('property', {}).get('storage_duration'):
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
    service_type = normalize_service_type(getattr(booking, 'primary_service', None) or 'residential')
    selected = set((booking.workflow_data or {}).get('services', {}) or {})
    selected.add(service_type)
    invalid = sorted(selected - SERVICE_COMPATibilities[service_type])
    if invalid:
        raise ValueError(f'Incompatible service combination: {", ".join(invalid)}.')
    return tuple(sorted(selected))
