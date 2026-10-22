"""Fetch, parse, reconcile, and safely import FCT postal/village directories.

Dry-run is the default. Use --apply only after reviewing the JSON report.
"""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WIKI_TITLE = 'List_of_villages_in_the_Federal_Capital_Territory,_Nigeria'
WIKI_ARTICLE_URL = f'https://en.wikipedia.org/wiki/{WIKI_TITLE}'
WIKI_API_URL = 'https://en.wikipedia.org/w/api.php'
ZIP_ROOT = 'https://www.zipcode.com.ng/2021/08/abuja-fct-postal-code.html'
ZIP_SEEDS = (
    ZIP_ROOT,
    'https://www.zipcode.com.ng/2021/08/abaji-lga-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/abuja-municipal-amac-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/bwari-lga-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/gwagwalada-lga-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/kuje-lga-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/kwali-lga-postal-code.html',
    'https://www.zipcode.com.ng/2021/08/abuja-town-postal-code.html',
    'https://www.zipcode.com.ng/2019/11/abaji-district.html',
    'https://www.zipcode.com.ng/2019/11/yaba-district.html',
    'https://www.zipcode.com.ng/2022/05/gwarinpa-village-postal-code.html',
    'https://www.zipcode.com.ng/2022/05/lugbe-postal-code.html',
)
COUNCILS = {
    'abuja municipal area council': 'Abuja Municipal Area Council',
    'abuja municipal': 'Abuja Municipal Area Council', 'amac': 'Abuja Municipal Area Council',
    'bwari area council': 'Bwari Area Council', 'bwari': 'Bwari Area Council',
    'gwagwalada area council': 'Gwagwalada Area Council', 'gwagwalada': 'Gwagwalada Area Council',
    'kuje area council': 'Kuje Area Council', 'kuje': 'Kuje Area Council',
    'kwali area council': 'Kwali Area Council', 'kwali': 'Kwali Area Council',
    'abaji area council': 'Abaji Area Council', 'abaji': 'Abaji Area Council',
}
SIX_COUNCILS = tuple(sorted(set(COUNCILS.values())))
ZIP_CODE_RE = re.compile(r'(?<!\d)(\d{6})(?!\d)')
POSTAL_PATH_RE = re.compile(r'/(?:20\d\d)/(?:\d\d)/[^?#]+(?:postal-code|district|village)[^?#]*\.html$', re.I)
STATE_SLUG_RE = re.compile(r'(?:state-postal|postal-codes?-in-|-state-postal)', re.I)
NON_FCT_STATE_SLUGS = frozenset({
    'abia', 'adamawa', 'akwa-ibom', 'anambra', 'bauchi', 'bayelsa', 'benue', 'borno',
    'cross-river', 'delta', 'ebonyi', 'edo', 'ekiti', 'enugu', 'gombe', 'imo', 'jigawa',
    'kaduna', 'kano', 'katsina', 'kebbi', 'ogun', 'ondo', 'osun', 'oyo', 'plateau',
    'rivers', 'sokoto', 'taraba', 'yobe', 'zamfara', 'nassarawa', 'nasarawa', 'niger', 'kogi',
})
USER_AGENT = 'ChigoFctLocationImporter/1.0 (contact: support@chigomove.online)'
LOG = logging.getLogger('chigo.fct_location_import')


