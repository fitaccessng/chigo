"""Read-only diagnostics for Chigo's location search path."""

import argparse
from collections import Counter
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from moving_company.config import Config
from moving_company.extensions import db
from moving_company.routes.locations import locations_bp
from moving_company.services import location_service


REQUIRED_COLUMNS = {
    'local_places': {
        'id', 'name', 'normalized_name', 'area_council', 'latitude', 'longitude',
        'active', 'provider', 'source_name', 'source_identifier',
    },
    'location_records': {
        'id', 'name', 'normalized_name', 'place_type', 'area_council', 'latitude',
        'longitude', 'geom', 'source', 'source_id', 'active', 'postal_code',
    },
    'location_aliases': {'location_id', 'alias_name', 'normalized_alias'},
    'location_observations': {'normalized_input', 'raw_input', 'latitude', 'longitude', 'status'},
    'administrative_areas': {'name', 'area_council', 'geom'},
}
REQUIRED_INDEXES = {
    'local_places': {'ix_local_places_active_normalized_name', 'ix_local_places_postal_code'},
    'location_records': {'idx_location_records_geom', 'ix_location_records_name_trgm'},
    'location_aliases': {'ix_location_aliases_name_trgm'},
}
FAILURE_COUNT = 0


def report(state, label, detail=''):
    global FAILURE_COUNT
    if state == 'FAIL':
        FAILURE_COUNT += 1
    suffix = f': {detail}' if detail else ''
    print(f'{state}: {label}{suffix}')


def readonly_engine(database_url):
    url = make_url(database_url)
    if url.get_backend_name() == 'sqlite':
        database_path = Path(url.database).resolve()
        readonly_uri = f'file:{database_path.as_posix()}?mode=ro'
        return create_engine(
            'sqlite://',
            creator=lambda: sqlite3.connect(readonly_uri, uri=True),
        )
    connect_args = dict(Config.SQLALCHEMY_ENGINE_OPTIONS.get('connect_args', {}))
    connect_args['options'] = '-c default_transaction_read_only=on'
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def table_count(connection, table_name):
    table = Table(table_name, MetaData(), autoload_with=connection)
    return connection.execute(select(func.count()).select_from(table)).scalar_one()


def check_provider(base_url, path, label):
    if not base_url:
        report('WARN', f'{label} endpoint', 'not configured')
        return
    request = urllib.request.Request(
        f'{base_url.rstrip("/")}{path}',
        headers={'User-Agent': Config.OSM_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.5) as response:
            report('PASS' if 200 <= response.status < 300 else 'FAIL', f'{label} health', f'HTTP {response.status}')
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        report('FAIL', f'{label} health', type(error).__name__)


def percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.5)))
    return ordered[index]


def check_recent_errors(log_path):
    if not log_path:
        report('INFO', 'Recent location search errors', 'not available; pass --log-file or inspect runtime platform logs')
        return
    path = Path(log_path)
    if not path.is_file():
        report('WARN', 'Recent location search errors', 'configured log file was not found')
        return
    counts = {'database': 0, 'provider': 0}
    with path.open(encoding='utf-8', errors='replace') as log_file:
        for line in log_file:
            if 'Location autocomplete unavailable (kind=database)' in line:
                counts['database'] += 1
            elif 'Location autocomplete unavailable (kind=provider)' in line:
                counts['provider'] += 1
    report('PASS', 'Recent location search errors', f"database={counts['database']}, provider={counts['provider']}")


