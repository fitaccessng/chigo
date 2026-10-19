from decimal import Decimal, InvalidOperation


RATE_CARD_VERSION = 'morph-cleaning-2026-10-v1'
CURRENCY = 'NGN'

RESIDENTIAL_MOVE_CLEAN = {
    'self-contain': 70_000,
    '1-bedroom': 95_000,
    '2-bedroom': 120_000,
    '3-bedroom': 200_000,
    '4-bedroom': 300_000,
    '5-bedroom': 400_000,
    '6-bedroom': 500_000,
    '7-bedroom': 600_000,
}

COMMERCIAL_MOVE_CLEAN = (
    (Decimal('50'), 'Small · up to 50 m²', 100_000),
    (Decimal('100'), 'Medium · over 50 m² and up to 100 m²', 200_000),
    (Decimal('200'), 'XL · over 100 m² and up to 200 m²', 300_000),
    (Decimal('300'), '2XL · over 200 m² and up to 300 m²', 400_000),
    (Decimal('400'), '3XL · over 300 m² and up to 400 m²', 500_000),
    (Decimal('500'), '4XL · over 400 m² and up to 500 m²', 600_000),
    (Decimal('600'), '5XL · over 500 m² and up to 600 m²', 700_000),
)

# Retained separately for business verification; it is not an approved price table.
UNVERIFIED_RESIDENTIAL_SCHEDULE = {
    'self-contain': 50_000,
    '1-bedroom': 70_000,
    '2-bedroom': 90_000,
    '3-bedroom': 110_000,
    '4-bedroom': 130_000,
    '5-bedroom': 150_000,
    '6-bedroom': 160_000,
    '7-bedroom': 170_000,
}

COUCH_UNIT_PRICES = {f'{size}-seater': size * 8_000 for size in range(1, 8)}
BEDFRAME_UNIT_PRICES = {
    '3x6-ft': 16_000,
    '4x6-ft': 20_000,
    '5x6-ft': 24_000,
    '6x6-ft': 28_000,
    '6x7-ft': 32_000,
    '7x7-ft': 36_000,
}
APPLIANCE_UNIT_PRICE = 10_000
APPROVED_APPLIANCE_TYPES = frozenset({'oven', 'refrigerator', 'microwave', 'dishwasher', 'other-approved'})
SEPARATE_LOGISTICS_FEE = 20_000


def _quantity_map(value, allowed, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} quantities must be an object.')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f'Unsupported {label.lower()} selection: {", ".join(sorted(unknown))}.')
    quantities = {}
    for item, raw_quantity in value.items():
        if isinstance(raw_quantity, bool):
            raise ValueError(f'{label} quantities must be whole numbers from 0 to 500.')
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            raise ValueError(f'{label} quantities must be whole numbers from 0 to 500.') from None
        if str(raw_quantity).strip() != str(quantity) or not 0 <= quantity <= 500:
            raise ValueError(f'{label} quantities must be whole numbers from 0 to 500.')
        if quantity:
            quantities[item] = quantity
    return quantities


def _line(label, quantity, unit_price):
    return {
        'label': label,
        'quantity': quantity,
        'unit_rate': unit_price,
        'amount': quantity * unit_price,
    }


def calculate_morph_cleaning_quote(selection):
    """Calculate an NGN cleaning preview from structured selections only."""
    if not isinstance(selection, dict):
        raise ValueError('Cleaning selections must be an object.')

    category = selection.get('category')
    lines = []
    review_reason = None

    if category == 'residential-move-clean':
        property_type = selection.get('property_type')
        if property_type not in RESIDENTIAL_MOVE_CLEAN:
            return {
                'total': None, 'lines': [], 'status': 'custom_quote',
                'reason': 'This property type is not listed; request a custom quote.',
            }
        price = RESIDENTIAL_MOVE_CLEAN[property_type]
        lines.append(_line(f'Residential Move-Clean · {property_type}', 1, price))
    elif category == 'commercial-move-clean':
        try:
            area = Decimal(str(selection.get('area_sqm', '')))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('Enter a valid commercial floor area in square metres.') from None
        if not area.is_finite() or area <= 0:
            raise ValueError('Commercial floor area must be greater than zero.')
        bracket = next((row for row in COMMERCIAL_MOVE_CLEAN if area <= row[0]), None)
        if bracket is None:
            return {
                'total': None, 'lines': [], 'status': 'custom_quote',
                'reason': 'Commercial spaces over 600 m² require a custom quote.',
            }
        _, label, price = bracket
        lines.append(_line(f'Commercial Move-Clean · {label}', 1, price))
    elif category == 'fumigation-pest-control':
        return {
            'total': None, 'lines': [], 'status': 'business_verification',
            'reason': 'The supplied residential schedule has not been confirmed for fumigation and pest control.',
        }
    elif category == 'upholstery-care':
        couches = _quantity_map(selection.get('couch_quantities'), COUCH_UNIT_PRICES, 'Couch')
        beds = _quantity_map(selection.get('bedframe_quantities'), BEDFRAME_UNIT_PRICES, 'Bedframe')
        for size, quantity in couches.items():
            lines.append(_line(f'Couch cleaning · {size}', quantity, COUCH_UNIT_PRICES[size]))
        for size, quantity in beds.items():
            lines.append(_line(f'Bedframe cleaning · {size}', quantity, BEDFRAME_UNIT_PRICES[size]))
        if not lines:
            raise ValueError('Select at least one couch or bedframe item.')
    elif category == 'kitchen-appliance-cleaning':
        appliances = _quantity_map(
            selection.get('appliance_quantities'), APPROVED_APPLIANCE_TYPES, 'Appliance',
        )
        if not appliances:
            raise ValueError('Select at least one kitchen appliance.')
        for appliance, quantity in appliances.items():
            lines.append(_line(f'Appliance cleaning · {appliance}', quantity, APPLIANCE_UNIT_PRICE))
    else:
        raise ValueError('Choose a supported Morph Cleaning service category.')

    if category in {'residential-move-clean', 'commercial-move-clean'}:
        logistics_requested = selection.get('logistics_requested', False)
        logistics_separate = selection.get('logistics_separate', False)
        if not isinstance(logistics_requested, bool) or not isinstance(logistics_separate, bool):
            raise ValueError('Logistics selections must be explicit true or false values.')
        if logistics_requested and logistics_separate:
            lines.append(_line('Separate cleaning logistics', 1, SEPARATE_LOGISTICS_FEE))

    total = sum(line['amount'] for line in lines)
    return {
        'total': total,
        'lines': lines,
        'status': 'indicative',
        'currency': CURRENCY,
        'rate_card_version': RATE_CARD_VERSION,
        'review_reason': review_reason,
    }