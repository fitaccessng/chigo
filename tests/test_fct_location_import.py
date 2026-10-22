import json

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import LocalPlace
from moving_company.services.location_service import clear_location_search_cache, search_places
from scripts.import_fct_locations import (
    SIX_COUNCILS,
    _import_sqlite,
    _eligible_link,
    _record,
    _reconcile,
    _ensure_source_parent_records,
    parse_postal_html,
    parse_wikipedia_wikitext,
)


def test_wikipedia_postal_and_ward_sections_parse_all_six_councils():
    source = '''
== By postal code ==
{| class="wikitable sortable"
! LGA !! District / Area !! Postal code !! Villages
|-
| rowspan="2" | [[Abaji]] || Abaji || 905101 || Agyana; Bago
|-
| Yaba || 905102 || Abuja; Adagba
|-
| Abuja Municipal Area Council (AMAC) || Karu || 900110 || [[Jikwoyi]]; Karu Village
|-
| Bwari || Bwari || 901101 || Kubwa; Ushafa
|-
| Gwagwalada || Gwagwalada || 902101 || Agota; Zuba
|-
| Kuje || Rubochi || 903102 || Rubochi; Zokutu
|-
| Kwali || Pai || 904101 || Pai; Bako
|}
== By electoral ward ==
{| class="wikitable sortable"
! LGA !! Ward !! Polling Unit Name
|-
| rowspan="1" | Abaji || Abaji Central || Agyana / Agyana Primary School; Nuku / Nuku Primary School
|-
| Abuja Municipal Area Council || Garki || Garki Village / Village Centre
|-
| Bwari || Bwari Central || Bwari I / Central Primary School
|-
| Gwagwalada || Zuba || Zuba / Clinic
|-
| Kuje || Kuje || Kuje I / Primary School
|-
| Kwali || Pai || Pai / Primary School
|}
== References ==
[1] Example directory.
'''

    records, review = parse_wikipedia_wikitext(source)

    assert {record['area_council'] for record in records} == set(SIX_COUNCILS)
    assert len(records) == 14
    abaji_yaba = next(record for record in records if record['name'] == 'Abuja' and record['area_council'] == 'Abaji Area Council')
    assert abaji_yaba['district'] == 'Yaba'
    assert abaji_yaba['postal_code'] == '905102'
    assert abaji_yaba['latitude'] is None and abaji_yaba['longitude'] is None
    assert len(review) == 7
    assert all(record['active'] is False and record['verification_status'] == 'needs_review' for record in review)


def test_postal_html_area_postcode_lga_table_is_not_misread_as_district():
    source = '''
    <html><head><title>Gwagwalada LGA Postal Code: FCT Abuja</title></head><body>
    <table><tr><th>Area</th><th>Postcode</th><th>LGA</th><th>State</th></tr>
    <tr><td>Agota</td><td>902101</td><td>Gwagwalada</td><td>FCT Abuja</td></tr>
    <tr><td>Akwayi</td><td>902101</td><td>Gwagwalada</td><td>FCT Abuja</td></tr></table>
    </body></html>
    '''

    records = parse_postal_html(source, 'https://www.zipcode.com.ng/2021/08/gwagwalada-lga-postal-code.html')

    assert [record['name'] for record in records] == ['Agota', 'Akwayi']
    assert all(record['area_council'] == 'Gwagwalada Area Council' for record in records)
    assert all(record['district'] is None for record in records)
    assert all(record['parent_name'] == 'Gwagwalada Area Council' for record in records)
    assert all(record['postal_code'] == '902101' for record in records)


def test_postal_crawler_accepts_fct_child_page_but_rejects_state_page():
    base = 'https://www.zipcode.com.ng/2021/08/abuja-municipal-amac-postal-code.html'
    assert _eligible_link(base, '/2022/05/karu-village-postal-code.html', 'Karu Village', allow_fct_child=True)
    assert _eligible_link(base, '/2021/08/plateau-state-postal-code.html', 'Plateau', allow_fct_child=True) is None
    assert _eligible_link(base, '/2021/08/plateau-postal-codes.html', 'Plateau postal codes', allow_fct_child=True) is None
    assert _eligible_link(base, '/zipcode.com.ng/2021/08/abuja-town-postal-code.html', 'Abuja', allow_fct_child=True) is None


def test_key_value_postal_page_parses_place_without_inventing_coordinates():
    source = '''
    <html><head><title>Gwarinpa Village Postal Code, AMAC: FCT Abuja</title></head><body>
    <table><tr><th>Name</th><th>Gwarinpa village</th></tr>
    <tr><td>Postal Code</td><td>900108</td></tr><tr><td>District</td><td>Gwarinpa</td></tr>
    <tr><td>LGA</td><td>Abuja Municipal Area Council</td></tr></table>
    </body></html>
    '''

    records = parse_postal_html(source, 'https://www.zipcode.com.ng/2022/05/gwarinpa-village-postal-code.html')

    assert len(records) == 1
    assert records[0]['name'] == 'Gwarinpa village'
    assert records[0]['area_council'] == 'Abuja Municipal Area Council'
    assert records[0]['district'] == 'Gwarinpa'
    assert records[0]['postal_code'] == '900108'
    assert records[0]['latitude'] is None and records[0]['longitude'] is None


