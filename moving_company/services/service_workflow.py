from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVICE_TYPES = frozenset({'cleaning', 'residential', 'commercial', 'packing', 'storage', 'logistics', 'support', 'home_decor'})
QUESTION_SCHEMA_VERSION = 4
LEGACY_PACKING_SCHEMA_CUTOFF = 3
LEGACY_PACKING_QUESTION_KEYS = {
    'property': frozenset({'packing_for', 'packing_scope', 'packing_rooms', 'packing_materials_needed', 'packing_materials_available'}),
    'inventory': frozenset({'packing_item_notes', 'packing_special_items'}),
}
PROPERTY_TYPES_BY_SERVICE = {
    'cleaning': ('Self-contained / studio', 'Flat / apartment', 'Duplex', 'Detached house', 'Bungalow', 'Office', 'Shop', 'Other'),
    'residential': (
        'Apartment', 'House', 'Duplex', 'Flat / Apartment', 'Self-contained',
        'Detached House', 'Semi-detached House', 'Bungalow', 'Terrace House',
        'Other',
    ),
    'commercial': ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
    'packing': ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate'),
    'storage': ('Apartment', 'House', 'Duplex', 'Office', 'Shop', 'Warehouse', 'Hotel', 'Estate'),
    'logistics': ('Office', 'Shop', 'Warehouse', 'Hotel', 'Estate', 'Construction Site'),
    'support': (),
    'home_decor': (),
}

SERVICE_WORKFLOWS = {
    'cleaning': {
        'label': 'Cleaning',
        'description': 'Choose Move-Clean, fumigation, upholstery or appliance care and tell us about the relevant property or items.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {'property': ('cleaning_subtype', 'cleaning_type', 'cleaning_areas', 'cleaning_frequency'),
                      'inventory': ('cleaning_items', 'cleaning_instructions')},
    },
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
        'steps': ('location', 'inventory', 'services', 'schedule', 'quote'),
        'questions': {
            'property': ('item_type', 'quantity', 'weight_kg', 'dimensions', 'fragile', 'recipient_name'),
            'inventory': ('item_type', 'quantity', 'weight_kg', 'dimensions', 'fragile', 'recipient_name'),
        },
    },
    'support': {
        'label': '24/7 Support',
        'description': 'Tell us what you need help with. An operations team member will confirm availability.',
        'steps': ('location', 'property', 'inventory', 'services', 'schedule', 'quote', 'payment', 'review'),
        'questions': {'property': ('support_category', 'support_urgency', 'support_description', 'onsite_required'),
                      'inventory': ('booking_reference', 'support_contact_preference')},
    },
    'home_decor': {
        'label': 'Home Décor',
        'description': 'Share your property, design preferences, project scope and timeline for an operations-reviewed décor enquiry.',
        'steps': ('property', 'inventory', 'services', 'schedule', 'quote'),
        'questions': {'property': ('home_decor_request',), 'inventory': ('home_decor_attachments',)},
    },
}

SERVICE_RECOMMENDATIONS = {
    'cleaning': (),
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
    'support': (),
    'home_decor': (),
}

SERVICE_COMPATibilities = {
    'cleaning': frozenset({'cleaning'}),
    'residential': frozenset({'residential', 'packing', 'storage', 'cleaning', 'home_decor'}),
    'commercial': frozenset({'commercial', 'packing', 'storage', 'cleaning', 'home_decor'}),
    'packing': frozenset({'packing', 'residential', 'commercial', 'transport'}),
    'storage': frozenset({'storage', 'residential', 'commercial', 'logistics'}),
    'logistics': frozenset({'logistics', 'packing', 'storage'}),
    'support': frozenset({'support'}),
    'home_decor': frozenset({'home_decor'}),
}

def _question(key, label, input_type, *, required=False, options=(), minimum=None, maximum=None, step=None, condition=None):
    return {
        'key': key, 'label': label, 'type': input_type, 'required': required,
        'options': options, 'min': minimum, 'max': maximum, 'step': step,
        'condition': condition,
    }


