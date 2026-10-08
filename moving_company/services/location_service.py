import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher

from flask import current_app, has_app_context
from sqlalchemy import func, or_, text
from sqlalchemy.exc import SQLAlchemyError

from .fct_location_data import FCT_AREA_COUNCILS, FCT_LOCATION_SEED

ABUJA_FCT_CENTER = {'latitude': 9.0765, 'longitude': 7.3986}
FCT_BOUNDS = {'min_latitude': 8.2, 'max_latitude': 9.85, 'min_longitude': 6.6, 'max_longitude': 7.85}
_SEARCH_CACHE = {}
_SEARCH_CACHE_TTL = 90
_SEARCH_CACHE_LIMIT = 500
_ROUTE_CACHE = {}
_ROUTE_CACHE_TTL = 300

ABUJA_ALIAS_MAP = {
    'gwarimpa': 'gwarinpa',
    'jikoyi': 'jikwoyi',
    'jikowyi': 'jikwoyi',
    'jikwyoi': 'jikwoyi',
    'wuse 2': 'wuse ii',
    'wuse two': 'wuse ii',
    'life camp': 'life camp',
    'life-camp': 'life camp',
    'lifecamp': 'life camp',
    'airport road': 'airport road',
    'kubwa express': 'kubwa expressway',
    'kubwa expressway': 'kubwa expressway',
    'gwarinpa estate': 'gwarinpa estate',
    'market square jikoyi': 'market square jikwoyi',
    'market square jikowyi': 'market square jikwoyi',
}

def _get_config_value(name):
    if has_app_context():
        return current_app.config.get(name)
    return os.getenv(name)


def _request_json(url, headers=None, timeout=12):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode('utf-8')
            if not payload:
                return None
            return json.loads(payload)
    except Exception:
        return None


def _nominatim_request(path, params=None, timeout=3.5):
    base_url = _get_config_value('NOMINATIM_BASE_URL') or 'https://nominatim.openstreetmap.org'
    request_path = f"{base_url.rstrip('/')}{path}"
    request_params = {'format': 'jsonv2', 'accept-language': 'en', 'addressdetails': 1}
    if params:
        request_params.update(params)
    if 'countrycodes' not in request_params:
        request_params['countrycodes'] = 'ng'
    query_string = urllib.parse.urlencode(request_params, doseq=True)
    headers = {'User-Agent': _get_config_value('OSM_USER_AGENT') or 'ChigoRelocations/1.0'}
    return _request_json(f'{request_path}?{query_string}', headers=headers, timeout=timeout)


def _photon_request(path, params=None, timeout=3.5):
    base_url = _get_config_value('PHOTON_BASE_URL') or 'https://photon.kom'
    request_path = f"{base_url.rstrip('/')}{path}"
    request_params = {'lang': 'en'}
    if params:
        request_params.update(params)
    query_string = urllib.parse.urlencode(request_params, doseq=True)
    return _request_json(f'{request_path}?{query_string}', headers={'User-Agent': _get_config_value('OSM_USER_AGENT') or 'ChigoRelocations/1.0'}, timeout=timeout)


def _osrm_request(path, params=None, timeout=5):
    base_url = _get_config_value('OSRM_BASE_URL') or 'https://router.project-osrm.org'
    request_path = f"{base_url.rstrip('/')}{path}"
    request_params = {'overview': 'false', 'alternatives': 'false', 'steps': 'false'}
    if params:
        request_params.update(params)
    query_string = urllib.parse.urlencode(request_params, doseq=True)
    headers = {'User-Agent': _get_config_value('OSM_USER_AGENT') or 'ChigoRelocations/1.0'}
    return _request_json(f'{request_path}?{query_string}', headers=headers, timeout=timeout)


def normalize_location_name(value):
    value = str(value or '').casefold().replace('&', ' and ')
    value = re.sub(r'[^a-z0-9]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    aliases = {
        'gwarimpa': 'gwarinpa', 'jikoyi': 'jikwoyi', 'jikowyi': 'jikwoyi',
        'wuse 2': 'wuse ii', 'wuse two': 'wuse ii', 'wuse 1': 'wuse i',
        'life camp': 'life camp', 'lifecamp': 'life camp', 'amac': 'abuja municipal area council',
    }
    for alias, canonical in sorted(aliases.items(), key=lambda item: -len(item[0])):
        if value == alias:
            return canonical
    return value


def clear_location_search_cache():
    _SEARCH_CACHE.clear()
    _ROUTE_CACHE.clear()


def validate_coordinates(latitude, longitude):
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


def get_local_place(place_id):
    value = str(place_id or '')
    if value.startswith('observation-') and has_app_context():
        from ..extensions import db

        if db.engine.dialect.name == 'postgresql':
            try:
                identifier = int(value.partition('-')[2])
                row = db.session.execute(text("""
                    SELECT id, raw_input, formatted_address, latitude, longitude,
                           area_council, district, nearest_landmark, nearest_road,
                           resolution_source, confidence
                    FROM location_observations
                    WHERE id = :id AND status IN ('pending_review', 'approved')
                """), {'id': identifier}).mappings().first()
            except (ValueError, SQLAlchemyError):
                db.session.rollback()
                return None
            if row:
                return {
                    'id': None, 'name': row['nearest_landmark'] or row['raw_input'],
                    'formatted_address': row['formatted_address'] or row['raw_input'],
                    'display_name': row['formatted_address'] or row['raw_input'],
                    'area': row['district'] or row['area_council'] or 'Abuja',
                    'area_council': row['area_council'] or '', 'district': row['district'] or '',
                    'neighborhood': '', 'location_type': 'cached_user_location',
                    'city': 'Abuja', 'state': 'Federal Capital Territory', 'country': 'Nigeria',
                    'latitude': row['latitude'], 'longitude': row['longitude'],
                    'place_id': value, 'provider_place_id': value,
                    'provider': 'local-reference', 'provider_raw_id': str(row['id']),
                    'source': 'cached-user-location', 'confidence': row['confidence'],
                    'nearest_landmark': row['nearest_landmark'], 'nearest_road': row['nearest_road'],
                }
        return None
    if value.startswith('postgis-') and has_app_context():
        from ..extensions import db

        if db.engine.dialect.name == 'postgresql':
            try:
                identifier = int(value.partition('-')[2])
                row = db.session.execute(text("""
                    SELECT id, name, address, area, area_council, district, neighborhood,
                           place_type, state, country, latitude, longitude, source_id, source
                    FROM location_records WHERE id = :id AND active IS TRUE
                """), {'id': identifier}).mappings().first()
            except (ValueError, SQLAlchemyError):
                db.session.rollback()
                return None
            if row:
                return {
                    'id': row['id'], 'name': row['name'],
                    'formatted_address': row['address'] or row['name'],
                    'display_name': row['address'] or row['name'],
                    'area': row['area'] or row['neighborhood'] or row['district'] or row['name'],
                    'area_council': row['area_council'] or '', 'district': row['district'] or '',
                    'neighborhood': row['neighborhood'] or '', 'location_type': row['place_type'],
                    'city': 'Abuja', 'state': row['state'], 'country': row['country'],
                    'latitude': float(row['latitude']), 'longitude': float(row['longitude']),
                    'place_id': value, 'provider_place_id': value,
                    'provider': 'local-reference', 'provider_raw_id': row['source_id'],
                    'source': row['source'],
                }
        return None
    if not value.startswith('local-'):
        return None
    from ..models import LocalPlace
    try:
        place_id_number = int(value.partition('-')[2])
    except ValueError:
        return None
    place = LocalPlace.query.filter_by(id=place_id_number, active=True).first()
    if place is None:
        return None
    return {
        'id': place.id, 'local_place_id': place.id,
        'name': place.name, 'formatted_address': place.address or place.name,
        'display_name': place.address or place.name, 'area': place.area or place.neighborhood or place.district or place.name,
        'area_council': place.area_council, 'district': place.district or '',
        'neighborhood': place.neighborhood or '', 'location_type': place.place_type,
        'city': 'Abuja', 'state': place.state, 'country': place.country,
        'latitude': float(place.latitude), 'longitude': float(place.longitude),
        'place_id': f'local-{place.id}', 'provider_place_id': f'local-{place.id}',
        'provider': 'local-reference', 'provider_raw_id': f'local-{place.id}',
        'source': place.provider,
    }