def normalize_name(value: str) -> str:
    value = html.unescape(str(value or '')).casefold().replace('&', ' and ')
    value = value.replace('’', "'").replace('–', '-').replace('—', '-')
    value = re.sub(r'[^a-z0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def canonical_council(value: str | None) -> str | None:
    normalized = normalize_name(value)
    exact = COUNCILS.get(normalized)
    if exact:
        return exact
    for alias, canonical in sorted(COUNCILS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf'\b{re.escape(alias)}\b', normalized):
            return canonical
    return None


def _slug(value: str) -> str:
    return normalize_name(value).replace(' ', '-')


def _source_identifier(record: dict) -> str:
    stable = '|'.join((record['place_type'], record['area_council'], record['parent_name'] or '', record['normalized_name']))
    suffix = hashlib.sha256(stable.encode()).hexdigest()[:20]
    return f'fct-directory:{record["place_type"]}:{suffix}'


def _clean_wiki(value: str) -> str:
    value = re.sub(r'<ref\b[^>]*>.*?</ref\s*>', '', value, flags=re.I | re.S)
    value = re.sub(r'<ref\b[^>]*/\s*>', '', value, flags=re.I)
    value = re.sub(r'\{\{[^{}]*\}\}', '', value)
    value = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', value)
    value = re.sub(r'\[\[([^\]]+)\]\]', r'\1', value)
    value = re.sub(r'\[https?://[^ ]+ ([^\]]+)\]', r'\1', value)
    value = re.sub(r"'''?", '', value)
    value = re.sub(r'<[^>]+>', '', value)
    return html.unescape(value).strip()


def _wiki_table_rows(section: str) -> list[list[str]]:
    tables = []
    table = None
    row = None
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if line.startswith('{|'):
            table = []
            row = None
        elif line.startswith('|}'):
            if table is not None:
                if row:
                    table.append(row)
                tables.append(table)
            table = None
            row = None
        elif table is None:
            continue
        elif line.startswith('|-'):
            if row:
                table.append(row)
            row = []
        elif line.startswith('!'):
            if row is None:
                row = []
            row.extend(_clean_wiki(cell) for cell in re.split(r'!!', line[1:]))
        elif line.startswith('|'):
            if row is None:
                row = []
            content = line[1:]
            content = re.sub(r'^\s*(?:rowspan|colspan)\s*=\s*"?\d+"?\s*\|', '', content, flags=re.I)
            row.extend(_clean_wiki(cell) for cell in re.split(r'\|\|', content))
    flattened = []
    for rows in tables:
        flattened.extend(rows)
    return [[cell.strip() for cell in row] for row in flattened if any(cell.strip() for cell in row)]


def parse_wikipedia_wikitext(wikitext: str, article_url: str = WIKI_ARTICLE_URL) -> tuple[list[dict], list[dict]]:
    postal_section = re.split(r'^==\s*By electoral ward\s*==?', wikitext, maxsplit=1, flags=re.I | re.M)[0]
    postal_section = re.split(r'^==\s*By postal code\s*==?', postal_section, maxsplit=1, flags=re.I | re.M)[-1]
    postal_rows = _wiki_table_rows(postal_section)
    records = []
    current_council = None
    for cells in postal_rows:
        if len(cells) == 3 and current_council:
            council_value = current_council
            district, postcode_cell, settlements = cells
        elif len(cells) >= 4:
            council_value, district, postcode_cell, settlements = cells[:4]
        else:
            continue
        inferred_council = canonical_council(council_value)
        if inferred_council:
            current_council = inferred_council
        council = inferred_council or current_council
        if not council or not district or not settlements:
            continue
        codes = ZIP_CODE_RE.findall(postcode_cell)
        if len(codes) != 1:
            continue
        for raw_name in settlements.split(';'):
            raw_name = _clean_wiki(raw_name).strip(' .\t')
            if not raw_name or len(raw_name) > 180:
                continue
            records.append(_record(raw_name, council, district, codes[0], 'village', 'wikipedia-fct-postal', article_url, raw_name))

    ward_section = re.split(r'^==\s*By electoral ward\s*==?', wikitext, maxsplit=1, flags=re.I | re.M)
    review_candidates = []
    if len(ward_section) > 1:
        ward_text = re.split(r'^==\s*References\s*==?', ward_section[1], maxsplit=1, flags=re.I | re.M)[0]
        ward_rows = _wiki_table_rows(ward_text)
        current_council = None
        for cells in ward_rows:
            if len(cells) < 3:
                continue
            council_value, ward_name, units = cells[:3]
            inferred = canonical_council(council_value)
            if inferred:
                current_council = inferred
            council = inferred or current_council
            if not council or not ward_name or not units:
                continue
            for unit in units.split(';'):
                raw = _clean_wiki(unit).strip(' .\t')
                if not raw:
                    continue
                name, separator, detail = raw.partition('/')
                name = name.strip()
                if name:
                    review_candidates.append(_record(
                        name, council, ward_name, None, 'facility_candidate',
                        'wikipedia-fct-electoral-ward', article_url, raw,
                        active=False, verification_status='needs_review',
                        extra={'ward_description': detail.strip() if separator else None},
                    ))
    return records, review_candidates


def _record(name, council, district, postal_code, place_type, source_name, source_url,
            raw_name, *, active=True, verification_status='source_listed', extra=None):
    council_name = canonical_council(council)
    return {
        'name': name.strip(), 'normalized_name': normalize_name(name),
        'raw_source_name': raw_name.strip(), 'place_type': place_type,
        'area_council': council_name, 'district': district.strip() if district else None,
        'parent_name': None if place_type == 'area_council' else (
            council_name if place_type == 'district' else
            district.strip() if district and normalize_name(district) != normalize_name(council_name or '') else council_name
        ),
        'postal_code': postal_code, 'source_name': source_name, 'source_url': source_url,
        'source_identifier': None, 'aliases': [], 'latitude': None, 'longitude': None,
        'verification_status': verification_status, 'active': active,
        'source_attributes': extra or {},
    }


class _DirectoryHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table_contexts = []
        self.links = []
        self.title_parts = []
        self.headings = []
        self._table_depth = 0
        self._table = None
        self._row = None
        self._cell = None
        self._title = False
        self._heading = False
        self._heading_parts = []
        self._heading_level = None
        self._current_headings = []
        self._table_heading_snapshot = []
        self._active_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self._title = True
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self._heading = True
            self._heading_level = tag
            self._heading_parts = []
        if tag == 'a' and attrs.get('href'):
            self.links.append((attrs['href'], []))
            self._active_link = len(self.links) - 1
        if tag == 'table':
            if self._table_depth == 0:
                self._table_heading_snapshot = list(self._current_headings[-4:])
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif self._table_depth == 1 and tag == 'tr':
            self._row = []
        elif self._table_depth == 1 and tag in {'td', 'th'}:
            self._cell = []

    def handle_data(self, data):
        text = ' '.join(data.split())
        if not text:
            return
        if self._title:
            self.title_parts.append(text)
        if self._heading:
            self._heading_parts.append(text)
        if self._cell is not None:
            self._cell.append(text)
        if self._active_link is not None:
            self.links[self._active_link][1].append(text)

    def handle_endtag(self, tag):
        if tag == 'title':
            self._title = False
        if tag in {'h1', 'h2', 'h3', 'h4'} and self._heading:
            heading = ' '.join(self._heading_parts).strip()
            if heading:
                self.headings.append((self._heading_level, heading))
                self._current_headings.append((self._heading_level, heading))
            self._heading = False
            self._heading_parts = []
        if self._table_depth == 1 and tag in {'td', 'th'} and self._cell is not None:
            self._row.append(' '.join(self._cell).strip())
            self._cell = None
        elif self._table_depth == 1 and tag == 'tr' and self._row is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        if tag == 'table' and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                if self._table:
                    self.tables.append(self._table)
                    self.table_contexts.append(list(self._table_heading_snapshot))
                self._table = None
        if tag == 'a' and self._active_link is not None:
            href, parts = self.links[self._active_link]
            self.links[self._active_link] = (href, ' '.join(parts).strip())
            self._active_link = None


def _postcode(cell_text):
    codes = ZIP_CODE_RE.findall(str(cell_text or ''))
    return codes[0] if len(codes) == 1 else None


def _guess_type(name: str, context: str) -> str:
    value = normalize_name(name)
    if any(re.search(pattern, value) for pattern in (r'\b(st|street|road|rd|way|avenue|ave|crescent|cr|close|drive|dr|lane|ln|expressway)\b',)):
        return 'street'
    if any(token in normalize_name(context) for token in ('postal facility', 'post office', 'facility')):
        return 'post_office'
    return 'settlement'


def parse_postal_html(content: str, page_url: str) -> list[dict]:
    parser = _DirectoryHTMLParser()
    parser.feed(content)
    title = ' '.join(parser.title_parts).strip()
    council_hint = canonical_council(f'{title} {page_url}')
    heading_context = ''
    records = []
    for table_index, table in enumerate(parser.tables):
        if not table:
            continue
        table_headings = parser.table_contexts[table_index] if table_index < len(parser.table_contexts) else []
        key_values = {normalize_name(row[0]): row[1].strip() for row in table if len(row) >= 2 and row[0].strip()}
        detail_name = key_values.get('name')
        detail_code = _postcode(key_values.get('postal code') or key_values.get('postcode') or '')
        if detail_name and detail_code and normalize_name(detail_name) not in {'fct abuja', 'abuja fct', 'federal capital territory'}:
            council = canonical_council(key_values.get('lga') or key_values.get('area council') or key_values.get('council') or title) or council_hint
            if council:
                district = key_values.get('district') or key_values.get('area')
                records.append(_record(
                    detail_name, council, district, detail_code,
                    _guess_type(detail_name, key_values.get('type', '')),
                    'zipcode.com.ng', page_url, detail_name,
                    extra={'page_title': title, 'key_value_table': key_values},
                ))
            continue
        header_row = table[0]
        header_norm = [normalize_name(cell) for cell in header_row]
        has_header = any(any(word in cell for word in ('postal code', 'postcode', 'zip code', 'place', 'district', 'lga', 'area council')) for cell in header_norm)
        headers = header_norm if has_header else []
        rows = table[1:] if has_header else table
        table_headings = parser.table_contexts[table_index] if table_index < len(parser.table_contexts) else []
        for row in rows:
            cells = [re.sub(r'\s+', ' ', cell).strip() for cell in row if cell and cell.strip()]
            if not cells:
                continue
            postal_idx = next((i for i, header in enumerate(headers) if any(key in header for key in ('postal code', 'postcode', 'zip code'))), None)
            name_idx = next((i for i, header in enumerate(headers) if any(key in header for key in ('name', 'place', 'street', 'village', 'settlement', 'location'))), 0)
            council_idx = next((i for i, header in enumerate(headers) if any(key in header for key in ('lga', 'area council', 'council'))), None)
            district_idx = next((i for i, header in enumerate(headers) if 'district' in header or header in {'parent district', 'parent area'}), None)
            type_idx = next((i for i, header in enumerate(headers) if header in {'type', 'classification'}), None)
            candidate_name = cells[name_idx] if name_idx < len(cells) else ''
            if normalize_name(candidate_name) in {
                'name', 'place', 'street', 'district', 'postal code', 'postcode',
                'lga', 'area council', 'state', 'country', 'type', 'facility', 'location',
            }:
                continue
            if normalize_name(candidate_name) in {'lga area council', 'federal capital territory abuja', 'abuja fct', 'postcode nipost'}:
                continue
            code = _postcode(cells[postal_idx]) if postal_idx is not None and postal_idx < len(cells) else None
            if code is None:
                code = next((_postcode(cell) for cell in cells if _postcode(cell)), None)
            council_value = cells[council_idx] if council_idx is not None and council_idx < len(cells) else None
            council = canonical_council(council_value) or council_hint
            district = cells[district_idx] if district_idx is not None and district_idx < len(cells) else None
            if district and normalize_name(district) == normalize_name(candidate_name):
                district = None
            if not district:
                district = next((heading for level, heading in reversed(table_headings)
                                 if level in {'h2', 'h3', 'h4'} and normalize_name(heading) not in {
                                     'state', 'postal facility', 'district list', 'code list',
                                     'lga area council', 'lga', 'area council', 'area list',
                                     'district', 'postcode', 'postal code', 'abuja fct',
                                 }), None)
            if normalize_name(district or '') in {'lga area council', 'lga', 'area council', 'place postcode', 'name federal capital territory'}:
                district = None
            if normalize_name(district or '') in {'post office', 'postal facility', 'facility'}:
                district = None
            if not candidate_name or len(candidate_name) > 180 or not council:
                continue
            context = ' '.join([*(heading for _level, heading in table_headings),
                                cells[type_idx] if type_idx is not None and type_idx < len(cells) else ''])
            place_type = _guess_type(candidate_name, context)
            if type_idx is not None and type_idx < len(cells):
                declared_type = normalize_name(cells[type_idx])
                if declared_type in {'facility', 'post office'}:
                    place_type = 'post_office'
                elif declared_type in {'rural', 'village', 'settlement'}:
                    place_type = 'settlement'
            record = _record(candidate_name, council, district, code, place_type,
                             'zipcode.com.ng', page_url, candidate_name)
            record['source_attributes'] = {'page_title': title, 'table_row': cells, 'headers': headers}
            records.append(record)
    return records


def _wiki_wikitext_url():
    query = urlencode({'action': 'parse', 'page': WIKI_TITLE, 'prop': 'wikitext', 'format': 'json', 'formatversion': 2})
    return f'{WIKI_API_URL}?{query}'


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / f'{hashlib.sha256(url.encode()).hexdigest()}.source'


def _fetch(url: str, cache_dir: Path, *, force=False) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path(cache_dir, url)
    if cache_path.exists() and not force:
        return cache_path.read_bytes()
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/json'})
    with urlopen(request, timeout=40) as response:
        payload = response.read()
    cache_path.write_bytes(payload)
    return payload


def _eligible_link(base_url: str, href: str, label: str, *, allow_fct_child=False) -> str | None:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.netloc.casefold() != 'www.zipcode.com.ng':
        return None
    path = parsed.path
    if 'zipcode.com.ng/' in path.casefold():
        return None
    if not POSTAL_PATH_RE.search(path) or STATE_SLUG_RE.search(path):
        return None
    normalized_path = normalize_name(path)
    if any(normalize_name(state) in normalized_path for state in NON_FCT_STATE_SLUGS):
        return None
    if any(token in path.casefold() for token in ('fct-postal-code', 'abuja-fct-postal-code')):
        return None
    slug_text = normalize_name(label + ' ' + path)
    fct_tokens = ('abaji', 'abuja municipal', 'municipal amac', 'amac', 'bwari', 'gwagwalada', 'kuje', 'kwali',
                  'abuja town', 'gwarinpa', 'wuse 2', 'lugbe', 'kubwa')
    if not allow_fct_child and not any(token in slug_text for token in fct_tokens):
        return None
    if not re.search(r'(postal code|postal-code|district|village)', slug_text):
        return None
    return parsed._replace(query='', fragment='').geturl()


def _discover_postal_pages(seed_urls, cache_dir: Path, *, force=False, max_pages=350):
    queue = [(url, 0) for url in seed_urls]
    seen, fetched, failed = set(), [], []
    records = []
    while queue and len(seen) < max_pages:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            content = _fetch(url, cache_dir, force=force).decode('utf-8', errors='replace')
        except (OSError, URLError, TimeoutError) as error:
            failed.append({'url': url, 'error': str(error)})
            continue
        fetched.append(url)
        records.extend(parse_postal_html(content, url))
        if depth >= 2:
            continue
        parser = _DirectoryHTMLParser()
        parser.feed(content)
        for href, label in parser.links:
            discovered = _eligible_link(url, href, label, allow_fct_child=depth == 1)
            if discovered and discovered not in seen:
                queue.append((discovered, depth + 1))
    return records, {'fetched': fetched, 'failed': failed, 'limit_hit': bool(queue and len(seen) >= max_pages)}


def _get_wikitext(cache_dir: Path, *, force=False):
    payload = json.loads(_fetch(_wiki_wikitext_url(), cache_dir, force=force))
    raw_wikitext = (payload.get('parse') or {}).get('wikitext')
    wikitext = raw_wikitext if isinstance(raw_wikitext, str) else (raw_wikitext or {}).get('*')
    if not isinstance(wikitext, str) or 'By postal code' not in wikitext or 'By electoral ward' not in wikitext:
        raise ValueError('Wikipedia source structure changed; expected postal-code and electoral-ward tables.')
    return wikitext


def _council_counts(records):
    counts = {council: Counter() for council in SIX_COUNCILS}
    for record in records:
        if record.get('area_council') in counts:
            counts[record['area_council']][record['place_type']] += 1
    return {council: dict(sorted(counter.items())) for council, counter in counts.items()}


def _reconcile(records):
    grouped = {}
    duplicates = []
    conflicts = []
    for record in records:
        council = canonical_council(record.get('area_council'))
        if not council:
            conflicts.append({'kind': 'unresolved_area_council', 'record': record})
            continue
        record['area_council'] = council
        record['normalized_name'] = normalize_name(record['name'])
        source_place_type = record['place_type']
        if source_place_type in {'village', 'settlement'}:
            record['place_type'] = 'settlement'
        record['source_identifier'] = _source_identifier(record)
        key = (record['place_type'], council, normalize_name(record.get('parent_name') or ''), record['normalized_name'])
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = record
            existing = record
        else:
            duplicates.append({'key': list(key), 'kept': existing['source_url'], 'duplicate': record['source_url']})
            if record.get('raw_source_name') and record['raw_source_name'] != existing.get('raw_source_name'):
                existing.setdefault('aliases', []).append(record['raw_source_name'])
        postal = record.get('postal_code')
        if postal:
            existing.setdefault('postal_codes_seen', set()).add(postal)
        provenance = {
            'source_name': record['source_name'], 'source_url': record['source_url'],
            'raw_source_name': record['raw_source_name'], 'postal_code': postal,
            'source_place_type': source_place_type,
            'attributes': record.get('source_attributes') or {},
        }
        sources = existing.setdefault('provenance', [])
        if not any(item['source_name'] == provenance['source_name'] and item['source_url'] == provenance['source_url'] and item['raw_source_name'] == provenance['raw_source_name'] for item in sources):
            sources.append(provenance)
    result = list(grouped.values())
    for record in result:
        codes = sorted(record.pop('postal_codes_seen', set()))
        record['postal_code'] = codes[0] if len(codes) == 1 else None
        record['postal_code_conflicts'] = codes if len(codes) > 1 else []
        if len(codes) > 1:
            record['verification_status'] = 'postcode_conflict'
            conflicts.append({'kind': 'postcode_conflict', 'name': record['name'], 'area_council': record['area_council'], 'parent_name': record.get('parent_name'), 'postal_codes': codes})
        record['aliases'] = list(dict.fromkeys(
            alias for alias in record.get('aliases', []) if alias.strip() != record['name']
        ))
    return result, duplicates, conflicts


def _ensure_source_parent_records(records):
    known = {(record['place_type'], record['area_council'], record['normalized_name']) for record in records}
    parents = []
    for council in SIX_COUNCILS:
        key = ('area_council', council, normalize_name(council))
        if key not in known:
            parents.append(_record(council, council, None, None, 'area_council', 'area-council-reference', ZIP_ROOT, council, verification_status='administrative_reference'))
    district_names = {}
    for record in records:
        parent = record.get('parent_name')
        if parent and normalize_name(parent) != normalize_name(record['area_council']):
            district_names[(record['area_council'], normalize_name(parent))] = parent
    for (council, normalized), name in district_names.items():
        key = ('district', council, normalized)
        if key not in known:
            parents.append(_record(name, council, name, None, 'district', 'directory-parent-reference', ZIP_ROOT, name, verification_status='administrative_reference'))
            known.add(key)
    return parents


def _backup_sqlite(database_url: str, cache_dir: Path) -> str | None:
    url = make_url(database_url)
    if url.get_backend_name() != 'sqlite' or url.database in (None, '', ':memory:'):
        return None
    source = Path(url.database).expanduser().resolve()
    if not source.exists():
        return None
    backup_dir = cache_dir / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f'{source.stem}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite3'
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as backup_connection:
        source_connection.backup(backup_connection)
    return str(destination)


def _local_place_import_state(place):
    fields = (
        'name', 'normalized_name', 'place_type', 'parent_id', 'area_council', 'district',
        'area', 'lga', 'state', 'country', 'aliases', 'search_keywords', 'latitude',
        'longitude', 'coordinate_precision', 'provider_place_id', 'source_name',
        'source_url', 'source_identifier', 'raw_source_name', 'postal_code',
        'source_metadata', 'verification_status', 'verified', 'active',
    )
    return tuple(
        json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else value
        for value in (getattr(place, field) for field in fields)
    )


def _import_sqlite(records, *, dry_run, backup_dir):
    from moving_company.extensions import db
    from moving_company.models import LocalPlace

    if dry_run:
        return {'inserted': 0, 'updated': 0, 'unchanged': 0, 'writes': 0, 'backup': None}
    backup = _backup_sqlite(str(db.engine.url), backup_dir)
    counts = Counter()
    imported_places = []
    for record in records:
        source_id = record['source_identifier']
        place = LocalPlace.query.filter_by(source_identifier=source_id).first()
        if place is None:
            candidates = LocalPlace.query.filter_by(normalized_name=record['normalized_name']).all()
            place = next((candidate for candidate in candidates
                          if canonical_council(candidate.area_council) == record['area_council']
                          and (
                              (candidate.place_type or '').casefold() == record['place_type'].casefold()
                              or record['place_type'] == 'settlement'
                              and (candidate.place_type or '').casefold() == 'village'
                          )
                          and normalize_name(candidate.district or '') == normalize_name(record.get('district') or '')), None)
        created = place is None
        before = None if created else _local_place_import_state(place)
        if created:
            place = LocalPlace(
                name=record['name'], normalized_name=record['normalized_name'],
                latitude=None, longitude=None, coordinate_precision='unknown',
            )
            db.session.add(place)
        place.name = place.name if place.verified and not created else record['name']
        place.normalized_name = place.normalized_name if place.verified and not created else record['normalized_name']
        place.place_type = place.place_type if place.verified and not created else record['place_type']
        place.area_council = place.area_council if place.verified and not created else record['area_council']
        place.district = place.district or record.get('district')
        place.area = place.area or record.get('district') or record['name']
        place.lga = place.lga or record['area_council']
        place.state = place.state or 'Federal Capital Territory'
        place.country = place.country or 'Nigeria'
        place.source_identifier = place.source_identifier or source_id
        place.source_name = place.source_name or record['source_name']
        place.source_url = place.source_url or record['source_url']
        place.raw_source_name = place.raw_source_name or record['raw_source_name']
        if not place.postal_code and record.get('postal_code') and not record.get('postal_code_conflicts'):
            place.postal_code = record['postal_code']
        place.verification_status = place.verification_status if place.verified else record['verification_status']
        place.active = place.active if place.verified else record['active']
        if place.provider_place_id is None:
            place.provider_place_id = source_id
        source_metadata = place.source_metadata if isinstance(place.source_metadata, dict) else {}
        source_metadata = dict(source_metadata)
        existing_provenance = source_metadata.get('provenance')
        if not isinstance(existing_provenance, list):
            existing_provenance = []
        provenance_keys = {
            (item.get('source_name'), item.get('source_url'), item.get('raw_source_name'))
            for item in existing_provenance if isinstance(item, dict)
        }
        for provenance in record.get('provenance', []):
            key = (provenance['source_name'], provenance['source_url'], provenance['raw_source_name'])
            if key not in provenance_keys:
                existing_provenance.append(provenance)
                provenance_keys.add(key)
        source_metadata['provenance'] = existing_provenance
        source_metadata['postal_code_conflicts'] = record.get('postal_code_conflicts', [])
        place.source_metadata = source_metadata
        for alias in record.get('aliases', []):
            current_aliases = [item.strip() for item in re.split(r'[,;|]', place.aliases or '') if item.strip()]
            if normalize_name(alias) not in {normalize_name(item) for item in current_aliases}:
                current_aliases.append(alias)
                place.aliases = '|'.join(current_aliases)
        keywords = [item.strip() for item in re.split(r'[,;|]', place.search_keywords or '') if item.strip()]
        for keyword in [record.get('postal_code')]:
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        place.search_keywords = '|'.join(keywords)
        imported_places.append((record, place, created, before))
    db.session.flush()
    for record, place, created, _before in imported_places:
        if created:
            place.latitude = None
            place.longitude = None
            place.coordinate_precision = 'unknown'
        parent_name = normalize_name(record.get('parent_name') or '')
        if not parent_name or place.parent_id or place.verified:
            continue
        candidates = LocalPlace.query.filter_by(normalized_name=parent_name, active=True).all()
        parent = next((candidate for candidate in candidates
                       if canonical_council(candidate.area_council) == record['area_council']
                       and candidate.id != place.id
                       and (candidate.place_type or '').casefold() in {'area_council', 'district', 'community'}), None)
        if parent:
            place.parent_id = parent.id
    for _record_data, place, created, before in imported_places:
        if created:
            counts['inserted'] += 1
        elif before != _local_place_import_state(place):
            counts['updated'] += 1
        else:
            counts['unchanged'] += 1
    db.session.commit()
    return {
        'inserted': counts['inserted'], 'updated': counts['updated'],
        'unchanged': counts['unchanged'], 'writes': counts['inserted'] + counts['updated'],
        'backup': backup,
    }


def _db_url():
    from moving_company.config import Config
    return Config.SQLALCHEMY_DATABASE_URI


def import_to_postgis(records, *, dry_run):
    if dry_run:
        return {'inserted': 0, 'updated': 0, 'unchanged': 0, 'writes': 0}
    engine = create_engine(_db_url(), pool_pre_ping=True)
    if engine.dialect.name != 'postgresql':
        engine.dispose()
        raise RuntimeError('PostGIS import requires PostgreSQL; SQLite imports use LocalPlace.')
    counts = Counter()
    try:
        with engine.begin() as connection:
            source_ids = {}
            source_urls = {record['source_name']: record['source_url'] for record in records}
            for record in records:
                for provenance in record.get('provenance', []):
                    source_urls.setdefault(provenance['source_name'], provenance['source_url'])
            for source_name, source_url in source_urls.items():
                source_ids[source_name] = connection.execute(text('''
                    INSERT INTO location_sources (source_name, source_type, source_url, metadata)
                    VALUES (:name, 'directory', :url, CAST(:metadata AS json))
                    ON CONFLICT (source_name) DO UPDATE SET source_url = EXCLUDED.source_url
                    RETURNING id
                '''), {'name': source_name, 'url': source_url,
                      'metadata': json.dumps({'importer': 'scripts/import_fct_locations.py'})}).scalar_one()
            location_ids = {}
            for record in records:
                existing_id = connection.execute(text('''
                    SELECT id FROM location_records
                    WHERE source = 'fct-directory' AND source_id = :source_id
                '''), {'source_id': record['source_identifier']}).scalar_one_or_none()
                if existing_id is None:
                    existing_id = connection.execute(text('''
                        SELECT id FROM location_records
                        WHERE normalized_name = :name AND place_type = :place_type
                          AND area_council = :council AND COALESCE(parent_name, '') = COALESCE(:parent, '')
                        ORDER BY (verification_status = 'verified') DESC, id
                        LIMIT 1
                    '''), {'name': record['normalized_name'], 'place_type': record['place_type'],
                          'council': record['area_council'], 'parent': record.get('parent_name')}).scalar_one_or_none()
                inserted = existing_id is None
                if inserted:
                    existing_id = connection.execute(text('''
                        INSERT INTO location_records (
                            name, normalized_name, place_type, area_council, district, parent_name,
                            state, country, latitude, longitude, geom, aliases, search_keywords,
                            source, source_id, source_url, raw_input, confidence, verification_status,
                            verified, active, postal_code, raw_source_name, first_seen_at, last_seen_at,
                            created_at, updated_at
                        ) VALUES (
                            :name, :normalized, :type, :council, :district, :parent,
                            'Federal Capital Territory', 'Nigeria', NULL, NULL, NULL,
                            CAST(:aliases AS json), CAST(:keywords AS json), 'fct-directory', :source_id,
                            :source_url, :raw_name, 0.75, :verification_status, FALSE, :active,
                            :postal_code, :raw_name, NOW(), NOW(), NOW(), NOW()
                        ) RETURNING id
                    '''), {
                        'name': record['name'], 'normalized': record['normalized_name'],
                        'type': record['place_type'], 'council': record['area_council'],
                        'district': record.get('district'), 'parent': record.get('parent_name'),
                        'aliases': json.dumps(record.get('aliases') or []),
                        'keywords': json.dumps([record.get('postal_code'), *record.get('postal_code_conflicts', [])]),
                        'source_id': record['source_identifier'], 'source_url': record.get('source_url'),
                        'raw_name': record.get('raw_source_name'),
                        'verification_status': record['verification_status'], 'active': record['active'],
                        'postal_code': record.get('postal_code'),
                    }).scalar_one()
                    counts['inserted'] += 1
                else:
                    counts['updated'] += 1
                    if record.get('postal_code_conflicts'):
                        counts['conflicts'] += 1
                    connection.execute(text('''
                        UPDATE location_records SET postal_code = COALESCE(postal_code, :postal_code),
                            updated_at = NOW()
                        WHERE id = :id AND verification_status <> 'verified'
                    '''), {'id': existing_id, 'postal_code': record.get('postal_code')})
                location_ids[record['source_identifier']] = existing_id
                for provenance in record.get('provenance', []):
                    connection.execute(text('''
                        INSERT INTO location_provenance (location_id, source_id, external_id, source_url, raw_attributes)
                        VALUES (:location_id, :source_id, :external_id, :source_url, CAST(:attrs AS json))
                        ON CONFLICT (source_id, external_id) DO UPDATE SET
                            location_id=EXCLUDED.location_id, source_url=EXCLUDED.source_url,
                            raw_attributes=EXCLUDED.raw_attributes
                    '''), {'location_id': existing_id, 'source_id': source_ids[provenance['source_name']],
                          'external_id': f"{record['source_identifier']}:{hashlib.sha1(provenance['source_url'].encode()).hexdigest()[:10]}",
                          'source_url': provenance['source_url'], 'attrs': json.dumps(provenance)})
                aliases = list(record.get('aliases') or [])
                for alias in aliases:
                    connection.execute(text('''
                        INSERT INTO location_aliases (location_id, alias_name, normalized_alias, source_name)
                        VALUES (:location_id, :alias, :normalized, :source)
                        ON CONFLICT (location_id, normalized_alias) DO NOTHING
                    '''), {'location_id': existing_id, 'alias': alias, 'normalized': normalize_name(alias), 'source': record['source_name']})
            for record in records:
                parent_name = normalize_name(record.get('parent_name') or '')
                if not parent_name:
                    continue
                parent_id = connection.execute(text('''
                    SELECT id FROM location_records
                    WHERE normalized_name = :parent AND area_council = :council AND active IS TRUE
                      AND place_type IN ('area_council', 'district', 'community')
                    ORDER BY CASE WHEN place_type = 'area_council' THEN 0 ELSE 1 END, id
                    LIMIT 1
                '''), {'parent': parent_name, 'council': record['area_council']}).scalar_one_or_none()
                if parent_id:
                    connection.execute(text('''
                        UPDATE location_records SET parent_id = COALESCE(parent_id, :parent),
                            district_id = CASE WHEN :type IN ('village', 'settlement', 'street')
                                               THEN COALESCE(district_id, :parent) ELSE district_id END,
                            updated_at = NOW()
                        WHERE id = :id AND verification_status <> 'verified'
                    '''), {'parent': parent_id, 'type': record['place_type'],
                          'id': location_ids[record['source_identifier']]})
    finally:
        engine.dispose()
    return {'inserted': counts['inserted'], 'updated': counts['updated'], 'unchanged': counts['unchanged'], 'writes': counts['inserted'] + counts['updated'], 'conflicts': counts['conflicts']}


def build_report(zip_records, wiki_records, ward_candidates, page_coverage, *, source_errors):
    parents = _ensure_source_parent_records(zip_records + wiki_records)
    reconciled, duplicate_rows, conflicts = _reconcile(zip_records + wiki_records + parents)
    review_reconciled, review_duplicates, review_conflicts = _reconcile(ward_candidates)
    records = reconciled + review_reconciled
    conflicts.extend(review_conflicts)
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_urls': {'zipcode_root': ZIP_ROOT, 'wikipedia_article': WIKI_ARTICLE_URL, 'wikipedia_api': _wiki_wikitext_url()},
        'coverage': page_coverage,
        'source_errors': source_errors,
        'source_records': {'zipcode.com.ng': len(zip_records), 'wikipedia_postal_table': len(wiki_records), 'wikipedia_electoral_ward_review_candidates': len(ward_candidates)},
        'source_record_counts_by_url': dict(Counter(record['source_url'] for record in zip_records)),
        'records_after_reconciliation': len(records),
        'records_by_area_council_and_type': _council_counts(records),
        'all_six_area_councils_represented': sorted({row['area_council'] for row in records if row['place_type'] == 'area_council'}),
        'records_with_verified_coordinates': sum(row['latitude'] is not None and row['longitude'] is not None for row in records),
        'records_with_postcodes': sum(bool(row.get('postal_code')) for row in records),
        'duplicate_candidates': duplicate_rows + review_duplicates,
        'conflicts_and_unresolved': conflicts,
        'records': records,
    }


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Default behavior; fetch, parse, reconcile, and report without database writes.')
    parser.add_argument('--apply', action='store_true', help='Import reconciled records into the configured existing location store.')
    parser.add_argument('--force-fetch', action='store_true', help='Ignore source cache and fetch pages again.')
    parser.add_argument('--cache-dir', default=str(ROOT / 'data' / 'geodata' / 'fct-directory-cache'))
    parser.add_argument('--report', default=str(ROOT / 'data' / 'geodata' / 'reports' / 'fct-location-import-latest.json'))
    parser.add_argument('--max-postal-pages', type=int, default=350)
    return parser.parse_args(argv)


