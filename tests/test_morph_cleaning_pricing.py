import pytest

from moving_company.services.morph_cleaning_pricing import (
    APPLIANCE_UNIT_PRICE,
    BEDFRAME_UNIT_PRICES,
    COUCH_UNIT_PRICES,
    RATE_CARD_VERSION,
    RESIDENTIAL_MOVE_CLEAN,
    SEPARATE_LOGISTICS_FEE,
    UNVERIFIED_RESIDENTIAL_SCHEDULE,
    calculate_morph_cleaning_quote,
)


@pytest.mark.parametrize(('property_type', 'amount'), RESIDENTIAL_MOVE_CLEAN.items())
def test_residential_move_clean_uses_exact_listed_price(property_type, amount):
    quote = calculate_morph_cleaning_quote({
        'category': 'residential-move-clean', 'property_type': property_type,
    })

    assert quote['total'] == amount
    assert quote['rate_card_version'] == RATE_CARD_VERSION


@pytest.mark.parametrize(('area', 'amount'), [
    (50, 100_000), (50.01, 200_000), (100, 200_000),
    (100.01, 300_000), (200, 300_000), (200.01, 400_000),
    (300, 400_000), (300.01, 500_000), (400, 500_000),
    (400.01, 600_000), (500, 600_000), (500.01, 700_000), (600, 700_000),
])
def test_commercial_boundaries_use_non_overlapping_brackets(area, amount):
    quote = calculate_morph_cleaning_quote({
        'category': 'commercial-move-clean', 'area_sqm': area,
    })

    assert quote['total'] == amount


def test_unlisted_sizes_and_fumigation_schedule_require_review():
    large_space = calculate_morph_cleaning_quote({
        'category': 'commercial-move-clean', 'area_sqm': 600.01,
    })
    unknown_residence = calculate_morph_cleaning_quote({
        'category': 'residential-move-clean', 'property_type': '8-bedroom',
    })
    fumigation = calculate_morph_cleaning_quote({
        'category': 'fumigation-pest-control', 'property_type': '2-bedroom',
    })

    assert large_space['status'] == 'custom_quote'
    assert unknown_residence['status'] == 'custom_quote'
    assert fumigation['status'] == 'business_verification'
    assert fumigation['total'] is None
    assert UNVERIFIED_RESIDENTIAL_SCHEDULE['2-bedroom'] == 90_000


def test_upholstery_and_appliance_quantities_use_backend_rates():
    upholstery = calculate_morph_cleaning_quote({
        'category': 'upholstery-care',
        'couch_quantities': {'3-seater': '1', '1-seater': 2},
        'bedframe_quantities': {'6x7-ft': 1},
    })
    appliances = calculate_morph_cleaning_quote({
        'category': 'kitchen-appliance-cleaning',
        'appliance_quantities': {'oven': 1, 'microwave': 2},
    })

    assert upholstery['total'] == 24_000 + 16_000 + BEDFRAME_UNIT_PRICES['6x7-ft']
    assert appliances['total'] == 3 * APPLIANCE_UNIT_PRICE
    assert COUCH_UNIT_PRICES['3-seater'] == 24_000


@pytest.mark.parametrize('quantity', [-1, '1.5', 'abc', 501, True])
def test_invalid_item_quantities_are_rejected(quantity):
    with pytest.raises(ValueError, match='whole numbers'):
        calculate_morph_cleaning_quote({
            'category': 'upholstery-care', 'couch_quantities': {'1-seater': quantity},
        })


def test_logistics_fee_is_applied_once_only_when_separate():
    base = {'category': 'residential-move-clean', 'property_type': '2-bedroom'}
    separate = calculate_morph_cleaning_quote({
        **base, 'logistics_requested': True, 'logistics_separate': True,
    })
    included = calculate_morph_cleaning_quote({
        **base, 'logistics_requested': True, 'logistics_separate': False,
    })
    absent = calculate_morph_cleaning_quote(base)

    assert separate['total'] == 120_000 + SEPARATE_LOGISTICS_FEE
    assert sum(line['amount'] == SEPARATE_LOGISTICS_FEE for line in separate['lines']) == 1
    assert included['total'] == absent['total'] == 120_000