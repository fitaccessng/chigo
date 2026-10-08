import argparse
import http.client
import json
import logging
import os
import re
import socket
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from dotenv import load_dotenv
import osmium

from .fct_location_data import FCT_LOCATION_SEED
from .location_service import normalize_location_name

LOGGER = logging.getLogger('chigo.geodata')
load_dotenv(Path(__file__).resolve().parents[2] / '.env')
_TOKEN_RE = re.compile(r'[^a-z0-9]+')
FCT_NAME = 'Abuja Federal Capital Territory'
COUNCILS = {
    'abuja municipal': 'Abuja Municipal Area Council',
    'municipal area council': 'Abuja Municipal Area Council',
    'amac': 'Abuja Municipal Area Council',
    'abuja municipal area council': 'Abuja Municipal Area Council',
    'bwari': 'Bwari Area Council',
    'gwagwalada': 'Gwagwalada Area Council',
    'kuje': 'Kuje Area Council',
    'kwali': 'Kwali Area Council',
    'abaji': 'Abaji Area Council',
}
AREA_COUNCILS = tuple(dict.fromkeys(COUNCILS.values()))
DEFAULT_OSM_URL = 'https://download.geofabrik.de/africa/nigeria-latest.osm.pbf'
GEONAMES_URL = 'https://download.geonames.org/export/dump/NG.zip'
GEOBOUNDARIES_API = 'https://www.geoboundaries.org/api/current/gbOpen/NGA/{level}/'
OSM_TYPES = {
    'administrative': 'district', 'boundary': 'district', 'suburb': 'neighborhood',
    'neighbourhood': 'neighborhood', 'quarter': 'neighborhood', 'residential': 'residential_area',
    'estate': 'estate', 'village': 'village', 'town': 'town', 'hamlet': 'village',
    'road': 'road', 'street': 'street', 'highway': 'road', 'airport': 'airport',
    'hotel': 'hotel', 'school': 'school', 'university': 'university', 'college': 'university',
    'hospital': 'hospital', 'clinic': 'hospital', 'market': 'market', 'mall': 'shopping_centre',
    'shopping_centre': 'shopping_centre', 'place_of_worship': 'religious_site',
    'government': 'government_facility', 'bus_station': 'transport', 'station': 'transport',
    'building': 'building', 'commercial': 'commercial_area',
}


def normalize_location_payload(payload):
    if not isinstance(payload, dict):
        raise TypeError('location payload must be a dictionary')
    name = str(payload.get('name') or payload.get('display_name') or '').strip()
    if not name:
        raise ValueError('location payload is missing a name')
    latitude = float(payload.get('latitude') or payload.get('lat') or 0)
    longitude = float(payload.get('longitude') or payload.get('lon') or payload.get('lng') or 0)
    aliases = payload.get('aliases') or []
    keywords = payload.get('keywords') or payload.get('search_keywords') or []
    if isinstance(aliases, str):
        aliases = re.split(r'[,;|]', aliases)
    if isinstance(keywords, str):
        keywords = re.split(r'[,;|]', keywords)
    return {
        **payload,
        'name': name,
        'normalized_name': normalize_location_name(name),
        'place_type': str(payload.get('place_type') or payload.get('type') or 'poi').lower(),
        'area_council': payload.get('area_council') or payload.get('council'),
        'latitude': latitude,
        'longitude': longitude,
        'centroid': {'type': 'Point', 'coordinates': [longitude, latitude]},
        'aliases': list(dict.fromkeys(_TOKEN_RE.sub(' ', str(alias).casefold()).strip() for alias in aliases if str(alias).strip())),
        'keywords': list(dict.fromkeys(_TOKEN_RE.sub(' ', str(word).casefold()).strip() for word in keywords if str(word).strip())),
        'source': payload.get('source') or 'chigo',
        'source_id': str(payload.get('source_id') or payload.get('provider_place_id') or normalize_location_name(name)),
        'verification_status': payload.get('verification_status') or ('verified' if payload.get('verified', False) else 'imported'),
    }