def main(argv=None):
    args = _args(argv)
    if args.apply and args.dry_run:
        raise SystemExit('Choose either --apply or --dry-run, not both.')
    cache_dir = Path(args.cache_dir).resolve()
    source_errors = []
    zip_records, page_coverage = _discover_postal_pages(
        ZIP_SEEDS, cache_dir / 'zipcode', force=args.force_fetch, max_pages=max(1, args.max_postal_pages),
    )
    try:
        wikitext = _get_wikitext(cache_dir / 'wikipedia', force=args.force_fetch)
        wiki_records, ward_candidates = parse_wikipedia_wikitext(wikitext)
    except Exception as error:
        source_errors.append({'source': WIKI_ARTICLE_URL, 'error': str(error)})
        if not wiki_records_available(cache_dir):
            raise
        wiki_records, ward_candidates = [], []
    report = build_report(zip_records, wiki_records, ward_candidates, page_coverage, source_errors=source_errors)
    dry_run = not args.apply
    report['mode'] = 'dry-run' if dry_run else 'apply'
    report['import_result'] = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'writes': 0, 'backup': None}
    if args.apply:
        from moving_company import create_app
        from moving_company.extensions import db
        from moving_company.config import Config
        app = create_app(testing=False)
        with app.app_context():
            report['import_result'] = _import_sqlite(report['records'], dry_run=False, backup_dir=cache_dir) if db.engine.dialect.name == 'sqlite' else import_to_postgis(report['records'], dry_run=False)
            db.session.remove()
    output = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    summary = {key: value for key, value in report.items() if key != 'records'}
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(f'Full import report: {output}')
    return 0


def wiki_records_available(cache_dir: Path) -> bool:
    return any(cache_dir.glob('*.source'))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