SERVICE_QUESTION_SCHEMAS = {
    'cleaning': {
        'property': (
            _question('morph_cleaning_category', 'Cleaning category', 'select', required=True,
                      options=('legacy', 'residential-move-clean', 'commercial-move-clean',
                               'fumigation-pest-control', 'upholstery-care', 'kitchen-appliance-cleaning')),
            _question('cleaning_subtype', 'Cleaning service', 'select', required=True,
                      options=('home', 'office', 'move-in-out'), condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_stage', 'Cleaning is for', 'select', required=True,
                      options=('move-in', 'move-out', 'handover', 'post-renovation'),
                      condition=('cleaning_subtype', 'move-in-out')),
            _question('cleaning_property_type', 'Property type', 'select', required=True,
                      options=('studio', 'flat', 'duplex', 'detached-house', 'bungalow', 'office-premises', 'coworking-space', 'shop', 'other'),
                      condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_property_other', 'Describe the property', 'text', required=True, maximum=120,
                      condition=('cleaning_property_type', 'other')),
            _question('cleaning_business_type', 'Commercial premises type', 'select', required=True,
                      options=('office', 'coworking', 'shop', 'other'), condition=('cleaning_subtype', 'office')),
            _question('cleaning_business_other', 'Describe the premises', 'text', required=True, maximum=120,
                      condition=('cleaning_business_type', 'other')),
            _question('cleaning_bedrooms', 'Bedrooms or rooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_subtype', 'home')),
            _question('cleaning_bathrooms', 'Bathrooms and toilets', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_subtype', 'home')),
            _question('cleaning_move_rooms', 'Bedrooms or rooms', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_subtype', 'move-in-out')),
            _question('cleaning_move_bathrooms', 'Bathrooms and toilets', 'number', required=True, minimum=0, maximum=100, step=1,
                      condition=('cleaning_subtype', 'move-in-out')),
            _question('cleaning_floors', 'Number of floors', 'number', required=True, minimum=1, maximum=100, step=1,
                      condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_size_m2', 'Approximate floor area (m²)', 'number', minimum=1, maximum=100000, step=0.1,
                      condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_workstations', 'Approximate workstations', 'number', minimum=0, maximum=10000, step=1,
                      condition=('cleaning_subtype', 'office')),
            _question('cleaning_meeting_rooms', 'Meeting rooms', 'number', minimum=0, maximum=1000, step=1,
                      condition=('cleaning_subtype', 'office')),
            _question('cleaning_restrooms', 'Restrooms', 'number', required=True, minimum=0, maximum=1000, step=1,
                      condition=('cleaning_subtype', 'office')),
            _question('cleaning_occupied', 'Will the property be occupied during cleaning?', 'select', required=True,
                      options=('yes', 'no'), condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_type', 'Cleaning type', 'select', required=True,
                      options=('regular', 'deep', 'post-renovation'), condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_areas', 'Areas to be cleaned', 'multiselect', required=True,
                      options=('entire-property', 'living-room', 'bedrooms', 'kitchen', 'bathrooms', 'balcony',
                               'windows', 'cupboards', 'hallways', 'outdoor-areas', 'workstations', 'meeting-rooms',
                               'restrooms', 'interior-glass', 'floor-care'), condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_frequency', 'Cleaning frequency', 'select', required=True,
                      options=('one-time', 'weekly', 'twice-weekly', 'monthly', 'other'),
                      condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_frequency_other', 'Describe the requested arrangement', 'text', required=True, maximum=200,
                      condition=('cleaning_frequency', 'other')),
            _question('cleaning_access_hours', 'Access hours or restrictions', 'text', maximum=250,
                      condition=('morph_cleaning_category', 'legacy')),
            _question('morph_fumigation_property_type', 'Property type for fumigation', 'select', required=True,
                      options=('studio', 'apartment', 'house', 'duplex', 'office', 'shop', 'warehouse', 'other'),
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_fumigation_property_other', 'Describe the property', 'text', required=True, maximum=120,
                      condition=('morph_fumigation_property_type', 'other')),
            _question('morph_fumigation_size_sqm', 'Approximate property size (m²)', 'number', required=True,
                      minimum=1, maximum=100000, step=0.1,
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_fumigation_pest_type', 'Pest or infestation type', 'select', required=True,
                      options=('ants', 'cockroaches', 'termites', 'rodents', 'mosquitoes', 'bed-bugs', 'other'),
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_fumigation_pest_other', 'Describe the pest or infestation', 'text', required=True, maximum=160,
                      condition=('morph_fumigation_pest_type', 'other')),
            _question('morph_fumigation_treatment_areas', 'Areas requiring treatment', 'multiselect', required=True,
                      options=('entire-property', 'bedrooms', 'kitchen', 'bathrooms', 'storage-areas', 'outdoor-areas', 'office-areas'),
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_fumigation_occupancy', 'Property occupancy during treatment', 'select', required=True,
                      options=('vacant', 'occupied', 'not-sure'),
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_fumigation_access_notes', 'Access, ventilation or safety considerations', 'textarea', maximum=1000,
                      condition=('morph_cleaning_category', 'fumigation-pest-control')),
            _question('morph_residential_property_type', 'Residential property size', 'select', required=True,
                      options=('self-contain', '1-bedroom', '2-bedroom', '3-bedroom', '4-bedroom',
                               '5-bedroom', '6-bedroom', '7-bedroom'),
                      condition=('morph_cleaning_category', 'residential-move-clean')),
            _question('morph_commercial_area_sqm', 'Commercial floor area (m²)', 'number', required=True,
                      minimum=0.1, maximum=100000, step=0.1,
                      condition=('morph_cleaning_category', 'commercial-move-clean')),
            _question('morph_couch_1_seater', '1-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_2_seater', '2-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_3_seater', '3-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_4_seater', '4-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_5_seater', '5-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_6_seater', '6-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_couch_7_seater', '7-seater couches', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_3x6_ft', '3 × 6 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_4x6_ft', '4 × 6 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_5x6_ft', '5 × 6 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_6x6_ft', '6 × 6 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_6x7_ft', '6 × 7 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_bedframe_7x7_ft', '7 × 7 ft bedframes', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'upholstery-care')),
            _question('morph_appliance_oven', 'Ovens', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'kitchen-appliance-cleaning')),
            _question('morph_appliance_refrigerator', 'Refrigerators', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'kitchen-appliance-cleaning')),
            _question('morph_appliance_microwave', 'Microwaves', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'kitchen-appliance-cleaning')),
            _question('morph_appliance_dishwasher', 'Dishwashers', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'kitchen-appliance-cleaning')),
            _question('morph_appliance_other_approved', 'Other approved appliances', 'number', minimum=0, maximum=500, step=1,
                      condition=('morph_cleaning_category', 'kitchen-appliance-cleaning')),
            _question('morph_logistics_requested', 'Cleaning logistics requested separately?', 'select',
                      options=('no', 'yes'), condition=('morph_cleaning_category', 'residential-move-clean')),
            _question('morph_logistics_separate', 'Is logistics separate from general cleaning?', 'select',
                      options=('no', 'yes'), condition=('morph_cleaning_category', 'residential-move-clean')),
            _question('morph_commercial_logistics_requested', 'Cleaning logistics requested separately?', 'select',
                      options=('no', 'yes'), condition=('morph_cleaning_category', 'commercial-move-clean')),
            _question('morph_commercial_logistics_separate', 'Is logistics separate from general cleaning?', 'select',
                      options=('no', 'yes'), condition=('morph_cleaning_category', 'commercial-move-clean')),
        ),
        'inventory': (
            _question('cleaning_items', 'Large items or specialist tasks (include quantities)', 'textarea', maximum=1000,
                      condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_heavy_buildup', 'Is there heavy buildup, construction dust or significant staining?', 'select', required=True,
                      options=('no', 'yes'), condition=('morph_cleaning_category', 'legacy')),
            _question('cleaning_instructions', 'Special instructions, delicate surfaces, pets or access concerns', 'textarea', maximum=1200),
        ),
    },
    'residential': {
        'property': (
            _question('destination_property_type', 'Destination property type', 'select', required=True,
                      options=('Flat / Apartment', 'Self-contained', 'Duplex', 'Detached House',
                               'Semi-detached House', 'Bungalow', 'Terrace House', 'Other')),
            _question('destination_property_other', 'Describe the destination property', 'text', required=True, maximum=120,
                      condition=('destination_property_type', 'Other')),
            _question('destination_bedrooms', 'Bedrooms at destination', 'number', minimum=0, maximum=100, step=1,
                      condition=('destination_property_type', (
                          'Flat / Apartment', 'Self-contained', 'Duplex', 'Detached House',
                          'Semi-detached House', 'Bungalow', 'Terrace House',
                      ))),
            _question('destination_floors', 'Destination property floors or storeys', 'number', minimum=1, maximum=100, step=1,
                      condition=('destination_property_type', (
                          'Flat / Apartment', 'Self-contained', 'Duplex', 'Detached House',
                          'Semi-detached House', 'Bungalow', 'Terrace House',
                      ))),
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
            _question('packing_room_list', 'Which rooms or work areas?', 'textarea', required=True, maximum=500,
                      condition=('packing_scope', 'selected-rooms')),
            _question('packing_box_estimate', 'Approximate number of boxes, if known', 'number', minimum=0, maximum=10000, step=1),
            _question('packing_already_packed', 'Are any items already packed?', 'select', required=True,
                      options=('no', 'some', 'most')),
            _question('packing_materials_needed', 'Should Chigo supply packing materials?', 'select', required=True,
                      options=('no', 'yes')),
            _question('packing_materials_available', 'Do you already have boxes or protective materials?', 'select', required=True,
                      options=('no', 'yes'), condition=('packing_materials_needed', 'no')),
            _question('packing_service_level', 'Packing help required', 'select', required=True,
                      options=('full-service', 'labour-only')),
            _question('unpacking_required', 'Will unpacking be needed?', 'select', required=True,
                      options=('no', 'yes')),
            _question('packing_dismantling', 'Is furniture dismantling needed?', 'select', required=True,
                      options=('no', 'yes')),
            _question('packing_reassembly', 'Is furniture reassembly needed?', 'select', required=True,
                      options=('no', 'yes')),
        ),
        'inventory': (
            _question('packing_item_notes', 'Which selected items need packing or unpacking?', 'textarea', maximum=1000),
            _question('packing_special_items', 'Fragile or awkward items needing special packing attention', 'textarea', maximum=1000),
            _question('packing_fragile_items', 'Are fragile, oversized or high-value items included?', 'select', required=True,
                      options=('no', 'yes')),
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
            _question('storage_return_known', 'Do you know the return or delivery address?', 'select', required=True,
                      options=('yes', 'no'), condition=('storage_retrieval', 'yes')),
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
            _question('delivery_urgency', 'Delivery timing', 'select', required=True,
                      options=('scheduled', 'urgent')),
            _question('sender_name', 'Sender name', 'text', required=True, maximum=120),
            _question('sender_phone', 'Sender phone', 'text', required=True, maximum=30),
            _question('recipient_name', 'Recipient name', 'text', required=True, maximum=120),
            _question('recipient_phone', 'Recipient phone', 'text', required=True, maximum=30),
            _question('delivery_instructions', 'Delivery instructions', 'textarea', maximum=1000),
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
    'support': {
        'property': (
            _question('support_category', 'What do you need help with?', 'select', required=True,
                      options=('urgent-moving', 'emergency-cleaning', 'urgent-delivery', 'storage-assistance',
                               'existing-booking', 'other')),
            _question('support_urgency', 'When do you need help?', 'select', required=True,
                      options=('immediately', 'within-hours', 'today', 'tomorrow')),
            _question('support_description', 'Briefly describe the issue', 'textarea', required=True, maximum=1200),
            _question('onsite_required', 'Is an on-site response required?', 'select', required=True,
                      options=('no', 'yes')),
        ),
        'inventory': (
            _question('booking_reference', 'Existing booking reference, if applicable', 'text', maximum=32),
            _question('support_contact_preference', 'Preferred contact method', 'select', required=True,
                      options=('phone', 'email')),
        ),
    },
    'home_decor': {'property': (), 'inventory': ()},
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
    if submitted.get('destination_property_type') == 'Apartment':
        submitted['destination_property_type'] = 'Flat / Apartment'
    elif submitted.get('destination_property_type') == 'House':
        submitted['destination_property_type'] = 'Detached House'
    if any(field['key'] == 'morph_cleaning_category' for field in schema):
        submitted.setdefault('morph_cleaning_category', 'legacy')
    definitions = {field['key']: field for field in schema}
    unknown = sorted(set(submitted) - set(definitions))
    if unknown:
        raise ValueError(f'Unknown service answer: {", ".join(unknown)}.')

    normalized = {}
    for field in schema:
        key = field['key']
        value = submitted.get(key)
        condition = field['condition']
        visible = True
        if condition:
            expected_values = condition[1] if isinstance(condition[1], (list, tuple, set, frozenset)) else (condition[1],)
            visible = any(
                str(submitted.get(condition[0], '')).casefold() == str(expected).casefold()
                for expected in expected_values
            )
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
            if key == 'cleaning_areas' and 'entire-property' in value:
                subtype = submitted.get('cleaning_subtype')
                office_areas = {'workstations', 'meeting-rooms', 'restrooms', 'interior-glass', 'floor-care'}
                home_areas = {'living-room', 'bedrooms', 'kitchen', 'bathrooms', 'balcony', 'cupboards', 'hallways', 'outdoor-areas'}
                value = [
                    option for option in field['options'] if option != 'entire-property'
                    and not (subtype == 'office' and option in home_areas)
                    and not (subtype != 'office' and option in office_areas)
                ]
            elif key == 'cleaning_areas':
                subtype = submitted.get('cleaning_subtype')
                office_areas = {'workstations', 'meeting-rooms', 'restrooms', 'interior-glass', 'floor-care'}
                home_areas = {'living-room', 'bedrooms', 'kitchen', 'bathrooms', 'balcony', 'cupboards', 'hallways', 'outdoor-areas'}
                if any((subtype == 'office' and option in home_areas) or (subtype != 'office' and option in office_areas) for option in value):
                    raise ValueError('Choose cleaning areas that apply to the selected property type.')
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
    if 'cleaning_subtype' in normalized:
        home_property_types = {'studio', 'flat', 'duplex', 'detached-house', 'bungalow', 'other'}
        office_property_types = {'office-premises', 'coworking-space', 'shop', 'other'}
        valid_types = office_property_types if normalized['cleaning_subtype'] == 'office' else home_property_types
        if normalized.get('cleaning_property_type') not in valid_types:
            raise ValueError('Choose a property type that matches the selected cleaning service.')
    return normalized


def service_question_schemas_for(service_type: str) -> dict[str, tuple[dict[str, Any], ...]]:
    return SERVICE_QUESTION_SCHEMAS[normalize_service_type(service_type)]


def service_questions_for(service_type: str, section: str = 'property') -> tuple[dict[str, Any], ...]:
    if section not in {'property', 'inventory'}:
        raise ValueError(f'Unknown service question section: {section}.')
    return service_question_schemas_for(service_type)[section]


def booking_questions_for(booking: Mapping[str, Any] | Any, section: str) -> tuple[dict[str, Any], ...]:
    service_type = normalize_service_type(_booking_value(booking, 'primary_service') or 'residential')
    schema = service_questions_for(service_type, section)
    workflow_data = _booking_value(booking, 'workflow_data', {}) or {}
    if service_type == 'packing' and workflow_data.get('question_schema_version', 0) < LEGACY_PACKING_SCHEMA_CUTOFF:
        allowed = LEGACY_PACKING_QUESTION_KEYS[section]
        return tuple(field for field in schema if field['key'] in allowed)
    return schema


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
    if service_type == 'home_decor':
        property_data = workflow_data.get('property') or {}
        if not isinstance(property_data, Mapping) or not isinstance(property_data.get('service_answers', {}), Mapping):
            raise ValueError('Home Décor request details must be an object.')
        if not _booking_value(booking, 'pickup_address'):
            raise ValueError('A verified property location is required for this décor request.')
        return workflow_data
    location_access = workflow_data.get('location_access') or {}
    for place in ('pickup', 'destination'):
        floor = _booking_value(booking, f'{place}_floor') or 'Ground'
        lift_value = location_access.get(f'{place}_lift_available')
        if floor != 'Ground' and lift_value not in {True, False}:
            raise ValueError(f'Confirm whether a lift is available at the above-ground {place} location.')
    property_data = dict(workflow_data.get('property') or {})
    inventory_answers = workflow_data.get('inventory_answers') or {}
    property_answers = property_data.get('service_answers') or {}
    if service_type == 'cleaning' and 'morph_cleaning_category' not in property_answers:
        property_answers = {**property_answers, 'morph_cleaning_category': 'legacy'}
    if not isinstance(property_answers, Mapping) or not isinstance(inventory_answers, Mapping):
        raise ValueError('Service property and inventory answers must be objects.')
    property_data['service_answers'] = validate_question_answers(
        booking_questions_for(booking, 'property'), property_answers,
    )
    workflow_data['property'] = property_data
    workflow_data['inventory_answers'] = validate_question_answers(
        booking_questions_for(booking, 'inventory'), inventory_answers,
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
    if service_type in {'residential', 'commercial', 'logistics'}:
        for place in ('pickup', 'destination'):
            if not _booking_value(booking, f'{place}_address'):
                raise ValueError(f'{place.title()} address is required for {service_type} bookings.')
    elif service_type in {'cleaning', 'packing'} and not _booking_value(booking, 'pickup_address'):
        raise ValueError('A service location is required for this booking.')
    elif service_type == 'storage':
        answers = property_data['service_answers']
        if answers.get('storage_collection') == 'yes' and not _booking_value(booking, 'pickup_address'):
            raise ValueError('A collection address is required when Chigo is arranging collection.')
        if answers.get('storage_retrieval') == 'yes' and answers.get('storage_return_known') == 'yes' and not _booking_value(booking, 'destination_address'):
            raise ValueError('A known return address is required for redelivery.')
    elif service_type == 'support' and property_answers.get('onsite_required') == 'yes' and not _booking_value(booking, 'pickup_address'):
        raise ValueError('An on-site support location is required for this request.')
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