def _http_download(url, destination, *, force=False, timeout=120, retries=4):
    path = Path(destination)
    if path.exists() and path.stat().st_size and not force:
        LOGGER.info('Using cached source file %s', path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    if force and temporary.exists():
        temporary.unlink()

    for attempt in range(retries + 1):
        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = {'User-Agent': 'ChigoGeodataIngest/1.0'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = urllib.request.Request(url, headers=headers)
        LOGGER.info('Downloading %s (%.1f MB cached)', url, offset / (1024 * 1024))
        expected_total = None
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, 'status', response.getcode())
                if offset and status != 206:
                    temporary.unlink(missing_ok=True)
                    offset = 0
                    request = urllib.request.Request(url, headers={'User-Agent': 'ChigoGeodataIngest/1.0'})
                    response.close()
                    with urllib.request.urlopen(request, timeout=timeout) as fresh_response, temporary.open('wb') as output:
                        while chunk := fresh_response.read(1024 * 1024):
                            output.write(chunk)
                else:
                    mode = 'ab' if offset else 'wb'
                    expected_total = response.headers.get('Content-Length')
                    expected_total = offset + int(expected_total) if expected_total is not None else None
                    with temporary.open(mode) as output:
                        while chunk := response.read(4 * 1024 * 1024):
                            output.write(chunk)
            if expected_total is not None and temporary.stat().st_size != expected_total:
                raise http.client.IncompleteRead(b'', expected_total - temporary.stat().st_size)
            temporary.replace(path)
            return path
        except (http.client.IncompleteRead, http.client.RemoteDisconnected, socket.timeout, TimeoutError, urllib.error.URLError) as error:
            if attempt >= retries:
                raise RuntimeError(
                    f'Download did not complete after {retries + 1} attempts; '
                    f'{temporary.stat().st_size if temporary.exists() else 0} bytes retained at {temporary}'
                ) from error
            LOGGER.warning('Download interrupted; retrying from byte %s (%s)',
                           temporary.stat().st_size if temporary.exists() else 0, error)
            time.sleep(min(2 ** attempt, 15))


def download_sources(data_dir, *, sources, force=False):
    root = Path(data_dir)
    downloaded = {}
    if 'osm' in sources:
        downloaded['osm'] = _http_download(
            os.getenv('GEOFABRIK_NIGERIA_URL', DEFAULT_OSM_URL), root / 'nigeria-latest.osm.pbf', force=force,
        )
    if 'geonames' in sources:
        downloaded['geonames'] = _http_download(GEONAMES_URL, root / 'NG.zip', force=force)
    for level in ('ADM1', 'ADM2'):
        if 'boundaries' not in sources:
            break
        metadata_path = root / f'geoboundaries-NGA-{level}.json'
        if metadata_path.exists() and not force:
            metadata = json.loads(metadata_path.read_text())
        else:
            metadata_url = GEOBOUNDARIES_API.format(level=level)
            request = urllib.request.Request(metadata_url, headers={'User-Agent': 'ChigoGeodataIngest/1.0'})
            with urllib.request.urlopen(request, timeout=45) as response:
                metadata = json.load(response)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(json.dumps(metadata))
        downloaded[f'boundaries_{level.lower()}'] = _http_download(
            metadata['gjDownloadURL'], root / f'geoboundaries-NGA-{level}.geojson', force=force,
        )
    return downloaded


def _load_fct_boundary(path):
    from shapely.geometry import shape

    data = json.loads(Path(path).read_text())
    for feature in data.get('features', []):
        props = feature.get('properties') or {}
        if any(normalize_location_name(value) in {
            'abuja federal capital territory', 'federal capital territory', 'fct',
        } for value in props.values()):
            return shape(feature['geometry']), props
    raise ValueError(f'FCT polygon not found in boundary source {path}')


def _feature_center(geometry):
    point = geometry.representative_point()
    return point.y, point.x


def _boundary_features(path, fct_geometry):
    data = json.loads(Path(path).read_text())
    for feature in data.get('features', []):
        geometry = feature.get('geometry')
        if not geometry:
            continue
        from shapely.geometry import shape
        polygon = shape(geometry)
        if not polygon.intersects(fct_geometry):
            continue
        properties = feature.get('properties') or {}
        name = properties.get('shapeName') or properties.get('name')
        if not name:
            continue
        yield name, properties, polygon.intersection(fct_geometry)


def _load_council_boundaries(path, fct_geometry):
    boundaries = {}
    for name, _properties, geometry in _boundary_features(path, fct_geometry):
        council = COUNCILS.get(normalize_location_name(name))
        if council:
            boundaries[council] = geometry
    return boundaries


def _classify_osm(tags):
    place = tags.get('place')
    if place:
        return OSM_TYPES.get(place, 'poi')
    for key in ('amenity', 'shop', 'tourism', 'leisure', 'office', 'building', 'aeroway', 'highway'):
        value = tags.get(key)
        if value:
            if key == 'amenity' and value in OSM_TYPES:
                return OSM_TYPES[value]
            if key == 'shop':
                return 'shopping_centre' if value in {'mall', 'department_store'} else 'commercial_area'
            if key == 'tourism' and value == 'hotel':
                return 'hotel'
            if key == 'aeroway' and value in {'aerodrome', 'terminal'}:
                return 'airport'
            if key == 'highway':
                return 'street' if value in {'residential', 'living_street', 'pedestrian'} else 'road'
            if key == 'building':
                return 'building'
            return 'poi'
    return OSM_TYPES.get(tags.get('landuse'), 'poi')


class OsmFctHandler(osmium.SimpleHandler):
    def __init__(self, polygon_path, council_boundaries):
        super().__init__()
        self.fct_geometry, _ = _load_fct_boundary(polygon_path)
        self.council_boundaries = council_boundaries
        self.records = []
        self.objects_processed = 0
        self.named_objects = 0
        self.discarded = 0
        self.categories = Counter()

    def _append(self, obj, geometry):
        tags = dict(obj.tags)
        name = tags.get('name') or tags.get('name:en')
        if not name:
            return
        self.named_objects += 1
        if len(name) > 180:
            self.discarded += 1
            return
        clipped = geometry.intersection(self.fct_geometry)
        if clipped.is_empty:
            self.discarded += 1
            return
        representative = clipped.representative_point()
        latitude, longitude = representative.y, representative.x
        source_id = f'{obj.__class__.__name__.lower()}:{obj.id}'
        aliases = [value for key, value in tags.items() if key.startswith('name:') and key != 'name:en']
        place_type = _classify_osm(tags)
        self.categories[place_type] += 1
        self.records.append({
            'name': name,
            'place_type': place_type,
            'area_council': _council_for_point(latitude, longitude, self.council_boundaries),
            'latitude': latitude,
            'longitude': longitude,
            'aliases': aliases,
            'keywords': [tags[key] for key in ('brand', 'operator', 'alt_name', 'old_name', 'loc_name') if tags.get(key)],
            'source': 'osm',
            'source_id': source_id,
            'source_url': f'https://www.openstreetmap.org/{source_id.replace(":", "/")}',
            'description': tags.get('description'),
            'confidence': 0.8,
            'verification_status': 'imported',
            'verified': False,
        })

    def way(self, way):
        self.objects_processed += 1
        tags = dict(way.tags)
        if not tags.get('name') and not tags.get('name:en'):
            return
        try:
            from shapely.geometry import LineString, Polygon
            coordinates = [(node.location.lon, node.location.lat) for node in way.nodes if node.location.valid()]
            if len(coordinates) < 2:
                return
            geometry = Polygon(coordinates) if way.is_closed() and len(coordinates) >= 4 else LineString(coordinates)
        except Exception:
            return
        self._append(way, geometry)

    def node(self, node):
        self.objects_processed += 1
        tags = dict(node.tags)
        if not tags.get('name') and not tags.get('name:en'):
            return
        try:
            from shapely.geometry import Point
            point = Point(node.location.lon, node.location.lat)
        except Exception:
            return
        self._append(node, point)

    def relation(self, relation):
        self.objects_processed += 1
        if relation.tags.get('name') or relation.tags.get('name:en'):
            self.named_objects += 1
            self.discarded += 1
        return None

    def progress(self):
        LOGGER.info(
            'OSM scan: %s objects, %s named objects, %s FCT named retained, %s discarded',
            self.objects_processed, self.named_objects, len(self.records), self.discarded,
        )


class ProgressHandler(osmium.SimpleHandler):
    def __init__(self, collector, log_every=1_000_000):
        super().__init__()
        self.collector = collector
        self.log_every = log_every

    def _record(self, object_type, obj):
        callback = getattr(self.collector, object_type)
        callback(obj)
        if self.collector.objects_processed and self.collector.objects_processed % self.log_every == 0:
            self.collector.progress()

    def node(self, node):
        self._record('node', node)

    def way(self, way):
        self._record('way', way)

    def relation(self, relation):
        self._record('relation', relation)


def _council_for_point(latitude, longitude, council_boundaries):
    from shapely.geometry import Point

    point = Point(longitude, latitude)
    return next((name for name, geometry in council_boundaries.items() if geometry.covers(point)), None)


def _read_geonames(path, fct_geometry, council_boundaries):
    rows = []
    processed = 0
    with zipfile.ZipFile(path) as archive:
        member = next((name for name in archive.namelist() if name.endswith('.txt') and name.startswith('NG')), None)
        if member is None:
            raise ValueError('GeoNames archive does not contain the Nigeria gazetteer file')
        from shapely.geometry import Point
        with archive.open(member) as source:
            for line in source:
                fields = line.decode('utf-8').rstrip('\n').split('\t')
                if len(fields) < 19:
                    continue
                processed += 1
                try:
                    latitude, longitude = float(fields[4]), float(fields[5])
                except ValueError:
                    continue
                if not fct_geometry.covers(Point(longitude, latitude)):
                    continue
                aliases = [name for name in fields[3].split(',') if name]
                rows.append({
                    'name': fields[1], 'place_type': 'town' if fields[7] == 'P' else 'poi',
                    'area_council': _council_for_point(latitude, longitude, council_boundaries),
                    'latitude': latitude, 'longitude': longitude,
                    'aliases': aliases, 'keywords': [fields[2]], 'source': 'geonames',
                    'source_id': fields[0], 'source_url': f'https://www.geonames.org/{fields[0]}',
                    'description': fields[11] or None, 'confidence': 0.75,
                    'verification_status': 'imported', 'verified': False,
                })
    return rows, processed


def _curated_records():
    records = []
    for item in FCT_LOCATION_SEED:
        record = dict(item)
        record['source'] = 'chigo'
        record['source_id'] = str(record.get('provider_place_id') or normalize_location_name(record['name']))
        record['source_url'] = None
        record['confidence'] = 1.0
        record['verification_status'] = 'verified'
        record['verified'] = True
        record['place_type'] = str(record.get('place_type') or 'poi').lower()
        record['keywords'] = record.pop('search_keywords', [])
        records.append(record)
    return records


def _source_connection_url():
    raw = os.getenv('DATABASE_URL')
    if not raw:
        raise RuntimeError('DATABASE_URL is required for PostgreSQL/PostGIS ingestion')
    url = make_url(raw)
    if url.get_backend_name() != 'postgresql':
        raise RuntimeError('Geodata ingestion requires PostgreSQL/PostGIS; SQLite fallback is not supported')
    if url.drivername in {'postgres', 'postgresql'}:
        url = url.set(drivername='postgresql+psycopg2')
    return url


def _validate_postgis_schema():
    engine = create_engine(_source_connection_url(), pool_pre_ping=True, connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT PostGIS_Version()')).scalar_one()
            missing = connection.execute(text("""
                SELECT name FROM (VALUES
                    ('location_records'), ('location_sources'), ('location_aliases'),
                    ('location_provenance'), ('location_observations'), ('administrative_areas')
                ) AS required(name)
                WHERE to_regclass(name) IS NULL
            """)).scalars().all()
            if missing:
                raise RuntimeError(
                    'PostGIS schema is not migrated. Run `flask db upgrade` before importing; missing: '
                    + ', '.join(missing)
                )
    finally:
        engine.dispose()


def _insert_records(engine, records, *, force=False, dry_run=False):
    if dry_run:
        return {'created': 0, 'enriched': 0, 'records': len(records)}
    created = 0
    enriched = 0
    with engine.begin() as connection:
        source_ids = {}
        for source in {record['source'] for record in records}:
            source_metadata = {
                'osm': {'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors'},
                'geonames': {'license': 'CC-BY-4.0', 'attribution': 'GeoNames.org'},
                'chigo': {'license': 'proprietary', 'attribution': 'Chigo curated data'},
            }.get(source, {})
            source_ids[source] = connection.execute(text("""
                INSERT INTO location_sources (source_name, source_type, source_url, metadata)
                VALUES (:name, :type, :url, CAST(:metadata AS json))
                ON CONFLICT (source_name) DO UPDATE SET source_url = COALESCE(EXCLUDED.source_url, location_sources.source_url)
                RETURNING id
            """), {
                'name': source,
                'type': 'open-geodata' if source in {'osm', 'geonames'} else 'curated',
                'url': {'osm': DEFAULT_OSM_URL, 'geonames': GEONAMES_URL, 'chigo': None}.get(source),
                'metadata': json.dumps(source_metadata),
            }).scalar_one()
        for record in records:
            point = f"SRID=4326;POINT({record['longitude']} {record['latitude']})"
            existing_id = connection.execute(text("""
                SELECT id FROM location_records
                WHERE normalized_name = :normalized_name
                  AND COALESCE(area_council, '') = COALESCE(:area_council, '')
                  AND place_type = :place_type
                  AND ST_DWithin(geom::geography, ST_GeogFromText(:geog), 250)
                ORDER BY (verification_status = 'verified') DESC, geom <-> ST_GeomFromEWKT(:geom)
                LIMIT 1
            """), {
                'normalized_name': normalize_location_name(record['name']),
                'area_council': record.get('area_council'),
                'place_type': record['place_type'],
                'geog': f"SRID=4326;POINT({record['longitude']} {record['latitude']})",
                'geom': point,
            }).scalar_one_or_none()
            if existing_id is None:
                existing_id = connection.execute(text("""
                INSERT INTO location_records (
                    name, normalized_name, place_type, parent_name, area_council, district,
                    neighborhood, area, state, country, address, latitude, longitude, geom,
                    aliases, search_keywords, source, source_id, source_url, description,
                    confidence, verification_status, verified, active, popularity_score,
                    created_at, updated_at
                ) VALUES (
                    :name, :normalized_name, :place_type, :parent_name, :area_council, :district,
                    :neighborhood, :area, :state, :country, :address, :latitude, :longitude,
                    ST_GeomFromEWKT(:geom), CAST(:aliases AS json), CAST(:keywords AS json),
                    :source, :source_id, :source_url, :description, :confidence,
                    :verification_status, :verified, TRUE, 0, NOW(), NOW()
                )
                ON CONFLICT (source, source_id) DO UPDATE SET
                    name = CASE WHEN location_records.verification_status = 'verified' AND NOT :force
                                THEN location_records.name ELSE EXCLUDED.name END,
                    latitude = CASE WHEN location_records.verification_status = 'verified' AND NOT :force
                                    THEN location_records.latitude ELSE EXCLUDED.latitude END,
                    longitude = CASE WHEN location_records.verification_status = 'verified' AND NOT :force
                                     THEN location_records.longitude ELSE EXCLUDED.longitude END,
                    geom = CASE WHEN location_records.verification_status = 'verified' AND NOT :force
                                THEN location_records.geom ELSE EXCLUDED.geom END,
                    aliases = (SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)::json
                               FROM jsonb_array_elements_text(COALESCE(location_records.aliases::jsonb, '[]'::jsonb) || EXCLUDED.aliases::jsonb) AS value),
                    search_keywords = (SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)::json
                                       FROM jsonb_array_elements_text(COALESCE(location_records.search_keywords::jsonb, '[]'::jsonb) || EXCLUDED.search_keywords::jsonb) AS value),
                    updated_at = NOW()
                RETURNING id
            """), {
                'name': record['name'],
                'normalized_name': normalize_location_name(record['name']),
                'place_type': record['place_type'],
                'parent_name': record.get('parent_name'),
                'area_council': record.get('area_council'),
                'district': record.get('district'),
                'neighborhood': record.get('neighborhood'),
                'area': record.get('area'),
                'state': record.get('state') or 'Federal Capital Territory',
                'country': record.get('country') or 'Nigeria',
                'address': record.get('address'),
                'latitude': record['latitude'],
                'longitude': record['longitude'],
                'geom': point,
                'aliases': json.dumps(record.get('aliases') or []),
                'keywords': json.dumps(record.get('keywords') or []),
                'source': record['source'],
                'source_id': str(record['source_id']),
                'source_url': record.get('source_url'),
                'description': record.get('description'),
                'confidence': float(record.get('confidence') or 0.5),
                'verification_status': record.get('verification_status') or 'imported',
                'verified': bool(record.get('verified', False)),
                'force': force,
            }).scalar_one()
                created += 1
            else:
                connection.execute(text("""
                    UPDATE location_records SET
                        aliases = (SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)::json
                                   FROM jsonb_array_elements_text(COALESCE(aliases::jsonb, '[]'::jsonb) || CAST(:aliases AS jsonb)) AS value),
                        search_keywords = (SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)::json
                                           FROM jsonb_array_elements_text(COALESCE(search_keywords::jsonb, '[]'::jsonb) || CAST(:keywords AS jsonb)) AS value),
                        updated_at = NOW()
                    WHERE id = :id
                """), {'id': existing_id, 'aliases': json.dumps(record.get('aliases') or []),
                      'keywords': json.dumps(record.get('keywords') or []), 'force': force})
                enriched += 1
            connection.execute(text("""
                INSERT INTO location_provenance (location_id, source_id, external_id, source_url, raw_attributes)
                VALUES (:location_id, :source_id, :external_id, :source_url, CAST(:attributes AS json))
                ON CONFLICT (source_id, external_id) DO UPDATE SET
                    location_id = EXCLUDED.location_id, source_url = EXCLUDED.source_url,
                    raw_attributes = EXCLUDED.raw_attributes
            """), {
                'location_id': existing_id, 'source_id': source_ids[record['source']],
                'external_id': str(record['source_id']), 'source_url': record.get('source_url'),
                'attributes': json.dumps({'place_type': record['place_type'], 'confidence': record.get('confidence')}),
            })
            for alias in record.get('aliases') or []:
                normalized_alias = normalize_location_name(alias)
                if normalized_alias:
                    connection.execute(text("""
                        INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
                        VALUES (:location_id, :alias, :normalized, :source)
                        ON CONFLICT (location_id, normalized_alias) DO NOTHING
                    """), {'location_id': existing_id, 'alias': alias, 'normalized': normalized_alias,
                          'source': record['source']})
    return {'created': created, 'enriched': enriched, 'records': len(records)}


def _resolve_hierarchy(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE location_records AS location SET
                                area_council_id = (
                                        SELECT area.id FROM administrative_areas AS area
                                        WHERE area.area_council = location.area_council
                                            AND area.area_council <> 'Federal Capital Territory'
                                            AND ST_Covers(area.geom, location.geom)
                                        LIMIT 1
                                ),
                                parent_id = COALESCE(location.parent_id, (
                                        SELECT parent_location.id FROM location_records AS parent_location
                                        WHERE lower(parent_location.name) = lower(location.parent_name)
                                        LIMIT 1
                                )),
                                district_id = COALESCE(location.district_id, (
                                        SELECT district_location.id FROM location_records AS district_location
                                        WHERE lower(district_location.name) = lower(location.district)
                                            AND district_location.area_council = location.area_council
                                            AND district_location.place_type = 'district'
                                        LIMIT 1
                                ))
                        WHERE location.area_council IS NOT NULL
        """))


def _insert_boundaries(engine, boundary_paths, fct_geometry, *, dry_run=False):
    retained = []
    for level, path in boundary_paths:
        for name, props, geometry in _boundary_features(path, fct_geometry):
            if level == 'ADM1' and normalize_location_name(name) not in {'abuja federal capital territory', 'federal capital territory'}:
                continue
            council = COUNCILS.get(normalize_location_name(name))
            if level == 'ADM2' and council is None:
                continue
            if level == 'ADM1':
                council = 'Federal Capital Territory'
            retained.append((level, name, council, props.get('shapeID') or name, geometry))
    if dry_run:
        return {'processed': len(retained), 'retained': len(retained)}
    with engine.begin() as connection:
        source_id = connection.execute(text("""
            INSERT INTO location_sources (source_name, source_type, source_url, metadata)
            VALUES ('geoBoundaries', 'administrative-boundaries', :url, CAST(:metadata AS json))
            ON CONFLICT (source_name) DO UPDATE SET source_url = EXCLUDED.source_url
            RETURNING id
        """), {'url': 'https://www.geoboundaries.org/', 'metadata': json.dumps({'levels': ['ADM1', 'ADM2']})}).scalar_one()
        for level, name, council, identifier, geometry in retained:
            geom_json = json.dumps(geometry.__geo_interface__)
            connection.execute(text("""
                INSERT INTO administrative_areas (name, slug, area_council, source_id, geom, source_url, source_identifier)
                VALUES (:name, :slug, :council, :source_id,
                        ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)),
                        :source_url, :identifier)
                ON CONFLICT (source_identifier) DO UPDATE SET
                    name = EXCLUDED.name, area_council = EXCLUDED.area_council,
                    geom = EXCLUDED.geom, source_url = EXCLUDED.source_url
            """), {
                'name': name, 'slug': f"{level.lower()}-{normalize_location_name(name).replace(' ', '-')}",
                'council': council, 'source_id': source_id, 'geometry': geom_json,
                'source_url': f'https://www.geoboundaries.org/', 'identifier': f'geoboundaries:{level}:{identifier}',
            })
    return {'processed': len(retained), 'retained': len(retained)}


def ingest(args):
    data_dir = Path(args.data_dir or os.getenv('POSTGIS_DATA_DIR', 'data/geodata'))
    selected = set()
    if args.download:
        selected.update({'osm', 'geonames', 'boundaries'})
    if args.all or args.import_osm or args.download:
        selected.add('osm')
    if args.all or args.import_geonames:
        selected.add('geonames')
    if args.all or args.import_boundaries:
        selected.add('boundaries')
    if (args.import_osm or args.import_geonames or args.all) and 'boundaries' not in selected:
        selected.add('boundaries')
    if not selected:
        raise ValueError('Choose --all, --download, or at least one --import-* option')
    if not args.dry_run and not args.download:
        _validate_postgis_schema()
    if args.download and not any((args.import_osm, args.import_geonames, args.import_boundaries, args.all)):
        paths = download_sources(data_dir, sources=selected, force=args.force)
        return {'downloaded': {key: str(value) for key, value in paths.items()}, 'database_writes': 0}
    paths = download_sources(data_dir, sources=selected, force=args.force)
    report = {'osm_processed': 0, 'fct_osm': 0, 'geonames_processed': 0,
              'fct_geonames': 0, 'boundary_records': 0, 'canonical_created': 0,
              'existing_enriched': 0, 'council_counts': Counter()}

    osm_records = []
    if 'osm' in selected and (args.all or args.import_osm):
        try:
            import osmium
        except ImportError as error:
            raise RuntimeError('Install requirements.txt to use the OSM PBF importer (osmium).') from error
        adm1_path = paths.get('boundaries_adm1') or data_dir / 'geoboundaries-NGA-ADM1.geojson'
        adm2_path = paths.get('boundaries_adm2') or data_dir / 'geoboundaries-NGA-ADM2.geojson'
        fct_geometry, _ = _load_fct_boundary(adm1_path)
        council_boundaries = _load_council_boundaries(adm2_path, fct_geometry)
        handler = OsmFctHandler(adm1_path, council_boundaries)
        progress_handler = ProgressHandler(handler)
        progress_handler.apply_file(str(paths['osm']), locations=True, idx='sparse_file_array')
        handler.progress()
        progress_handler = ProgressHandler(handler)
        progress_handler.apply_file(str(paths['osm']), locations=True, idx='sparse_file_array')
        handler.progress()
        osm_records = handler.records
        report['osm_objects_processed'] = handler.objects_processed
        report['osm_named_objects'] = handler.named_objects
        report['osm_records_discarded'] = handler.discarded
        report['osm_record_categories'] = dict(handler.categories)
        report['fct_roads'] = handler.categories['road'] + handler.categories['street']
        report['fct_landmarks'] = sum(handler.categories[kind] for kind in ('landmark', 'poi', 'hospital', 'school', 'hotel', 'shopping_centre'))
        report['fct_buildings'] = handler.categories['building']
        report['fct_administrative_records'] = handler.categories['district'] + handler.categories['area_council']
        report['fct_other_pois'] = sum(handler.categories.values()) - report['fct_roads'] - report['fct_landmarks'] - report['fct_buildings'] - report['fct_administrative_records']
        report['fct_osm'] = len(osm_records)

    geonames_records = []
    if 'geonames' in selected and (args.all or args.import_geonames):
        boundary_path = paths.get('boundaries_adm1') or data_dir / 'geoboundaries-NGA-ADM1.geojson'
        fct_geometry, _ = _load_fct_boundary(boundary_path)
        adm2_path = paths.get('boundaries_adm2') or data_dir / 'geoboundaries-NGA-ADM2.geojson'
        council_boundaries = _load_council_boundaries(adm2_path, fct_geometry)
        geonames_records, report['geonames_processed'] = _read_geonames(paths['geonames'], fct_geometry, council_boundaries)
        report['fct_geonames'] = len(geonames_records)

    boundary_paths = []
    if 'boundaries' in selected and (args.all or args.import_boundaries):
        boundary_paths = [('ADM1', paths['boundaries_adm1']), ('ADM2', paths['boundaries_adm2'])]
        fct_geometry, _ = _load_fct_boundary(paths['boundaries_adm1'])
        council_boundaries = _load_council_boundaries(paths['boundaries_adm2'], fct_geometry)
        boundary_report = _insert_boundaries(None, boundary_paths, fct_geometry, dry_run=True)
        report['boundary_records'] = boundary_report['retained']
        report['area_councils_resolved'] = len(council_boundaries)
        report['area_councils_missing'] = sorted(set(AREA_COUNCILS) - set(council_boundaries))

    records = osm_records + geonames_records
    if args.all or args.import_osm or args.import_geonames:
        records = _curated_records() + records
    if args.dry_run:
        report['canonical_locations'] = len(records)
        report.setdefault('area_councils_resolved', len({record.get('area_council') for record in records if record.get('area_council')}))
        return report

    if args.download:
        report['downloaded'] = {key: str(value) for key, value in paths.items()}
        report['database_writes'] = 0
        return report

    engine = create_engine(_source_connection_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT PostGIS_Version()'))
        if boundary_paths:
            _insert_boundaries(engine, boundary_paths, fct_geometry)
        ingest_counts = _insert_records(engine, records, force=args.force)
        _resolve_hierarchy(engine)
        report['canonical_created'] = ingest_counts['created']
        report['existing_enriched'] = ingest_counts['enriched']
        with engine.connect() as connection:
            counts = connection.execute(text("""
                SELECT area_council, count(*) FROM location_records
                WHERE active GROUP BY area_council
            """)).all()
        report['council_counts'] = dict(counts)
    finally:
        engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description='Download and ingest public Nigeria/FCT geographic datasets into PostgreSQL/PostGIS.')
    parser.add_argument('--data-dir', help='Cache directory for downloaded source data.')
    parser.add_argument('--download', action='store_true', help='Download selected source datasets only; no database writes.')
    parser.add_argument('--import-osm', action='store_true', help='Download/parse Nigeria OSM PBF and retain named features inside the FCT polygon.')
    parser.add_argument('--import-geonames', action='store_true', help='Download GeoNames Nigeria and retain points inside the FCT polygon.')
    parser.add_argument('--import-boundaries', action='store_true', help='Download and import FCT/area council boundary geometry.')
    parser.add_argument('--normalize', action='store_true', help='Normalize names and aliases during import.')
    parser.add_argument('--deduplicate', action='store_true', help='Use stable source IDs and upsert to prevent repeat-import duplicates.')
    parser.add_argument('--rebuild-index', action='store_true', help='Reindex PostGIS search/spatial indexes after ingestion.')
    parser.add_argument('--all', action='store_true', help='Download and import OSM, GeoNames, and boundaries.')
    parser.add_argument('--dry-run', action='store_true', help='Download and parse sources, report counts, and perform no database writes.')
    parser.add_argument('--force', action='store_true', help='Redownload cached files and allow imported records to refresh verified rows.')
    args = parser.parse_args()
    report = ingest(args)
    if args.rebuild_index and not args.dry_run:
        engine = create_engine(_source_connection_url(), pool_pre_ping=True)
        try:
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                connection.execute(text('REINDEX INDEX CONCURRENTLY ix_location_records_name_trgm'))
                connection.execute(text('REINDEX INDEX CONCURRENTLY ix_location_records_geom'))
        finally:
            engine.dispose()
    print('CHIGO FCT LOCATION IMPORT')
    print(json.dumps(report, indent=2, default=dict))


if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
    main()