def run_endpoint_check(database_url, benchmark_count):
    app = Flask('chigo_location_diagnostics')
    app.config.from_object(Config)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['TESTING'] = True
    db.init_app(app)
    app.register_blueprint(locations_bp)

    durations = []
    local_durations = []
    final_status = None
    final_payload = {}
    with app.app_context():
        for _ in range(max(1, benchmark_count)):
            started = time.perf_counter()
            location_service._search_local_abuja_places('Gwarinpa', limit=8)
            local_durations.append((time.perf_counter() - started) * 1000)
    with app.test_client() as client:
        for _ in range(max(1, benchmark_count)):
            location_service.clear_location_search_cache()
            started = time.perf_counter()
            response = client.get('/api/locations/autocomplete?q=Gwarinpa&limit=8')
            durations.append((time.perf_counter() - started) * 1000)
            final_status = response.status_code
            final_payload = response.get_json(silent=True) or {}
            if final_status != 200:
                break
    if final_status == 200:
        results = final_payload.get('results') or []
        sources = Counter(str(result.get('provider') or result.get('source') or 'unknown') for result in results)
        source_summary = ', '.join(f'{source}={count}' for source, count in sorted(sources.items())) or 'no results'
        report('PASS', 'Autocomplete endpoint', f'HTTP 200, {len(results)} results for Gwarinpa; result sources: {source_summary}')
        if benchmark_count > 1:
            report('INFO', 'Autocomplete endpoint latency', f'p50={percentile(durations, 0.50):.1f} ms, p95={percentile(durations, 0.95):.1f} ms, p99={percentile(durations, 0.99):.1f} ms over {len(durations)} cache-cleared requests')
            report('INFO', 'Local matcher latency', f'p50={percentile(local_durations, 0.50):.1f} ms, p95={percentile(local_durations, 0.95):.1f} ms, p99={percentile(local_durations, 0.99):.1f} ms over {len(local_durations)} direct local searches')
        for label, query in (
            ('alternate spelling', 'Gwarimpa'),
            ('no-match probe', 'zz-chigo-location-no-match-92731'),
        ):
            location_service.clear_location_search_cache()
            example = client.get('/api/locations/autocomplete', query_string={'q': query, 'limit': 8})
            example_payload = example.get_json(silent=True) or {}
            report(
                'PASS' if example.status_code == 200 else 'FAIL',
                f'Autocomplete {label}',
                f"HTTP {example.status_code}, {len(example_payload.get('results') or [])} result(s)",
            )
    else:
        report('FAIL', 'Autocomplete endpoint', f'HTTP {final_status}; response body omitted')
    return final_status == 200


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-file', help='Optional application log file; only safe location-error counts are printed.')
    parser.add_argument('--benchmark', type=int, default=1, help='Number of cache-cleared endpoint requests (default: 1).')
    args = parser.parse_args()
    if args.benchmark < 1 or args.benchmark > 100:
        parser.error('--benchmark must be between 1 and 100')

    database_url = Config.SQLALCHEMY_DATABASE_URI
    if not os.getenv('DATABASE_URL'):
        if make_url(database_url).get_backend_name() == 'sqlite':
            report('INFO', 'Database configuration', 'DATABASE_URL not set; using configured SQLite development database')
        else:
            report('WARN', 'Database configuration', 'DATABASE_URL not set; an explicit backend configuration is active')
    report('PASS', 'Database URL configuration', 'configured (credentials hidden)')
    report('INFO', 'Application environment', str(Config.APP_ENV or 'unknown'))
    target_url = make_url(database_url)
    report('INFO', 'Database target', f'backend={target_url.get_backend_name()}, host={target_url.host or "local-file"}, database={target_url.database}, port={target_url.port or "default"}')

    engine = readonly_engine(database_url)
    endpoint_ready = False
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1')).scalar_one()
            report('PASS', 'Database connectivity')
            backend = engine.dialect.name
            if backend == 'postgresql':
                version = connection.execute(text('SELECT current_setting(\'server_version\')')).scalar_one()
                schema_name = connection.execute(text('SELECT current_schema()')).scalar_one()
                report('INFO', 'PostgreSQL target details', f'version={version}, schema={schema_name}')
                try:
                    postgis_version = connection.execute(text('SELECT PostGIS_Full_Version()')).scalar_one()
                    report('PASS', 'PostGIS availability', str(postgis_version).split()[0])
                    spatial_point = connection.execute(text(
                        'SELECT ST_AsText(ST_SetSRID(ST_MakePoint(7.3986, 9.0765), 4326))'
                    )).scalar_one()
                    report('PASS', 'PostGIS spatial function', spatial_point)
                except SQLAlchemyError:
                    report('FAIL', 'PostGIS availability', 'extension unavailable or query failed')
                extensions = set(connection.execute(text(
                    "SELECT extname FROM pg_extension WHERE extname IN ('postgis', 'pg_trgm')"
                )).scalars())
                report('PASS' if {'postgis', 'pg_trgm'} <= extensions else 'FAIL', 'Required PostgreSQL extensions', ', '.join(sorted(extensions)) or 'none')
            else:
                report('INFO', 'PostGIS availability', 'not applicable to SQLite')

            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            for table_name, required_columns in REQUIRED_COLUMNS.items():
                if table_name not in table_names:
                    state = 'FAIL' if (backend == 'postgresql' and table_name != 'local_places') or table_name == 'local_places' else 'WARN'
                    report(state, f'Required table {table_name}', 'missing')
                    continue
                actual_columns = {column['name'] for column in inspector.get_columns(table_name)}
                missing = required_columns - actual_columns
                report('FAIL' if missing else 'PASS', f'Required table {table_name}', f'missing columns: {", ".join(sorted(missing))}' if missing else 'required columns present')
                count = table_count(connection, table_name)
                report('PASS' if count else 'WARN', f'{table_name} record count', str(count))
                existing_indexes = {index['name'] for index in inspector.get_indexes(table_name)}
                expected_indexes = REQUIRED_INDEXES.get(table_name, set())
                relevant_indexes = expected_indexes if backend == 'postgresql' or table_name == 'local_places' else set()
                if relevant_indexes:
                    missing_indexes = relevant_indexes - existing_indexes
                    report('FAIL' if missing_indexes else 'PASS', f'{table_name} search indexes', ', '.join(sorted(missing_indexes)) if missing_indexes else 'expected indexes present')

            if 'local_places' in table_names:
                columns = {column['name'] for column in inspector.get_columns('local_places')}
                if {'latitude', 'longitude'} <= columns:
                    quality = connection.execute(text("""
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                         AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180
                                         AND NOT (latitude = 0 AND longitude = 0) THEN 1 ELSE 0 END) AS valid_coordinates,
                               SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                         AND NOT (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
                                         THEN 1 ELSE 0 END) AS invalid_coordinates,
                               SUM(CASE WHEN latitude IS NULL OR longitude IS NULL THEN 1 ELSE 0 END) AS missing_coordinates,
                               SUM(CASE WHEN NOT (latitude BETWEEN 8.2 AND 9.85
                                         AND longitude BETWEEN 6.6 AND 7.85) THEN 1 ELSE 0 END) AS outside_fct_bounds
                        FROM local_places WHERE active IS TRUE
                    """)).first()
                    report('INFO', 'Active local-place coordinate quality', f'valid={quality.valid_coordinates or 0}, missing={quality.missing_coordinates or 0}, invalid={quality.invalid_coordinates or 0}, outside_fct_bounds={quality.outside_fct_bounds or 0}, total={quality.total or 0}')
                if 'source_name' in columns:
                    source_count = connection.execute(text("""
                        SELECT COUNT(DISTINCT source_name) FROM local_places
                        WHERE source_name IS NOT NULL AND source_name <> ''
                    """)).scalar_one()
                    report('INFO', 'Local-place import provenance', f'{source_count} named source(s) represented')
                    type_column = 'place_type' if 'place_type' in columns else 'location_type' if 'location_type' in columns else None
                    if type_column:
                        source_distribution = connection.execute(text(f"""
                            SELECT COALESCE(NULLIF(TRIM(source_name), ''), '[unknown]') AS source,
                                   COALESCE(NULLIF(TRIM({type_column}), ''), '[unknown]') AS place_type,
                                   COUNT(*) AS records
                            FROM local_places GROUP BY source, place_type ORDER BY source, place_type
                        """)).all()
                        report('INFO', 'Local-place rows by source and type', ', '.join(
                            f'{row.source}/{row.place_type}={row.records}' for row in source_distribution
                        ) or 'none')
                if {'area_council', 'active'} <= columns:
                    distribution = connection.execute(text("""
                        SELECT COALESCE(NULLIF(TRIM(area_council), ''), '[missing]') AS council, COUNT(*) AS records
                        FROM local_places WHERE active IS TRUE GROUP BY council ORDER BY council
                    """)).all()
                    report('INFO', 'Active records by area council', ', '.join(f'{row.council}={row.records}' for row in distribution) or 'none')
                    missing_council = connection.execute(text("""
                        SELECT COUNT(*) FROM local_places
                        WHERE active IS TRUE AND (area_council IS NULL OR TRIM(area_council) = '')
                    """)).scalar_one()
                    report('INFO', 'Active records missing area council', str(missing_council))
                if {'normalized_name', 'active'} <= columns:
                    duplicate_groups = connection.execute(text("""
                        SELECT COUNT(*) FROM (
                            SELECT normalized_name FROM local_places
                            WHERE active IS TRUE GROUP BY normalized_name HAVING COUNT(*) > 1
                        ) AS duplicates
                    """)).scalar_one()
                    duplicate_rows = connection.execute(text("""
                        SELECT COALESCE(SUM(record_count - 1), 0) FROM (
                            SELECT COUNT(*) AS record_count FROM local_places
                            WHERE active IS TRUE GROUP BY normalized_name HAVING COUNT(*) > 1
                        ) AS duplicates
                    """)).scalar_one()
                    report('INFO', 'Duplicate normalized-name groups', f'groups={duplicate_groups}, excess_rows={duplicate_rows}')
            if 'location_records' in table_names:
                columns = {column['name'] for column in inspector.get_columns('location_records')}
                if {'latitude', 'longitude', 'active'} <= columns:
                    quality = connection.execute(text("""
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                         AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180
                                         AND NOT (latitude = 0 AND longitude = 0) THEN 1 ELSE 0 END) AS valid_coordinates,
                               SUM(CASE WHEN latitude IS NULL OR longitude IS NULL THEN 1 ELSE 0 END) AS missing_coordinates,
                               SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                         AND NOT (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
                                         THEN 1 ELSE 0 END) AS invalid_coordinates,
                               SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                         AND NOT (latitude BETWEEN 8.2 AND 9.85
                                         AND longitude BETWEEN 6.6 AND 7.85) THEN 1 ELSE 0 END) AS outside_fct_bounds
                        FROM location_records WHERE active IS TRUE
                    """)).first()
                    report('INFO', 'Active canonical coordinate quality', f'valid={quality.valid_coordinates or 0}, missing={quality.missing_coordinates or 0}, invalid={quality.invalid_coordinates or 0}, outside_fct_bounds={quality.outside_fct_bounds or 0}, total={quality.total or 0}')
                if {'area_council', 'active'} <= columns:
                    distribution = connection.execute(text("""
                        SELECT COALESCE(NULLIF(TRIM(area_council), ''), '[missing]') AS council, COUNT(*) AS records
                        FROM location_records WHERE active IS TRUE GROUP BY council ORDER BY council
                    """)).all()
                    report('INFO', 'Active canonical records by area council', ', '.join(f'{row.council}={row.records}' for row in distribution) or 'none')
                if {'normalized_name', 'active'} <= columns:
                    duplicate_groups = connection.execute(text("""
                        SELECT COUNT(*) FROM (
                            SELECT normalized_name FROM location_records
                            WHERE active IS TRUE GROUP BY normalized_name HAVING COUNT(*) > 1
                        ) AS duplicates
                    """)).scalar_one()
                    report('INFO', 'Canonical duplicate normalized-name groups', str(duplicate_groups))
                if backend == 'postgresql' and 'geom' in columns:
                    srids = connection.execute(text("""
                        SELECT COALESCE(ST_SRID(geom), 0) AS srid, COUNT(*) AS records
                        FROM location_records WHERE geom IS NOT NULL GROUP BY srid ORDER BY srid
                    """)).all()
                    report('INFO', 'Canonical geometry SRIDs', ', '.join(f'EPSG:{row.srid}={row.records}' for row in srids) or 'no geometry records')
            if 'alembic_version' in table_names:
                revisions = ', '.join(connection.execute(text('SELECT version_num FROM alembic_version')).scalars()) or 'none'
                report('INFO', 'Applied migration revision', revisions)
            endpoint_ready = True
    except SQLAlchemyError as error:
        report('FAIL', 'Database connectivity', type(error).__name__)
    finally:
        engine.dispose()

    import_report = PROJECT_ROOT / 'data' / 'geodata' / 'reports' / 'fct-location-import-latest.json'
    if import_report.is_file():
        try:
            data = json.loads(import_report.read_text(encoding='utf-8'))
            report('INFO', 'FCT directory import report', f"records={data.get('records_after_reconciliation', 'unknown')}, verified_coordinates={data.get('records_with_verified_coordinates', 'unknown')}")
        except (OSError, ValueError):
            report('WARN', 'FCT directory import report', 'unreadable')
    else:
        report('WARN', 'FCT directory import report', 'not found')

    check_provider(
        Config.PHOTON_BASE_URL,
        '/api/?q=' + urllib.parse.quote('Abuja') + '&limit=1',
        'Photon',
    )
    check_provider(
        Config.OSRM_BASE_URL,
        '/route/v1/driving/7.3986,9.0765;7.4951,9.0578?overview=false',
        'OSRM',
    )
    endpoint_ok = run_endpoint_check(database_url, args.benchmark) if endpoint_ready else False
    check_recent_errors(args.log_file)
    report('PASS' if endpoint_ok and FAILURE_COUNT == 0 else 'FAIL', 'Overall diagnostics', 'read-only checks complete')
    return 0 if endpoint_ok and FAILURE_COUNT == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())