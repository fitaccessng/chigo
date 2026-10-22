"""Create privacy-conscious, read-only location readiness evidence.

This tool never creates or alters a database. SQLite is opened with mode=ro.
It writes reports only to the explicitly supplied output directory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moving_company.services.location_service import FCT_BOUNDS, normalize_location_name
from moving_company.models import LocalPlace

COUNCILS = {
    'abuja municipal area council': 'Abuja Municipal Area Council',
    'abuja municipal': 'Abuja Municipal Area Council',
    'municipal area council': 'Abuja Municipal Area Council',
    'amac': 'Abuja Municipal Area Council',
    'bwari area council': 'Bwari Area Council',
    'bwari': 'Bwari Area Council',
    'gwagwalada area council': 'Gwagwalada Area Council',
    'gwagwalada': 'Gwagwalada Area Council',
    'kuje area council': 'Kuje Area Council',
    'kuje': 'Kuje Area Council',
    'kwali area council': 'Kwali Area Council',
    'kwali': 'Kwali Area Council',
    'abaji area council': 'Abaji Area Council',
    'abaji': 'Abaji Area Council',
}
COUNCIL_ALIASES = {
    key: {'canonical': value, 'normalization_version': 'fct-councils-v1'}
    for key, value in sorted(COUNCILS.items())
}
SOURCES = {
    'osm': {
        'path': 'data/geodata/nigeria-latest.osm.pbf',
        'edition': 'Geofabrik Nigeria extract; embedded extract date not verified',
        'license': 'ODbL-1.0',
        'attribution': 'Credit OpenStreetMap contributors and provide ODbL notice/link; review database extraction obligations.',
        'coverage': 'Nigeria extract available; FCT-specific coverage not measured in this run.',
    },
    'geonames': {
        'path': 'data/geodata/NG.zip',
        'edition': 'GeoNames Nigeria dump; embedded edition date not verified',
        'license': 'CC-BY-4.0 per current project importer metadata; confirm against source terms before reuse.',
        'attribution': 'Credit GeoNames and retain source ID/reference.',
        'coverage': 'Nigeria gazetteer available; FCT candidates are documented in prior dry-run evidence, not re-imported here.',
    },
    'geoboundaries_adm1': {
        'path': 'data/geodata/geoboundaries-NGA-ADM1.geojson',
        'edition': 'File metadata must be reviewed for release/version.',
        'license': 'Not conclusively verified in this run; inspect dataset metadata/license before reuse.',
        'attribution': 'Retain geoBoundaries dataset/version/source URL and required attribution.',
        'coverage': 'Nigeria ADM1 geometries; FCT membership is used only if feature labels resolve unambiguously.',
    },
    'geoboundaries_adm2': {
        'path': 'data/geodata/geoboundaries-NGA-ADM2.geojson',
        'edition': 'File metadata must be reviewed for release/version.',
        'license': 'Not conclusively verified in this run; inspect dataset metadata/license before reuse.',
        'attribution': 'Retain geoBoundaries dataset/version/source URL and required attribution.',
        'coverage': 'Nigeria ADM2 geometries; six council boundaries must be independently verified.',
    },
    'fct_directory_report': {
        'path': 'data/geodata/reports/fct-location-import-latest.json',
        'edition': 'Report timestamp is read from the JSON metadata.',
        'license': 'Mixed upstream sources; verify each underlying source license and attribution.',
        'attribution': 'Keep the report source URLs and per-record provenance.',
        'coverage': 'Report only; counts do not prove corresponding database rows.',
    },
}
PUBLIC_SOURCES = {
    'chigo-curated', 'directory-parent-reference', 'wikipedia-fct-electoral-ward',
    'wikipedia-fct-postal', 'zipcode.com.ng',
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(folder: Path, filename: str, payload: object) -> None:
    (folder / filename).write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f'file:{path.resolve().as_posix()}?mode=ro'
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def migration_graph() -> dict:
    revisions = {}
    edges = []
    errors = []
    for path in sorted((ROOT / 'migrations' / 'versions').glob('*.py')):
        try:
            module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            values = {}
            for node in module.body:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {'revision', 'down_revision'}:
                        values[target.id] = ast.literal_eval(node.value)
            revision = values.get('revision')
            parent = values.get('down_revision')
            if not revision:
                errors.append(f'{path.name}: missing revision')
                continue
            if revision in revisions:
                errors.append(f'duplicate revision identifier: {revision}')
            revisions[revision] = path.name
            edges.append((revision, parent, path.name))
        except (SyntaxError, ValueError, TypeError) as error:
            errors.append(f'{path.name}: {type(error).__name__}')
    parent_ids = {parent for _, parent, _ in edges if isinstance(parent, str)}
    heads = sorted(set(revisions) - parent_ids)
    missing_parents = sorted({parent for _, parent, _ in edges if isinstance(parent, str) and parent not in revisions})
    roots = sorted(revision for revision, parent, _ in edges if parent is None)
    return {
        'revision_count': len(revisions),
        'head_revisions': heads,
        'root_revisions': roots,
        'missing_parent_revisions': missing_parents,
        'parse_or_duplicate_errors': errors,
        'linear_single_head': len(heads) == 1 and len(roots) == 1 and not missing_parents and not errors,
        'revisions': [{'revision': revision, 'parent': parent, 'file': filename} for revision, parent, filename in edges],
    }


def load_boundaries() -> tuple[object | None, dict[str, object], dict]:
    from shapely.geometry import shape
    from shapely.ops import unary_union

    adm1_path = ROOT / SOURCES['geoboundaries_adm1']['path']
    adm2_path = ROOT / SOURCES['geoboundaries_adm2']['path']
    summary = {'adm1_fct_feature_matches': 0, 'adm2_council_features': {}, 'boundary_error': None}
    fct_parts = []
    if not (adm1_path.is_file() and adm2_path.is_file()):
        summary['boundary_error'] = 'ADM1/ADM2 geometry file missing'
        return None, {}, summary
    try:
        adm1 = json.loads(adm1_path.read_text(encoding='utf-8'))
        for feature in adm1.get('features', []):
            properties = feature.get('properties') or {}
            normalized_values = {normalize_location_name(value) for value in properties.values() if isinstance(value, str)}
            if normalized_values & {'federal capital territory', 'abuja federal capital territory', 'fct'}:
                fct_parts.append(shape(feature['geometry']))
        summary['adm1_fct_feature_matches'] = len(fct_parts)
        fct_geometry = unary_union(fct_parts) if fct_parts else None
        adm2 = json.loads(adm2_path.read_text(encoding='utf-8'))
        council_parts = defaultdict(list)
        for feature in adm2.get('features', []):
            properties = feature.get('properties') or {}
            canonical = None
            for value in properties.values():
                if isinstance(value, str):
                    canonical = COUNCILS.get(normalize_location_name(value))
                    if canonical:
                        break
            geometry = shape(feature['geometry'])
            if canonical and (fct_geometry is None or geometry.intersects(fct_geometry)):
                council_parts[canonical].append(geometry)
        councils = {name: unary_union(parts) for name, parts in council_parts.items()}
        summary['adm2_council_features'] = {name: len(parts) for name, parts in council_parts.items()}
        summary['six_councils_found'] = len(councils) == 6
        return fct_geometry, councils, summary
    except (OSError, ValueError, KeyError, TypeError) as error:
        summary['boundary_error'] = type(error).__name__
        return None, {}, summary


def run_audit(database: Path, backup: Path | None, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).isoformat()
    graph = migration_graph()
    model_columns = {column.name for column in LocalPlace.__table__.columns}
    write_json(output, 'migration-graph.json', graph)
    write_json(output, 'area-council-normalization.json', COUNCIL_ALIASES)

    source_entries = []
    import_report = None
    for source, item in SOURCES.items():
        path = ROOT / item['path']
        entry = {
            'source': source,
            **item,
            'present': path.is_file(),
            'size_bytes': path.stat().st_size if path.is_file() else None,
            'sha256': sha256_file(path) if path.is_file() else None,
            'retrieval_date': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.is_file() else None,
        }
        if source == 'fct_directory_report' and path.is_file():
            try:
                import_report = json.loads(path.read_text(encoding='utf-8'))
                entry['source_report_generated_at'] = import_report.get('generated_at')
                entry['reported_reconciled_records'] = import_report.get('records_after_reconciliation')
                entry['reported_verified_coordinates'] = import_report.get('records_with_verified_coordinates')
                entry['reported_postcodes'] = import_report.get('records_with_postcodes')
                entry['reported_duplicate_candidates'] = len(import_report.get('duplicate_candidates') or [])
            except (OSError, ValueError):
                entry['source_report_parse_error'] = True
        source_entries.append(entry)
    source_entries.extend([
        {'source': 'whos-on-first', 'present': False, 'license': 'Per-record sources and terms must be reviewed; WOF requires license link and source-specific attribution.', 'coverage': 'No local complete bundle was identified.'},
        {'source': 'openaddresses', 'present': False, 'license': 'Must be assessed per source listing.', 'coverage': 'No local source package was identified.'},
    ])
    write_json(output, 'source-register.json', {'generated_at': now, 'sources': source_entries})

    fct_geometry, council_geometries, boundary_summary = load_boundaries()
    quality = Counter()
    council_counts = Counter()
    type_counts = Counter()
    source_type_counts = Counter()
    coordinate_dispositions = Counter()
    duplicate_groups = defaultdict(list)
    all_source_ids = Counter()
    foreign_keys = []
    with read_only_connection(database) as connection:
        table_names = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'local_places' not in table_names:
            raise RuntimeError('Configured local SQLite database has no local_places table')
        revision = connection.execute('SELECT version_num FROM alembic_version').fetchone()['version_num'] if 'alembic_version' in table_names else None
        info = {row['name']: row for row in connection.execute('PRAGMA table_info(local_places)')}
        columns = set(info)
        indexes = [dict(row) for row in connection.execute('PRAGMA index_list(local_places)')]
        foreign_keys = [dict(row) for row in connection.execute('PRAGMA foreign_key_list(local_places)')]
        records = connection.execute('SELECT * FROM local_places').fetchall()
        for row in records:
            record = dict(row)
            active = bool(record.get('active'))
            if not active:
                quality['inactive_rows'] += 1
                continue
            quality['active_rows'] += 1
            source = str(record.get('source_name') or '[unknown]')
            place_type = str(record.get('place_type') or '[unknown]')
            source_type_counts[(source, place_type)] += 1
            type_counts[place_type] += 1
            council = COUNCILS.get(normalize_location_name(record.get('area_council') or ''))
            if council:
                council_counts[council] += 1
            else:
                quality['noncanonical_or_missing_council'] += 1
            latitude, longitude = record.get('latitude'), record.get('longitude')
            has_coordinates = latitude is not None and longitude is not None
            if not has_coordinates:
                quality['missing_coordinates'] += 1
                disposition = 'must_remain_unknown_unless_trusted_source_match'
            else:
                try:
                    latitude, longitude = float(latitude), float(longitude)
                except (ValueError, TypeError):
                    quality['invalid_coordinates'] += 1
                    disposition = 'manual_review'
                else:
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
                        quality['invalid_coordinates'] += 1
                        disposition = 'manual_review'
                    elif not (FCT_BOUNDS['min_latitude'] <= latitude <= FCT_BOUNDS['max_latitude'] and FCT_BOUNDS['min_longitude'] <= longitude <= FCT_BOUNDS['max_longitude']):
                        quality['outside_rough_fct_bounds'] += 1
                        disposition = 'manual_review'
                    elif fct_geometry is None:
                        quality['boundary_membership_unverified'] += 1
                        disposition = 'manual_review_boundary_unavailable'
                    else:
                        from shapely.geometry import Point
                        point = Point(longitude, latitude)
                        if not fct_geometry.covers(point):
                            quality['outside_fct_polygon'] += 1
                            disposition = 'manual_review_outside_polygon'
                        else:
                            quality['inside_fct_polygon'] += 1
                            assigned = [name for name, geometry in council_geometries.items() if geometry.covers(point)]
                            if len(assigned) == 1:
                                canonical_record_council = COUNCILS.get(normalize_location_name(record.get('area_council') or ''))
                                if canonical_record_council == assigned[0]:
                                    quality['coordinate_council_matches_polygon'] += 1
                                    disposition = 'trusted_coordinate_boundary_match_requires_source_review'
                                else:
                                    quality['coordinate_council_mismatch'] += 1
                                    disposition = 'manual_review_council_mismatch'
                            elif not assigned:
                                quality['coordinate_no_council_polygon'] += 1
                                disposition = 'manual_review_no_council_polygon'
                            else:
                                quality['coordinate_multiple_council_polygons'] += 1
                                disposition = 'manual_review_ambiguous_council_boundary'
            verification = str(record.get('verification_status') or 'unknown')
            verified_flag = bool(record.get('verified'))
            if verified_flag or verification == 'verified':
                quality['marked_verified'] += 1
            elif verification in {'needs_review', 'unverified'}:
                quality['unverified_or_review'] += 1
            coordinate_dispositions[disposition] += 1
            normalized = str(record.get('normalized_name') or '')
            if normalized:
                duplicate_groups[(normalized, council or '[noncanonical]', place_type)].append(record)
            source_identifier = record.get('source_identifier')
            if source_identifier:
                all_source_ids[str(source_identifier)] += 1

    backup_info = None
    if backup is not None:
        with read_only_connection(backup) as backup_connection:
            backup_tables = {row['name'] for row in backup_connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            backup_revision = backup_connection.execute('SELECT version_num FROM alembic_version').fetchone()['version_num'] if 'alembic_version' in backup_tables else None
            backup_count = backup_connection.execute('SELECT COUNT(*) FROM local_places').fetchone()[0] if 'local_places' in backup_tables else None
            backup_active = backup_connection.execute('SELECT COUNT(*) FROM local_places WHERE active IS 1').fetchone()[0] if 'local_places' in backup_tables else None
            quick_check = [row[0] for row in backup_connection.execute('PRAGMA quick_check')]
        backup_info = {
            'path': str(backup.resolve()),
            'sha256': sha256_file(backup),
            'size_bytes': backup.stat().st_size,
            'sqlite_quick_check': quick_check,
            'migration_revision': backup_revision,
            'local_places_total': backup_count,
            'local_places_active': backup_active,
            'matches_live_local_db_revision': backup_revision == revision,
            'matches_live_local_db_location_count': backup_count == len(records),
            'recovery_verified': False,
            'reason_recovery_unverified': 'No isolated restore was performed; file integrity is not proof of recoverability.',
        }
    # Counts are aggregate-only; record names, raw queries and addresses are not emitted.
    duplicates = []
    for (normalized, council, place_type), group in duplicate_groups.items():
        if len(group) < 2:
            continue
        sources = Counter(str(item.get('source_name') or '[unknown]') for item in group)
        public_only = all(str(item.get('source_name') or '') in PUBLIC_SOURCES for item in group)
        group_id = hashlib.sha256(f'{normalized}|{council}|{place_type}'.encode()).hexdigest()[:20]
        duplicates.append({
            'group_id': group_id,
            'normalized_name_sha256': hashlib.sha256(normalized.encode()).hexdigest(),
            'canonical_area_council': council,
            'feature_type': place_type,
            'candidate_count': len(group),
            'source_counts': dict(sorted(sources.items())),
            'coordinates_present_count': sum(item.get('latitude') is not None and item.get('longitude') is not None for item in group),
            'source_identifiers_present_count': sum(bool(item.get('source_identifier')) for item in group),
            'records': [{
                'local_place_id': item.get('id'),
                'record_name_sha256': hashlib.sha256(str(item.get('name') or '').encode()).hexdigest(),
                'original_area_council': item.get('area_council'),
                'source_name': item.get('source_name'),
                'source_identifier_sha256': hashlib.sha256(str(item.get('source_identifier') or '').encode()).hexdigest(),
                'verification_status': item.get('verification_status'),
                'verified': bool(item.get('verified')),
                'coordinates_present': item.get('latitude') is not None and item.get('longitude') is not None,
                'postal_code_present': bool(item.get('postal_code')),
                'aliases_present': bool(item.get('aliases')),
            } for item in sorted(group, key=lambda candidate: candidate.get('id') or 0)],
            'confidence': 'low_without_record-level review',
            'recommended_action': 'manual_review_preserve_all_records_and_provenance',
            'human_review_required': True,
            'public_source_records_only': public_only,
        })
    duplicates.sort(key=lambda item: (item['canonical_area_council'], item['feature_type'], item['group_id']))
    write_json(output, 'duplicate-reconciliation-plan.json', {
        'generated_at': now,
        'method': 'exact normalized_name + canonicalized council + exact feature type; values hashed/redacted; no automatic merge',
        'privacy': 'No place names, coordinates, postal codes, aliases, raw queries, or customer address fields are emitted. Use local_place_id for authorized local review.',
        'group_count': len(duplicates),
        'excess_rows': sum(item['candidate_count'] - 1 for item in duplicates),
        'groups': duplicates,
    })
    write_json(output, 'coordinate-remediation.json', {
        'generated_at': now,
        'method': 'numeric validation plus local GeoBoundaries FCT/council polygon covers checks; rough bounding box is only a preliminary rejection filter',
        'boundary_evidence': boundary_summary,
        'categories': dict(sorted(coordinate_dispositions.items())),
        'records_to_keep_unknown': coordinate_dispositions.get('must_remain_unknown_unless_trusted_source_match', 0),
        'no_coordinate_updates_performed': True,
    })
    write_json(output, 'data-quality-report.json', {
        'generated_at': now,
        'database_backend': 'sqlite',
        'database_path': str(database.resolve()),
        'migration_revision': revision,
        'tables': sorted(table_names),
        'local_places_total': len(records),
        'active_location_counts_by_type': dict(sorted(type_counts.items())),
        'active_location_counts_by_canonical_area_council': dict(sorted(council_counts.items())),
        'active_source_type_counts': [{'source': key[0], 'feature_type': key[1], 'count': value} for key, value in sorted(source_type_counts.items())],
        'quality_counts': dict(sorted(quality.items())),
        'indexes': indexes,
        'columns': [{'name': column, 'type': info[column]['type'], 'notnull': info[column]['notnull'], 'default': info[column]['dflt_value'], 'pk': info[column]['pk']} for column in sorted(columns)],
        'foreign_keys': foreign_keys,
        'source_identifier_duplicate_values': sum(value > 1 for value in all_source_ids.values()),
        'no_database_writes_performed': True,
    })
    write_json(output, 'rejected-record-summary.json', {
        'generated_at': now,
        'database_rejected_records': 0,
        'reason': 'No source rows were ingested or rejected in this read-only audit; source report parser conflicts are not treated as database rejects.',
        'source_report_conflicts_and_unresolved_count': len((import_report or {}).get('conflicts_and_unresolved') or []),
        'no_database_writes_performed': True,
    })
    write_json(output, 'schema-reconciliation-matrix.json', {
        'generated_at': now,
        'target_identity': 'configured local development SQLite only; staging unknown',
        'rows': [
            {
                'object': 'alembic_version.version_num',
                'actual': revision,
                'expected': graph['head_revisions'],
                'difference': 'local revision trails the sole repository head by 20 application revisions',
                'risk': 'migration state unknown for staging; do not stamp or upgrade based on local state',
                'recommended_action': 'read-only inventory and clone/backup rehearsal on positively identified staging',
            },
            {
                'object': 'local_places columns',
                'actual': sorted(columns),
                'expected_sqlalchemy_model_columns': sorted(model_columns),
                'columns_missing_from_actual_vs_model': sorted(model_columns - columns),
                'columns_present_in_actual_but_not_model': sorted(columns - model_columns),
                'expected_migration_state': 'revision effects through 20261006_upgrade_booking_location_schema only',
                'difference': 'compare explicit current model columns above; current table also includes later provenance/postal/verification fields beyond the recorded revision',
                'risk': 'likely out-of-band application startup schema changes; exact historical cause not proven',
                'recommended_action': 'compare against model and each revision on staging; do not stamp head',
            },
            {
                'object': 'local_places indexes',
                'actual': sorted(index['name'] for index in indexes),
                'expected': 'historical migrations and current SQLAlchemy declarations',
                'difference': 'postal and source identifier indexes exist despite older revision marker',
                'risk': 'schema/revision drift; unique source identifier may reject duplicates on attempted import',
                'recommended_action': 'inspect definitions and data on staging clone before migration/import plan',
            },
            {
                'object': 'canonical PostGIS tables',
                'actual': sorted(name for name in table_names if name in {'location_records', 'location_aliases', 'location_observations', 'administrative_areas', 'location_sources', 'location_provenance'}),
                'expected': ['administrative_areas', 'location_aliases', 'location_observations', 'location_provenance', 'location_records', 'location_sources'],
                'difference': 'not present in local SQLite; PostgreSQL target not identified',
                'risk': 'cannot establish whether PostGIS migrations are applied in staging',
                'recommended_action': 'verify on positively identified Postgres staging clone only',
            },
        ],
        'cause_assessment': {
            'likely_local_cause': 'create_app AUTO_CREATE_SCHEMA path invokes upgrade_booking_schema, which directly adds columns/indexes and performs a backfill without updating Alembic version state',
            'proof_status': 'supported by current code path, not proven for every object or historical execution',
            'other_causes_not_excluded': ['manual DDL', 'restored database', 'different application version', 'partial migration'],
        },
        'mutation_performed': False,
    })
    write_json(output, 'proposed-import-manifest.json', {
        'generated_at': now,
        'status': 'proposal_only_not_executable',
        'sources': [entry for entry in source_entries if entry.get('present')],
        'reported_directory_counts_are_not_target_database_counts': True,
        'database_mutation_permitted': False,
        'required_preconditions': ['positive staging identity', 'isolated restore test', 'reviewed migration reconciliation', 'approved source licenses', 'approved import plan'],
    })

    report = {
        'generated_at': now,
        'database': {'backend': 'sqlite', 'path': str(database.resolve()), 'backup_snapshot': backup_info},
        'migration_graph': graph,
        'environment_gate': {'staging_identified': False, 'backup_recoverability_verified': False, 'database_mutations_performed': False},
        'source_count': len(source_entries),
        'duplicate_groups': len(duplicates),
        'coordinate_counts': dict(sorted(coordinate_dispositions.items())),
    }
    write_json(output, 'audit-summary.json', report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=str(ROOT / 'moving_company.db'), help='SQLite database path; opened read-only.')
    parser.add_argument('--backup', default=str(ROOT / 'data/geodata/fct-directory-cache/backups/moving_company-20261020T143945Z.sqlite3'), help='Optional local SQLite snapshot path for read-only metadata comparison.')
    parser.add_argument('--output-dir', required=True, help='New, non-existing evidence directory. Existing directory is never overwritten.')
    args = parser.parse_args()
    database = Path(args.database)
    backup = Path(args.backup) if args.backup else None
    output = Path(args.output_dir)
    if not database.is_file():
        parser.error('database file does not exist')
    if backup is not None and not backup.is_file():
        backup = None
    try:
        run_audit(database, backup, output)
    except FileExistsError:
        parser.error('output directory already exists; choose a new timestamped directory')
    print(f'Evidence written to {output.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