def test_reconciliation_preserves_postcode_conflicts_and_source_aliases():
    first = _record('Example Village', 'Kuje', 'Rubochi', '903102', 'village', 'wiki', 'https://example.test/wiki', 'Example Village')
    second = _record('Example Village', 'Kuje Area Council', 'Rubochi', '903101', 'village', 'zip', 'https://example.test/zip', 'EXAMPLE VILLAGE')
    records, duplicates, conflicts = _reconcile([first, second])

    assert len(records) == 1
    assert records[0]['postal_code'] is None
    assert records[0]['postal_code_conflicts'] == ['903101', '903102']
    assert records[0]['verification_status'] == 'postcode_conflict'
    assert 'EXAMPLE VILLAGE' in records[0]['aliases']
    assert len(records[0]['provenance']) == 2
    assert len(duplicates) == 1
    assert conflicts[0]['kind'] == 'postcode_conflict'


def test_import_is_repeatable_and_does_not_assign_fake_coordinates(tmp_path):
    app = create_app(testing=True)
    source = _record('Example Hamlet', 'Kwali', 'Pai', '904101', 'village', 'zip', 'https://example.test/pai', 'Example Hamlet')
    source['source_identifier'] = 'fct-directory:village:stable-test-id'
    source['provenance'] = [{'source_name': 'zip', 'source_url': 'https://example.test/pai', 'raw_source_name': 'Example Hamlet'}]
    parent = _record('Kwali Area Council', 'Kwali Area Council', None, None, 'area_council', 'reference', 'https://example.test/root', 'Kwali Area Council')
    parent['source_identifier'] = 'fct-directory:area_council:stable-parent-id'
    parent['provenance'] = [{'source_name': 'reference', 'source_url': 'https://example.test/root', 'raw_source_name': 'Kwali Area Council'}]
    parent['verification_status'] = 'administrative_reference'
    district = _record('Pai', 'Kwali Area Council', 'Pai', None, 'district', 'reference', 'https://example.test/pai', 'Pai')
    district['source_identifier'] = 'fct-directory:district:stable-pai-id'
    district['provenance'] = [{'source_name': 'reference', 'source_url': 'https://example.test/pai', 'raw_source_name': 'Pai'}]
    district['verification_status'] = 'administrative_reference'
    records = [parent, district, source]

    with app.app_context():
        first = _import_sqlite(records, dry_run=False, backup_dir=tmp_path)
        count_after_first = LocalPlace.query.filter_by(source_identifier=source['source_identifier']).count()
        second = _import_sqlite(records, dry_run=False, backup_dir=tmp_path)
        imported = LocalPlace.query.filter_by(source_identifier=source['source_identifier']).one()
        imported_parent = LocalPlace.query.filter_by(source_identifier=district['source_identifier']).one()
        assert count_after_first == 1
        assert LocalPlace.query.filter_by(source_identifier=source['source_identifier']).count() == 1
        assert imported.latitude is None and imported.longitude is None
        assert imported.postal_code == '904101'
        assert imported.parent_id == imported_parent.id
        assert imported_parent.parent_id != imported_parent.id
        assert imported.source_metadata['provenance'][0]['source_url'] == 'https://example.test/pai'
        assert first['inserted'] == 2
        assert second['inserted'] == 0
        assert second['unchanged'] == 3
        assert second['writes'] == 0

    assert first['backup'] is None


def test_local_autocomplete_postcode_alias_ranking_context_and_limits():
    app = create_app(testing=True)
    clear_location_search_cache()
    with app.app_context():
        canonical = LocalPlace(
            name='Kubwa Village Directory Test', normalized_name='kubwa village directory test',
            place_type='village', area_council='Bwari Area Council', district='Kubwa',
            postal_code='901101', aliases='Old Kubwa Settlement', search_keywords='901101',
            latitude=None, longitude=None, active=True,
        )
        same_name_other_council = LocalPlace(
            name='Kubwa Village Directory Test', normalized_name='kubwa village directory test',
            place_type='village', area_council='Abuja Municipal Area Council', district='Karu',
            latitude=None, longitude=None, active=True,
        )
        db.session.add_all([canonical, same_name_other_council])
        db.session.commit()

        exact = search_places('Kubwa Village Directory Test', limit=8)
        alias = search_places('Old Kubwa Settlement', limit=8)
        postcode = search_places('901101', limit=8)
        partial = search_places('Kubwa Village Directory', limit=1)

        assert len([row for row in exact if row['name'] == canonical.name]) == 2
        assert exact[0]['area_council'] == 'Bwari Area Council'
        assert {row['area_council'] for row in exact} == {'Abuja Municipal Area Council', 'Bwari Area Council'}
        assert alias[0]['area_council'] == 'Bwari Area Council'
        assert postcode[0]['postal_code'] == '901101'
        assert len(partial) == 1
        assert partial[0]['name'] == canonical.name

    clear_location_search_cache()