def normalize_query(query):
    if not query:
        return ''

    normalized = query.strip().replace('–', '-').replace('—', '-')
    normalized = re.sub(r'\s+', ' ', normalized)
    normalized = normalized.lower()

    for key, value in sorted(ABUJA_ALIAS_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = re.sub(rf'\b{re.escape(key)}\b', value, normalized)

    replacements = [
        ('gwarimpa', 'gwarinpa'),
        ('gwarinpa estate', 'gwarinpa estate'),
        ('jikoyi', 'jikwoyi'),
        ('jikowyi', 'jikwoyi'),
        ('jikwyoi', 'jikwoyi'),
        ('life-camp', 'life camp'),
        ('lifecamp', 'life camp'),
        ('wuse 2', 'wuse ii'),
        ('wuse two', 'wuse ii'),
        ('wuse ii', 'wuse ii'),
        ('fct', 'federal capital territory'),
        ('abuja fct', 'abuja federal capital territory'),
        ('abuja nigeria', 'abuja federal capital territory nigeria'),
    ]
    for old, new in replacements:
        normalized = normalized.replace(old, new)

    normalized = re.sub(r'\b(near|around|close to|behind|opposite|beside|off|along|by|next to)\b', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def _normalise_result(raw_result):
    address = raw_result.get('address') or {}
    provider_place_id = raw_result.get('provider_place_id') or raw_result.get('place_id') or raw_result.get('osm_id') or raw_result.get('id') or raw_result.get('name')
    result = {
        'name': raw_result.get('name') or raw_result.get('display_name') or raw_result.get('formatted_address') or 'Location',
        'formatted_address': raw_result.get('formatted_address') or raw_result.get('display_name') or raw_result.get('name') or 'Address unavailable',
        'display_name': raw_result.get('display_name') or raw_result.get('formatted_address') or raw_result.get('name') or 'Address unavailable',
        'area': raw_result.get('area') or address.get('suburb') or address.get('neighbourhood') or address.get('city_district') or raw_result.get('suburb') or raw_result.get('district') or raw_result.get('neighbourhood') or raw_result.get('city') or raw_result.get('name'),
        'area_council': raw_result.get('area_council') or address.get('area_council') or address.get('county') or '',
        'district': raw_result.get('district') or address.get('city_district') or address.get('county') or '',
        'neighborhood': raw_result.get('neighborhood') or raw_result.get('neighbourhood') or address.get('neighbourhood') or address.get('suburb') or '',
        'location_type': raw_result.get('location_type') or raw_result.get('place_type') or raw_result.get('type') or 'address',
        'city': raw_result.get('city') or address.get('city') or address.get('town') or address.get('village') or raw_result.get('town') or raw_result.get('village') or raw_result.get('municipality') or 'Abuja',
        'state': raw_result.get('state') or address.get('state') or address.get('state_district') or raw_result.get('state_district') or raw_result.get('administrative_area_level_1') or 'Federal Capital Territory',
        'country': raw_result.get('country') or address.get('country') or raw_result.get('country_name') or 'Nigeria',
        'latitude': float(raw_result.get('latitude') or raw_result.get('lat') or 0),
        'longitude': float(raw_result.get('longitude') or raw_result.get('lng') or raw_result.get('lon') or 0),
        'place_id': provider_place_id,
        'provider_place_id': provider_place_id,
        'provider': raw_result.get('provider') or 'nominatim',
        'provider_raw_id': raw_result.get('provider_raw_id') or raw_result.get('place_id') or raw_result.get('osm_id') or raw_result.get('id'),
        'coordinate_precision': raw_result.get('coordinate_precision') or ('street' if raw_result.get('location_type') in {'way', 'node'} else 'area'),
        'type': raw_result.get('type') or raw_result.get('place_type') or '',
        'category': raw_result.get('category') or raw_result.get('class') or '',
        'address': address,
        'original_query': raw_result.get('original_query') or '',
        'normalized_query': raw_result.get('normalized_query') or '',
        'source': raw_result.get('source') or raw_result.get('provider') or 'nominatim',
    }
    if result['formatted_address'] and result['formatted_address'] != result['name']:
        result['name'] = result['name'] or result['formatted_address'].split(',')[0].strip()
    return result


def _photon_to_location(item):
    display_name = item.get('display_name') or item.get('name') or 'Location'
    latitude = float(item.get('lat') or 0)
    longitude = float(item.get('lon') or 0)
    osm_type = str(item.get('osm_type') or '').casefold()
    precision = 'street' if osm_type in {'way', 'node'} else 'area'
    location = {
        'name': display_name,
        'formatted_address': display_name,
        'area': item.get('city') or item.get('town') or 'Abuja',
        'area_council': area_council_for_point(latitude, longitude),
        'district': '', 'neighborhood': '', 'location_type': item.get('osm_type') or 'address',
        'city': item.get('city') or 'Abuja', 'state': 'Federal Capital Territory',
        'country': 'Nigeria', 'latitude': latitude, 'longitude': longitude,
        'place_id': f"photon-{item.get('osm_id') or latitude}:{longitude}",
        'provider_place_id': f"photon-{item.get('osm_id') or latitude}:{longitude}",
        'provider': 'photon', 'provider_raw_id': item.get('osm_id') or '',
        'coordinate_precision': precision, 'source': 'photon',
        'address_components': item,
    }
    return location


def _nominatim_to_location(item):
    address = item.get('address') or {}
    display_name = item.get('display_name') or item.get('name') or 'Location'
    name = item.get('name') or display_name.split(',')[0].strip() or 'Location'
    city = address.get('city') or address.get('town') or address.get('village') or address.get('municipality') or 'Abuja'
    state = address.get('state') or address.get('state_district') or 'Federal Capital Territory'
    area = address.get('suburb') or address.get('neighbourhood') or address.get('city_district') or address.get('county') or city
    location = {
        'name': name,
        'formatted_address': display_name,
        'area': area,
        'area_council': '',
        'district': address.get('county') or address.get('state_district') or '',
        'neighborhood': address.get('neighbourhood') or address.get('suburb') or '',
        'location_type': item.get('type') or item.get('class') or 'address',
        'city': city,
        'state': state,
        'country': address.get('country') or 'Nigeria',
        'latitude': float(item.get('lat') or 0),
        'longitude': float(item.get('lon') or 0),
        'place_id': item.get('place_id') or item.get('osm_id') or str(item.get('lat')) + ':' + str(item.get('lon')),
        'provider_place_id': item.get('place_id') or item.get('osm_id') or str(item.get('lat')) + ':' + str(item.get('lon')),
        'provider': 'nominatim',
        'provider_raw_id': item.get('osm_id') or item.get('place_id'),
        'type': item.get('type') or '',
        'category': item.get('class') or '',
        'address': address,
    }
    location['area_council'] = area_council_for_point(location['latitude'], location['longitude'])
    return location


def area_council_for_point(latitude, longitude):
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return ''
    if not (FCT_BOUNDS['min_latitude'] <= latitude <= FCT_BOUNDS['max_latitude'] and
            FCT_BOUNDS['min_longitude'] <= longitude <= FCT_BOUNDS['max_longitude']):
        return ''
    centers = {
        'Abuja Municipal Area Council': (9.0578, 7.4951), 'Bwari Area Council': (9.2813, 7.3736),
        'Gwagwalada Area Council': (8.9435, 7.0913), 'Kuje Area Council': (8.8795, 7.2276),
        'Kwali Area Council': (8.8217, 6.9835), 'Abaji Area Council': (8.4759, 6.9447),
    }
    return min(centers, key=lambda name: (centers[name][0] - latitude) ** 2 + (centers[name][1] - longitude) ** 2)


def _search_local_abuja_places(query, limit=8):
    try:
        from ..models import LocalPlace
    except Exception:
        return []

    raw_query = (query or '').strip()
    normalized = normalize_location_name(raw_query)
    if len(normalized) < 2:
        return []

    base = LocalPlace.query.filter(LocalPlace.active.is_(True))
    candidates = {}
    exact = base.filter(func.lower(LocalPlace.normalized_name) == normalized).limit(30).all()
    prefixes = base.filter(func.lower(LocalPlace.normalized_name).like(f'{normalized}%')).limit(60).all()
    for place in exact + prefixes:
        candidates[place.id] = place

    terms = [term for term in normalized.split() if len(term) >= 2]
    if terms:
        clauses = []
        for term in terms[:4]:
            like = f'%{term}%'
            clauses.extend((
                func.lower(LocalPlace.aliases).like(like),
                func.lower(LocalPlace.search_keywords).like(like),
                func.lower(LocalPlace.district).like(f'{term}%'),
                func.lower(LocalPlace.neighborhood).like(f'{term}%'),
            ))
        for place in base.filter(or_(*clauses)).limit(120).all():
            candidates[place.id] = place

    if not candidates and len(normalized) >= 4:
        for place in base.filter(func.lower(LocalPlace.normalized_name).like(f'{normalized[0]}%')).order_by(
            LocalPlace.popularity_score.desc()
        ).limit(200).all():
            candidates[place.id] = place

    matches = []
    for place in candidates.values():
        name = normalize_location_name(place.name)
        aliases = [normalize_location_name(alias) for alias in re.split(r'[,;|]', place.aliases or '') if alias.strip()]
        keywords = [normalize_location_name(word) for word in re.split(r'[,;|]', place.search_keywords or '') if word.strip()]
        fields = [name, normalize_location_name(place.normalized_name), *aliases, *keywords,
                  normalize_location_name(place.district), normalize_location_name(place.neighborhood)]
        if normalized == name or normalized == normalize_location_name(place.normalized_name) or normalized in aliases:
            rank = 1000
        elif name.startswith(normalized) or normalize_location_name(place.normalized_name).startswith(normalized):
            rank = 850
        elif any(field and normalized in field for field in fields):
            rank = 650
        elif any(field and field.startswith(normalized) for field in aliases + keywords):
            rank = 600
        else:
            ratio = max((SequenceMatcher(None, normalized, field).ratio() for field in fields if field), default=0)
            if ratio < 0.62:
                continue
            rank = 350 + int(ratio * 100)
        if 'airport' in normalized and place.place_type == 'airport':
            rank += 250
        rank += min(100, int(place.popularity_score or 0))
        matches.append((rank, place))

    matches.sort(key=lambda match: (-match[0], match[1].name.casefold()))
    results = []
    seen_local = set()
    for _, place in matches[:limit]:
        canonical_key = (normalize_location_name(place.name), round(float(place.latitude or 0), 4), round(float(place.longitude or 0), 4))
        if canonical_key in seen_local:
            continue
        seen_local.add(canonical_key)
        result_id = f'local-{place.id}'
        council = place.area_council or ''
        context = ', '.join(part for part in (place.neighborhood, place.district, council, 'FCT', 'Nigeria') if part)
        results.append({
            'id': place.id, 'local_place_id': place.id,
            'name': place.name,
            'formatted_address': place.address or f'{place.name}, {context}',
            'display_name': place.address or f'{place.name}, {context}',
            'area': place.area or place.neighborhood or place.district or place.name,
            'area_council': council,
            'district': place.district or '',
            'neighborhood': place.neighborhood or '',
            'location_type': place.place_type or 'point_of_interest',
            'type': place.place_type or 'point_of_interest',
            'city': 'Abuja', 'state': place.state or 'Federal Capital Territory',
            'country': place.country or 'Nigeria', 'latitude': place.latitude,
            'longitude': place.longitude, 'place_id': result_id,
            'provider_place_id': result_id, 'provider': 'local-reference',
            'provider_raw_id': result_id, 'source': place.provider or 'chigo-curated',
            'popularity_score': place.popularity_score or 0,
        })
    return results


def _search_postgis_locations(query, limit=8):
    if not has_app_context():
        return []
    from ..extensions import db

    if db.engine.dialect.name != 'postgresql':
        return []
    normalized = normalize_location_name(query)
    try:
        rows = db.session.execute(text("""
            WITH matches AS (
                SELECT
                location_records.id, location_records.name, location_records.normalized_name,
                location_records.place_type, location_records.area_council,
                location_records.district, location_records.neighborhood, location_records.area,
                location_records.state, location_records.country, location_records.latitude,
                location_records.longitude, location_records.source, location_records.source_id,
                location_records.confidence, location_records.verification_status,
                location_records.address, location_records.aliases,
                COALESCE(location_records.popularity_score, 0) AS popularity_score,
                CASE WHEN location_records.normalized_name = :normalized THEN 1000
                     WHEN location_records.normalized_name ILIKE :prefix THEN 800
                     WHEN location_aliases.normalized_alias = :normalized THEN 950
                     WHEN location_aliases.normalized_alias ILIKE :prefix THEN 700
                     ELSE similarity(location_records.normalized_name, :normalized) * 500 END AS match_score
                FROM location_records
                LEFT JOIN location_aliases ON location_aliases.location_id = location_records.id
                WHERE location_records.active IS TRUE AND (
                    location_records.normalized_name ILIKE :prefix
                    OR location_aliases.normalized_alias ILIKE :contains
                    OR COALESCE(location_records.district, '') ILIKE :contains
                    OR COALESCE(location_records.neighborhood, '') ILIKE :contains
                    OR similarity(location_records.normalized_name, :normalized) >= 0.25
                )
            ), deduplicated AS (
                SELECT DISTINCT ON (id) * FROM matches
                ORDER BY id, match_score DESC, popularity_score DESC
            ), ranked AS (
                SELECT *, row_number() OVER (ORDER BY match_score DESC, popularity_score DESC, name) AS relevance_rank
                FROM deduplicated
            )
            SELECT * FROM ranked ORDER BY relevance_rank
            LIMIT :limit
        """), {
            'prefix': f'{normalized}%', 'contains': f'%{normalized}%',
            'normalized': normalized, 'limit': max(1, min(int(limit), 10)),
        }).mappings().all()
    except SQLAlchemyError:
        db.session.rollback()
        return []

    if len(rows) < max(1, min(int(limit), 10)):
        remaining = max(1, min(int(limit), 10)) - len(rows)
        try:
            observations = db.session.execute(text("""
                  SELECT id, raw_input, formatted_address, provider_reference, latitude, longitude,
                      area_council, district, nearest_landmark, nearest_road,
                      resolution_source, route_resolution, confidence, usage_count
                FROM location_observations
                WHERE status IN ('pending_review', 'approved')
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                  AND (normalized_input ILIKE :prefix OR normalized_input ILIKE :contains)
                ORDER BY CASE WHEN normalized_input = :normalized THEN 0 ELSE 1 END,
                         usage_count DESC, last_seen_at DESC
                LIMIT :limit
            """), {'prefix': f'{normalized}%', 'contains': f'%{normalized}%',
                  'normalized': normalized, 'limit': remaining}).mappings().all()
        except SQLAlchemyError:
            db.session.rollback()
            observations = []
        for observation in observations:
            results.append({
                'id': f"observation-{observation['id']}",
                'name': observation['nearest_landmark'] or observation['raw_input'],
                'formatted_address': observation['formatted_address'] or observation['raw_input'],
                'display_name': observation['formatted_address'] or observation['raw_input'],
                'area': observation['district'] or observation['area_council'] or 'Abuja',
                'area_council': observation['area_council'] or '',
                'district': observation['district'] or '', 'neighborhood': '',
                'location_type': 'cached_user_location', 'type': 'cached_user_location',
                'city': 'Abuja', 'state': 'Federal Capital Territory', 'country': 'Nigeria',
                'latitude': observation['latitude'], 'longitude': observation['longitude'],
                'place_id': f"observation-{observation['id']}",
                'provider_place_id': f"observation-{observation['id']}",
                'provider': 'local-reference', 'provider_raw_id': observation['provider_reference'] or str(observation['id']),
                'source': 'cached-user-location', 'confidence': observation['confidence'],
                'verification_status': 'user_submitted', 'usage_count': observation['usage_count'],
                'nearest_landmark': observation['nearest_landmark'], 'nearest_road': observation['nearest_road'],
            })

    for row in rows:
        context = ', '.join(part for part in (row['neighborhood'], row['district'], row['area_council'], 'FCT', 'Nigeria') if part)
        results.append({
            'id': row['id'], 'name': row['name'],
            'formatted_address': row['address'] or f"{row['name']}, {context}",
            'display_name': row['address'] or f"{row['name']}, {context}",
            'area': row['area'] or row['neighborhood'] or row['district'] or row['name'],
            'area_council': row['area_council'] or '', 'district': row['district'] or '',
            'neighborhood': row['neighborhood'] or '', 'location_type': row['place_type'],
            'type': row['place_type'], 'city': 'Abuja', 'state': row['state'],
            'country': row['country'], 'latitude': row['latitude'], 'longitude': row['longitude'],
            'place_id': f"postgis-{row['id']}", 'provider_place_id': f"postgis-{row['id']}",
            'provider': 'local-reference', 'provider_raw_id': row['source_id'],
            'source': row['source'], 'confidence': row['confidence'],
            'verification_status': row['verification_status'],
            'popularity_score': row['popularity_score'],
        })
    return results


def record_location_observation(raw_input, *, result=None, area_council=None, district=None, latitude=None, longitude=None, nearest_road=None, route_resolution=None):
    raw_input = str(raw_input or '').strip()[:500]
    normalized = normalize_location_name(raw_input)
    if not raw_input or not normalized or not has_app_context():
        return False
    from ..extensions import db

    if db.engine.dialect.name != 'postgresql':
        return False
    coordinates = validate_coordinates(latitude, longitude)
    if coordinates:
        latitude, longitude = coordinates
    else:
        latitude = longitude = None
    result = result or {}
    savepoint = db.session.begin_nested()
    try:
        db.session.execute(text("""
            INSERT INTO location_observations (
                normalized_input, raw_input, formatted_address, provider_reference,
                latitude, longitude, geom, area_council, district, nearest_landmark,
                nearest_road, resolution_source, route_resolution, confidence,
                usage_count, status, first_seen_at, last_seen_at
            ) VALUES (
                :normalized, :raw_input, :formatted_address, :provider_reference,
                :latitude, :longitude,
                CASE WHEN :longitude IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326) END,
                :area_council, :district, :nearest_landmark, :nearest_road,
                :source, :route_resolution, :confidence,
                1, 'pending_review', NOW(), NOW()
            )
            ON CONFLICT (normalized_input) DO UPDATE SET
                raw_input = EXCLUDED.raw_input,
                formatted_address = COALESCE(EXCLUDED.formatted_address, location_observations.formatted_address),
                provider_reference = COALESCE(EXCLUDED.provider_reference, location_observations.provider_reference),
                latitude = COALESCE(EXCLUDED.latitude, location_observations.latitude),
                longitude = COALESCE(EXCLUDED.longitude, location_observations.longitude),
                geom = COALESCE(EXCLUDED.geom, location_observations.geom),
                area_council = COALESCE(EXCLUDED.area_council, location_observations.area_council),
                district = COALESCE(EXCLUDED.district, location_observations.district),
                nearest_landmark = COALESCE(EXCLUDED.nearest_landmark, location_observations.nearest_landmark),
                nearest_road = COALESCE(EXCLUDED.nearest_road, location_observations.nearest_road),
                resolution_source = COALESCE(EXCLUDED.resolution_source, location_observations.resolution_source),
                route_resolution = COALESCE(EXCLUDED.route_resolution, location_observations.route_resolution),
                confidence = COALESCE(EXCLUDED.confidence, location_observations.confidence),
                usage_count = location_observations.usage_count + 1,
                last_seen_at = NOW()
        """), {
            'normalized': normalized, 'raw_input': raw_input,
            'formatted_address': result.get('formatted_address'),
            'provider_reference': result.get('provider_place_id') or result.get('provider_raw_id'),
            'latitude': latitude, 'longitude': longitude,
            'area_council': area_council or result.get('area_council'),
            'district': district or result.get('district'),
            'nearest_landmark': result.get('nearest_landmark') or (result.get('name') if coordinates else None),
            'nearest_road': nearest_road or result.get('nearest_road'),
            'source': result.get('provider') or result.get('source'),
            'route_resolution': route_resolution or ('exact' if coordinates else 'unresolved'),
            'confidence': result.get('confidence'),
        })
        savepoint.commit()
        return True
    except SQLAlchemyError:
        savepoint.rollback()
        return False


def resolve_nearest_postgis_references(latitude, longitude, *, maximum_distance_m=5000):
        coordinates = validate_coordinates(latitude, longitude)
        if not coordinates or not has_app_context():
                return {}
        from ..extensions import db

        if db.engine.dialect.name != 'postgresql':
                return {}
        latitude, longitude = coordinates
        point_wkt = f'SRID=4326;POINT({longitude} {latitude})'
        try:
                result = db.session.execute(text("""
                        WITH query_point AS (
                                SELECT ST_GeomFromEWKT(:point) AS geom
                        )
                        SELECT
                                (SELECT area.area_council FROM administrative_areas AS area, query_point
                                 WHERE area.area_council <> 'Federal Capital Territory'
                                     AND ST_Covers(area.geom, query_point.geom)
                                 ORDER BY ST_Area(area.geom::geography) ASC LIMIT 1) AS area_council,
                                (SELECT place.name FROM location_records AS place, query_point
                                 WHERE place.active IS TRUE AND place.place_type IN ('district', 'neighborhood', 'community')
                                     AND ST_DWithin(place.geom::geography, query_point.geom::geography, :max_distance)
                                 ORDER BY place.geom <-> query_point.geom LIMIT 1) AS district,
                                (SELECT place.name FROM location_records AS place, query_point
                                 WHERE place.active IS TRUE AND place.place_type IN ('landmark', 'poi', 'hospital', 'school', 'hotel', 'shopping_centre')
                                     AND ST_DWithin(place.geom::geography, query_point.geom::geography, :max_distance)
                                 ORDER BY place.geom <-> query_point.geom LIMIT 1) AS nearest_landmark,
                                (SELECT place.name FROM location_records AS place, query_point
                                 WHERE place.active IS TRUE AND place.place_type IN ('road', 'street')
                                     AND ST_DWithin(place.geom::geography, query_point.geom::geography, :max_distance)
                                 ORDER BY place.geom <-> query_point.geom LIMIT 1) AS nearest_road
                """), {'point': point_wkt, 'max_distance': maximum_distance_m}).mappings().one()
                return dict(result)
        except SQLAlchemyError:
                db.session.rollback()
                return {}


def review_location_observation(observation_id, action, *, canonical_name=None, place_type='neighborhood'):
    if not has_app_context():
        raise RuntimeError('Location review requires an application context.')
    from ..extensions import db

    if db.engine.dialect.name != 'postgresql':
        raise RuntimeError('Location review requires PostgreSQL/PostGIS.')
    if action not in {'approve', 'reject', 'promote'}:
        raise ValueError('Choose approve, reject, or promote.')
    observation = db.session.execute(text("""
        SELECT * FROM location_observations WHERE id = :id FOR UPDATE
    """), {'id': int(observation_id)}).mappings().first()
    if observation is None:
        raise LookupError('Location observation was not found.')
    if action == 'reject':
        db.session.execute(text("""
            UPDATE location_observations SET status = 'rejected' WHERE id = :id
        """), {'id': observation_id})
        db.session.commit()
        return {'status': 'rejected', 'location_id': None}

    if not observation['latitude'] or not observation['longitude']:
        raise ValueError('A location needs resolved coordinates before approval or promotion.')
    if action == 'approve':
        db.session.execute(text("""
            UPDATE location_observations SET status = 'approved' WHERE id = :id
        """), {'id': observation_id})
        db.session.commit()
        return {'status': 'approved', 'location_id': None}

    name = str(canonical_name or observation['raw_input']).strip()[:180]
    normalized = normalize_location_name(name)
    existing = db.session.execute(text("""
        SELECT id FROM location_records
        WHERE normalized_name = :normalized
          AND place_type = :place_type
          AND COALESCE(area_council, '') = COALESCE(:council, '')
          AND ST_DWithin(geom::geography,
              ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography, 250)
        ORDER BY (verification_status = 'verified') DESC, id
        LIMIT 1 FOR UPDATE
    """), {
        'normalized': normalized, 'place_type': place_type,
        'council': observation['area_council'], 'longitude': observation['longitude'],
        'latitude': observation['latitude'],
    }).scalar_one_or_none()
    if existing is None:
        location_id = db.session.execute(text("""
            INSERT INTO location_records (
                name, normalized_name, place_type, area_council, district, latitude,
                longitude, geom, address, aliases, search_keywords, source, source_id,
                source_url, confidence, verification_status, raw_input, usage_count,
                formatted_address, provider_reference, nearest_road, route_resolution,
                first_seen_at, last_seen_at, verified, active, created_at, updated_at
            ) VALUES (
                :name, :normalized, :place_type, :council, :district, :latitude,
                :longitude, ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326),
                :raw_input, '[]'::json, '[]'::json, 'user_submitted', :source_id,
                NULL, :confidence, 'verified', :raw_input, :usage_count,
                :formatted_address, :provider_reference, :nearest_road, :route_resolution,
                :first_seen, :last_seen, TRUE, TRUE, NOW(), NOW()
            ) RETURNING id
        """), {
            'name': name, 'normalized': normalized, 'place_type': place_type,
            'council': observation['area_council'], 'district': observation['district'],
            'latitude': observation['latitude'], 'longitude': observation['longitude'],
            'raw_input': observation['raw_input'], 'source_id': f"observation:{observation_id}",
            'formatted_address': observation.get('formatted_address'),
            'provider_reference': observation.get('provider_reference'),
            'nearest_road': observation.get('nearest_road'),
            'route_resolution': observation.get('route_resolution'),
            'confidence': observation['confidence'] or 0.5,
            'usage_count': observation['usage_count'], 'first_seen': observation['first_seen_at'],
            'last_seen': observation['last_seen_at'],
        }).scalar_one()
    else:
        location_id = existing
        db.session.execute(text("""
            UPDATE location_records SET
                usage_count = usage_count + :usage_count,
                first_seen_at = LEAST(COALESCE(first_seen_at, :first_seen), :first_seen),
                last_seen_at = GREATEST(COALESCE(last_seen_at, :last_seen), :last_seen),
                verification_status = 'verified', verified = TRUE, updated_at = NOW()
            WHERE id = :id
        """), {'usage_count': observation['usage_count'], 'first_seen': observation['first_seen_at'],
               'last_seen': observation['last_seen_at'], 'id': location_id})
    db.session.execute(text("""
        INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
        VALUES (:id, :alias, :normalized_alias, 'user_submitted')
        ON CONFLICT (location_id, normalized_alias) DO NOTHING
    """), {'id': location_id, 'alias': observation['raw_input'],
          'normalized_alias': normalize_location_name(observation['raw_input'])})
    db.session.execute(text("""
        UPDATE location_observations SET status = 'promoted' WHERE id = :id
    """), {'id': observation_id})
    db.session.commit()
    clear_location_search_cache()
    return {'status': 'promoted', 'location_id': location_id}


def add_location_alias(location_id, alias, *, source_name='admin'):
    alias = str(alias or '').strip()[:180]
    normalized = normalize_location_name(alias)
    if not alias or not normalized:
        raise ValueError('Alias is required.')
    from ..extensions import db
    from ..models import LocalPlace

    if db.engine.dialect.name == 'postgresql':
        result = db.session.execute(text("""
            INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
            SELECT id, :alias, :normalized, :source FROM location_records WHERE id = :id AND active
            ON CONFLICT (location_id, normalized_alias) DO NOTHING
            RETURNING id
        """), {'id': location_id, 'alias': alias, 'normalized': normalized, 'source': source_name})
        if result.scalar_one_or_none() is None:
            exists = db.session.execute(text('SELECT 1 FROM location_records WHERE id=:id AND active'), {'id': location_id}).first()
            if not exists:
                raise LookupError('Canonical location was not found.')
        db.session.commit()
        clear_location_search_cache()
        return
    place = LocalPlace.query.filter_by(id=location_id, active=True).first()
    if place is None:
        raise LookupError('Canonical location was not found.')
    aliases = [item for item in re.split(r'[,;|]', place.aliases or '') if item.strip()]
    if normalize_location_name(alias) not in {normalize_location_name(item) for item in aliases}:
        aliases.append(alias)
    place.aliases = '|'.join(aliases)
    db.session.commit()
    clear_location_search_cache()


def merge_local_places(source_id, target_id, *, maximum_distance_m=250):
    from ..extensions import db
    from ..models import Booking, LocalPlace

    if source_id == target_id:
        raise ValueError('Choose two different locations.')
    source = LocalPlace.query.filter_by(id=source_id, active=True).with_for_update().first()
    target = LocalPlace.query.filter_by(id=target_id, active=True).with_for_update().first()
    if source is None or target is None:
        raise LookupError('Both active local locations must exist.')
    if normalize_location_name(source.area_council) != normalize_location_name(target.area_council):
        raise ValueError('Locations in different Area Councils cannot be merged.')
    if (source.place_type or '').casefold() != (target.place_type or '').casefold():
        raise ValueError('Locations with different types cannot be merged.')
    source_coordinates = validate_coordinates(source.latitude, source.longitude)
    target_coordinates = validate_coordinates(target.latitude, target.longitude)
    if not source_coordinates or not target_coordinates:
        raise ValueError('Both locations need valid coordinates before merging.')
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1 = map(radians, source_coordinates)
    lat2, lon2 = map(radians, target_coordinates)
    haversine = 2 * 6371000 * asin(sqrt(sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2))
    if haversine > maximum_distance_m:
        raise ValueError('Locations are too far apart to merge safely.')
    aliases = [item.strip() for item in re.split(r'[,;|]', target.aliases or '') if item.strip()]
    aliases.append(source.name)
    aliases.extend(item.strip() for item in re.split(r'[,;|]', source.aliases or '') if item.strip())
    unique_aliases = {}
    for alias in aliases:
        unique_aliases.setdefault(normalize_location_name(alias), alias)
    target.aliases = '|'.join(unique_aliases.values())
    target.search_keywords = '|'.join(filter(None, (target.search_keywords or '', source.search_keywords or '')))
    for booking in Booking.query.filter_by(pickup_local_place_id=source.id).all():
        booking.pickup_local_place_id = target.id
    for booking in Booking.query.filter_by(destination_local_place_id=source.id).all():
        booking.destination_local_place_id = target.id
    source.active = False
    source.parent_id = target.id
    db.session.commit()
    clear_location_search_cache()
    return target.id


def merge_canonical_locations(source_id, target_id, *, maximum_distance_m=250):
    from ..extensions import db

    if source_id == target_id:
        raise ValueError('Choose two different locations.')
    if db.engine.dialect.name != 'postgresql':
        raise RuntimeError('Canonical PostGIS merge requires PostgreSQL/PostGIS.')
    with db.session.begin_nested():
        rows = db.session.execute(text("""
            SELECT id, name, place_type, area_council, verification_status
            FROM location_records
            WHERE id IN (:source, :target) AND active IS TRUE
            ORDER BY id FOR UPDATE
        """), {'source': source_id, 'target': target_id}).mappings().all()
        by_id = {row['id']: row for row in rows}
        source, target = by_id.get(source_id), by_id.get(target_id)
        if source is None or target is None:
            raise LookupError('Both active canonical locations must exist.')
        if source['place_type'] != target['place_type'] or normalize_location_name(source['area_council']) != normalize_location_name(target['area_council']):
            raise ValueError('Only locations of the same type and Area Council may be merged.')
        close_enough = db.session.execute(text("""
            SELECT ST_DWithin(source.geom::geography, target.geom::geography, :distance)
            FROM location_records AS source, location_records AS target
            WHERE source.id = :source AND target.id = :target
        """), {'source': source_id, 'target': target_id, 'distance': maximum_distance_m}).scalar_one()
        if not close_enough:
            raise ValueError('Locations are too far apart to merge safely.')
        db.session.execute(text("""
            INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
            SELECT :target, alias_name, normalized_alias, source_name
            FROM location_aliases WHERE location_id = :source
            ON CONFLICT (location_id, normalized_alias) DO NOTHING
        """), {'source': source_id, 'target': target_id})
        db.session.execute(text("""
            INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
            VALUES (:target, :name, :normalized, 'merged-location')
            ON CONFLICT (location_id, normalized_alias) DO NOTHING
        """), {'target': target_id, 'name': source['name'], 'normalized': normalize_location_name(source['name'])})
        db.session.execute(text("""
            UPDATE location_provenance SET location_id = :target WHERE location_id = :source
        """), {'source': source_id, 'target': target_id})
        db.session.execute(text("""
            UPDATE location_records AS target SET
                confidence = GREATEST(target.confidence, source.confidence),
                usage_count = target.usage_count + source.usage_count,
                first_seen_at = LEAST(COALESCE(target.first_seen_at, source.first_seen_at), COALESCE(source.first_seen_at, target.first_seen_at)),
                last_seen_at = GREATEST(COALESCE(target.last_seen_at, source.last_seen_at), COALESCE(source.last_seen_at, target.last_seen_at)),
                updated_at = NOW()
            FROM location_records AS source
            WHERE target.id = :target AND source.id = :source
        """), {'source': source_id, 'target': target_id})
        db.session.execute(text("""
            UPDATE location_records SET active = FALSE, parent_id = :target, updated_at = NOW()
            WHERE id = :source
        """), {'source': source_id, 'target': target_id})
    db.session.commit()
    clear_location_search_cache()
    return target_id


def _merge_results(results):
    merged = []
    seen = set()
    for result in results:
        normalised = _normalise_result(result)
        key = (
            normalised['formatted_address'],
            round(float(normalised['latitude'] or 0), 4),
            round(float(normalised['longitude'] or 0), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalised)
    return merged


def _result_score(item, query):
    text = f"{item['name']} {item['formatted_address']} {item['area']} {item['city']} {item['state']}".lower()
    query_norm = normalize_query(query).lower()
    score = 0
    if 'abuja' in text:
        score += 60
    if 'fct' in text or 'federal capital territory' in text:
        score += 35
    if query_norm and query_norm in text:
        score += 45
    if query_norm and item['name'].lower() == query_norm:
        score += 25
    if query_norm and query_norm.split()[0] in text:
        score += 15
    if query_norm:
        query_terms = set(query_norm.split())
        matched_terms = sum(1 for term in query_terms if term in text)
        score += matched_terms * 8
    useful_types = {'road', 'street', 'neighbourhood', 'suburb', 'residential', 'building', 'amenity', 'shop', 'commercial', 'estate'}
    result_type = str(item.get('type') or '').lower()
    category = str(item.get('category') or '').lower()
    if result_type in useful_types or category in useful_types:
        score += 12
    if all(item.get(key) for key in ('name', 'area', 'city', 'state', 'country')):
        score += 8
    if any(term in text for term in ['garki', 'wuse', 'maitama', 'asokoro', 'gwarinpa', 'kubwa', 'kuje', 'bwari', 'gwagwalada', 'kwali', 'abaji', 'jikwoyi', 'karu', 'nyanya', 'lugbe', 'zuba']):
        score += 10
    return score


def _search_variants(query):
    variants = []
    seen = set()
    raw = (query or '').strip()
    if not raw:
        return variants

    base = normalize_query(raw)
    candidates = [raw, base]
    if 'abuja' not in raw.lower() and 'fct' not in raw.lower() and 'federal capital' not in raw.lower():
        candidates.append(f'{raw} abuja')
        candidates.append(f'{base} abuja')

    for candidate in candidates:
        candidate = ' '.join((candidate or '').split())
        if not candidate:
            continue
        if candidate not in seen:
            variants.append(candidate)
            seen.add(candidate)

    for candidate in variants[:]:
        for extra in [f'{candidate} fct', f'{candidate} federal capital territory']:
            extra = ' '.join(extra.split())
            if extra and extra not in seen:
                variants.append(extra)
                seen.add(extra)
    return variants[:4]


def search_places(query, session_token=None, latitude=None, longitude=None, limit=8):
    raw_query = (query or '').strip()
    if len(raw_query) < 2:
        return []

    result_limit = max(1, min(int(limit or 8), 10))
    normalized = normalize_location_name(raw_query)
    cache_key = (normalized, result_limit)
    cached = _SEARCH_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _SEARCH_CACHE_TTL:
        return [dict(result) for result in cached[1]]

    local_results = _search_postgis_locations(raw_query, limit=result_limit)
    if not local_results:
        local_results = _search_local_abuja_places(raw_query, limit=result_limit)
    if local_results:
        results = local_results
        for result in results:
            result['search_count'] = int(result.get('search_count') or 0) + 1
    else:
        external_query = raw_query
        if not any(term in raw_query.casefold() for term in ('abuja', 'fct', 'federal capital territory')):
            external_query = f'{raw_query}, Abuja, FCT, Nigeria'
        payload = _photon_request('/api/?q={}'.format(urllib.parse.quote(external_query)), {
            'limit': result_limit,
        }, timeout=2.5)
        results = []
        if isinstance(payload, dict):
            hits = payload.get('hits') or []
            for item in hits:
                result = _photon_to_location(item)
                result['original_query'] = raw_query
                result['normalized_query'] = normalized
                results.append(result)
            results = _merge_results(results)

    _SEARCH_CACHE[cache_key] = (now, [dict(result) for result in results])
    if len(_SEARCH_CACHE) > _SEARCH_CACHE_LIMIT:
        oldest = sorted(_SEARCH_CACHE, key=lambda key: _SEARCH_CACHE[key][0])[:len(_SEARCH_CACHE) - _SEARCH_CACHE_LIMIT]
        for key in oldest:
            _SEARCH_CACHE.pop(key, None)
    return results[:result_limit]


def search_locations(query, limit=8):
    return search_places(query, limit=limit)


def save_customer_location(
    name,
    *,
    latitude,
    longitude,
    source='manual',
    coordinate_precision='unknown',
    address=None,
    city=None,
    district=None,
    area_council=None,
    landmark=None,
    location_type=None,
    confidence=None,
    precision_level=None,
):
    from ..extensions import db
    from ..models import LocalPlace

    normalized_name = normalize_location_name(name)
    if not normalized_name or len(name.strip()) > 160:
        raise ValueError('A valid location name is required.')
    latitude = float(latitude)
    longitude = float(longitude)
    if not validate_coordinates(latitude, longitude):
        raise ValueError('Coordinates must be valid and non-zero.')
    duplicate = LocalPlace.query.filter(
        func.lower(LocalPlace.normalized_name) == normalized_name,
        LocalPlace.active.is_(True),
    ).first()
    nearby = LocalPlace.query.filter(
        LocalPlace.active.is_(True),
        LocalPlace.latitude.isnot(None),
        LocalPlace.longitude.isnot(None),
    ).filter(
        func.abs(LocalPlace.latitude - latitude) < 0.0015,
        func.abs(LocalPlace.longitude - longitude) < 0.0015,
    ).first()
    is_duplicate = False
    if duplicate is None:
        duplicate = nearby
    if duplicate is None:
        duplicate = LocalPlace(
            name=name.strip(), normalized_name=normalized_name,
            place_type=location_type or 'point_of_interest', provider='customer-entered',
            latitude=latitude, longitude=longitude,
            coordinate_precision=coordinate_precision,
            precision_level=precision_level or coordinate_precision,
            verified=False, active=True,
        )
        db.session.add(duplicate)
    else:
        is_duplicate = True
        duplicate.name = name.strip()
        duplicate.normalized_name = normalized_name
        duplicate.provider = 'customer-entered'
        duplicate.verified = False
        duplicate.active = True
        duplicate.coordinate_precision = coordinate_precision
        duplicate.precision_level = precision_level or coordinate_precision
    duplicate.address = address.strip() if address and address.strip() else duplicate.address or name.strip()
    duplicate.city = city or duplicate.city
    duplicate.district = district or duplicate.district
    duplicate.area_council = area_council or duplicate.area_council
    duplicate.landmark = landmark or duplicate.landmark
    duplicate.location_type = location_type or duplicate.location_type
    duplicate.confidence = confidence if confidence is not None else duplicate.confidence
    duplicate.search_count = (duplicate.search_count or 0) + 1
    db.session.commit()
    clear_location_search_cache()
    return {
        'id': duplicate.id, 'name': duplicate.name, 'provider': duplicate.provider,
        'source': duplicate.provider, 'coordinate_precision': duplicate.coordinate_precision,
        'latitude': duplicate.latitude, 'longitude': duplicate.longitude,
        'duplicate': is_duplicate,
    }


def record_location_usage(place_id, action='search'):
    from ..models import LocalPlace
    place = LocalPlace.query.filter_by(id=int(place_id)).first()
    if place is None:
        return False
    if action == 'selection':
        place.selection_count = (place.selection_count or 0) + 1
    elif action == 'booking':
        place.booking_count = (place.booking_count or 0) + 1
    else:
        place.search_count = (place.search_count or 0) + 1
    place.last_used_at = datetime.utcnow()
    return True


def upsert_location_records(records, commit=True):
    from ..extensions import db
    from ..models import LocalPlace

    prepared = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Each location import record must be an object.')
        name = str(record.get('name') or '').strip()
        place_type = str(record.get('place_type') or record.get('type') or 'address').strip().casefold()
        council = str(record.get('area_council') or '').strip()
        normalized = normalize_location_name(name)
        if not name or len(name) > 160 or not normalized:
            raise ValueError('Each imported location needs a name no longer than 160 characters.')
        if place_type not in {'area_council', 'district', 'neighborhood', 'estate', 'landmark', 'road', 'airport', 'hotel', 'school', 'university', 'hospital', 'shopping_centre', 'commercial', 'residential', 'address', 'point_of_interest'}:
            raise ValueError(f'Unsupported location type for {name}.')
        if council not in FCT_AREA_COUNCILS and place_type != 'area_council':
            raise ValueError(f'Choose one of the six FCT Area Councils for {name}.')
        try:
            latitude = float(record.get('latitude'))
            longitude = float(record.get('longitude'))
        except (TypeError, ValueError):
            raise ValueError(f'Valid coordinates are required for {name}.') from None
        if not (FCT_BOUNDS['min_latitude'] <= latitude <= FCT_BOUNDS['max_latitude'] and FCT_BOUNDS['min_longitude'] <= longitude <= FCT_BOUNDS['max_longitude']):
            raise ValueError(f'{name} coordinates fall outside the supported FCT area.')
        prepared.append({**record, 'name': name, 'normalized_name': normalized,
                         'place_type': place_type, 'area_council': council,
                         'latitude': latitude, 'longitude': longitude})

    rows = {}
    for record in prepared:
        council = record['area_council'] or record['name']
        existing = LocalPlace.query.filter(func.lower(LocalPlace.normalized_name) == record['normalized_name']).all()
        place = next((candidate for candidate in existing
                      if normalize_location_name(candidate.area_council) == normalize_location_name(council)), None)
        if place is None and len(existing) == 1:
            place = existing[0]
        if place is None:
            place = LocalPlace(normalized_name=record['normalized_name'], name=record['name'])
            db.session.add(place)
        for duplicate in existing:
            if (duplicate.id != place.id and
                    normalize_location_name(duplicate.area_council) == normalize_location_name(council) and
                    str(duplicate.place_type or '').casefold() == record['place_type']):
                duplicate.active = False
        aliases = record.get('aliases') or []
        keywords = record.get('search_keywords') or record.get('keywords') or []
        place.name = record['name']
        place.normalized_name = record['normalized_name']
        place.place_type = record['place_type']
        place.area_council = record['area_council'] or record['name']
        place.district = record.get('district') or None
        place.neighborhood = record.get('neighborhood') or None
        place.area = record.get('area') or place.neighborhood or place.district or place.name
        place.lga = place.area_council
        place.state = record.get('state') or 'Federal Capital Territory'
        place.country = record.get('country') or 'Nigeria'
        place.address = record.get('address') or None
        place.aliases = '|'.join(str(alias).strip() for alias in aliases if str(alias).strip()) if isinstance(aliases, (list, tuple)) else str(aliases)
        place.search_keywords = '|'.join(str(word).strip() for word in keywords if str(word).strip()) if isinstance(keywords, (list, tuple)) else str(keywords)
        place.popularity_score = max(0.0, min(100.0, float(record.get('popularity_score') or 0)))
        place.latitude = record['latitude']
        place.longitude = record['longitude']
        place.provider = str(record.get('provider') or record.get('source') or 'admin-import')[:50]
        place.provider_place_id = str(record.get('provider_place_id') or f"chigo:{record['normalized_name'].replace(' ', '-')}")[:255]
        place.verified = bool(record.get('verified', True))
        place.active = bool(record.get('active', True))
        rows[record['normalized_name']] = place

    db.session.flush()
    for record in prepared:
        parent_name = normalize_location_name(record.get('parent_name') or '')
        place = rows[record['normalized_name']]
        if parent_name:
            parent = rows.get(parent_name) or LocalPlace.query.filter(
                func.lower(LocalPlace.normalized_name) == parent_name,
                LocalPlace.active.is_(True),
            ).first()
            if parent is None:
                raise ValueError(f"Parent location '{record.get('parent_name')}' for {place.name} was not found.")
            place.parent_id = parent.id
        else:
            place.parent_id = None
    if commit:
        db.session.commit()
    clear_location_search_cache()
    return len(prepared)


def seed_fct_locations():
    return upsert_location_records(FCT_LOCATION_SEED)


def get_place_details(place_id, session_token=None):
    if not place_id:
        return None
    local = get_local_place(place_id)
    if local:
        return local
    place_id_str = str(place_id)
    payload = _nominatim_request('/details', {'osmtype': 'N', 'osmid': place_id_str, 'format': 'jsonv2'})
    if not isinstance(payload, dict):
        return None

    address = payload.get('address') or {}
    display_name = payload.get('display_name') or payload.get('name') or 'Location'
    name = payload.get('name') or display_name.split(',')[0].strip() or 'Location'
    return {
        'provider': 'nominatim',
        'provider_place_id': payload.get('place_id') or place_id,
        'name': name,
        'formatted_address': display_name,
        'latitude': payload.get('lat'),
        'longitude': payload.get('lon'),
        'country': address.get('country') or payload.get('country'),
        'state': address.get('state') or payload.get('state'),
        'city': address.get('city') or address.get('town') or address.get('village') or 'Abuja',
        'area': address.get('suburb') or address.get('neighbourhood') or address.get('city_district') or payload.get('county') or 'Abuja',
        'district': address.get('county') or address.get('state_district'),
        'postal_code': address.get('postcode'),
        'address_components': address,
        'types': payload.get('extratags', {}),
    }


def geocode_address(address):
    query = (address or '').strip()
    if not query:
        return None
    local = _search_local_abuja_places(query, limit=5)
    normalized = normalize_location_name(query)
    for result in local:
        if normalize_location_name(result['name']) == normalized or normalize_location_name(result['formatted_address']) == normalized:
            return result
    payload = _nominatim_request('/search', {'q': query, 'limit': 1, 'countrycodes': 'ng'})
    if not isinstance(payload, list) or not payload:
        return None
    result = _nominatim_to_location(payload[0])
    return {
        'provider': result['provider'],
        'provider_place_id': result['place_id'],
        'formatted_address': result['formatted_address'],
        'latitude': result['latitude'],
        'longitude': result['longitude'],
        'country': result['country'],
        'state': result['state'],
        'city': result['city'],
        'area': result['area'],
        'district': result['area'],
        'postal_code': None,
        'address_components': payload[0].get('address', {}),
    }


def reverse_geocode(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    payload = _nominatim_request('/reverse', {
        'lat': latitude,
        'lon': longitude,
        'zoom': 18,
        'format': 'jsonv2',
        'addressdetails': 1,
    })
    if not isinstance(payload, dict) or not payload:
        return None
    address = payload.get('address') or {}
    return {
        'provider': 'nominatim',
        'provider_place_id': payload.get('place_id'),
        'formatted_address': payload.get('display_name'),
        'latitude': latitude,
        'longitude': longitude,
        'country': address.get('country') or 'Nigeria',
        'state': address.get('state') or 'Federal Capital Territory',
        'city': address.get('city') or address.get('town') or address.get('village') or 'Abuja',
        'area': address.get('suburb') or address.get('neighbourhood') or address.get('city_district') or payload.get('county') or 'Abuja',
        'district': address.get('county') or address.get('state_district'),
        'postal_code': address.get('postcode'),
        'address_components': address,
        'types': payload.get('extratags', {}),
    }


def route_distance(start_latitude, start_longitude, end_latitude, end_longitude, fallback=False):
    start = validate_coordinates(start_latitude, start_longitude)
    end = validate_coordinates(end_latitude, end_longitude)
    if start is None or end is None:
        return None
    start_latitude, start_longitude = start
    end_latitude, end_longitude = end
    route_key = tuple(round(value, 5) for value in (start_latitude, start_longitude, end_latitude, end_longitude))
    cached = _ROUTE_CACHE.get(route_key)
    now = time.monotonic()
    cache_ttl = _ROUTE_CACHE_TTL if cached and cached[1] else 10
    if cached and now - cached[0] < cache_ttl:
        return dict(cached[1]) if cached[1] else None

    payload = _osrm_request(
        f"/route/v1/driving/{start_longitude},{start_latitude};{end_longitude},{end_latitude}",
        {'geometries': 'geojson'},
    )
    if not isinstance(payload, dict):
        if fallback:
            latitude_delta = math.radians(start_latitude - end_latitude)
            longitude_delta = math.radians(start_longitude - end_longitude)
            latitude_average = math.radians((start_latitude + end_latitude) / 2)
            haversine = 111.195 * math.sqrt(
                latitude_delta ** 2
                + longitude_delta ** 2 * math.cos(latitude_average) ** 2
            )
            result = {
                'provider': 'approximate',
                'source': 'approximate',
                'distance_source': 'approximate',
                'distance_precision': 'approximate',
                'distance_km': round(max(0.1, haversine * 1.15), 2),
                'duration_minutes': round(max(1, haversine * 0.32), 2),
                'distance_meters': round(max(100, haversine * 1150), 0),
                'duration_seconds': round(max(60, haversine * 19), 0),
                'approximate': True,
            }
            _ROUTE_CACHE[route_key] = (now, result)
            return dict(result)
        _ROUTE_CACHE[route_key] = (now, None)
        return None

    routes = payload.get('routes') or []
    if not routes:
        _ROUTE_CACHE[route_key] = (now, None)
        return None
    route = routes[0]
    distance_km = round(float(route.get('distance', 0)) / 1000.0, 2)
    duration_minutes = round(float(route.get('duration', 0)) / 60.0, 2)
    if distance_km <= 0 or duration_minutes < 0:
        _ROUTE_CACHE[route_key] = (now, None)
        return None
    result = {
        'provider': 'osrm',
        'source': 'osrm',
        'distance_source': 'osrm-road-network',
        'distance_precision': 'street',
        'distance_km': distance_km,
        'duration_minutes': duration_minutes,
        'distance_meters': float(route.get('distance', 0)),
        'duration_seconds': float(route.get('duration', 0)),
    }
    _ROUTE_CACHE[route_key] = (now, result)
    return dict(result)


def validate_location(location):
    if not isinstance(location, dict):
        return 'INVALID_LOCATION'
    latitude = location.get('latitude')
    longitude = location.get('longitude')
    if latitude is None or longitude is None:
        return 'INVALID_LOCATION'
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return 'INVALID_LOCATION'

    if (FCT_BOUNDS['min_latitude'] <= latitude <= FCT_BOUNDS['max_latitude'] and
            FCT_BOUNDS['min_longitude'] <= longitude <= FCT_BOUNDS['max_longitude']):
        return 'SERVICEABLE'
    return 'OUTSIDE_SERVICE_AREA'


def validate_service_area(latitude, longitude):
    return validate_location({'latitude': latitude, 'longitude': longitude})
