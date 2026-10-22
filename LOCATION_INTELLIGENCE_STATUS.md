# Location Intelligence Status

Status date: 2026-10-22

## Staging readiness checkpoint (2026-10-22)

### A. Completed

- Re-audited the current service, autocomplete and booking routes, frontend handlers, LocalPlace/PostGIS models, migration graph, current SQLite database, FCT import report/source artifacts, provider adapters, and all 12 test modules.
- Repaired misleading search-failure behavior: canonical PostGIS query/observation SQL errors no longer become successful empty search results; Photon transport failures are distinct from a valid empty response. The autocomplete API returns a generic retryable HTTP 503 without exposing queries, credentials, or raw exceptions.
- Preserved the established `_photon_request` signature after the full suite exposed test/integration callers relying on its original call shape. A private request-failure sentinel keeps Photon transport failure distinguishable.
- Improved SQLite exact/prefix matching to use the existing `(active, normalized_name)` index; no database migration or schema change was required for this optimization.
- Added a read-only diagnostic command with migration, schema, index, data-quality, duplicate-name, council-distribution, provider, endpoint, latency-percentile, and optional safe-log-count checks. It reports its configured database target without credentials and returns nonzero if required health checks fail.
- Added test-only bcrypt cost configuration (`BCRYPT_LOG_ROUNDS=4`) within `create_app(testing=True)` only. Production and normal development defaults remain unchanged; this allowed the unfiltered full suite to finish.
- No staging database was positively identified. No database migration, data import, provider dataset ingestion, charge, partner dispatch, or production deployment was performed.

### B. Database and migrations

- Active configuration: `APP_ENV=development`, backend SQLite, local target `moving_company.db`; `DATABASE_URL` is absent and `USE_POSTGIS=false`. This is not staging configuration.
- Read-only connectivity to the configured SQLite database passed. PostgreSQL, PostGIS, SRID/spatial-function checks are **not verified** because there is no PostgreSQL target. Photon and OSRM public endpoint smoke checks returned HTTP 200; this is reachability evidence only, not licensing, capacity, or contract verification.
- Repository Alembic head: `20261022_fct_location_directory`. Database revision: `20261006_upgrade_booking_location_schema`; 20 application revisions are unapplied according to the repository history. The migration chain is linear, but the local database schema contains later-added LocalPlace columns/indexes despite the older revision, indicating migration/schema drift that must be reconciled before running upgrades.
- Canonical PostGIS tables (`location_records`, `location_aliases`, `location_observations`, `administrative_areas`) are absent in SQLite. No migration was applied. The location migration creates `postgis` and `pg_trgm` extensions, GiST/trigram indexes and canonical tables; the FCT-directory migration adds provenance/postcode columns and makes coordinates/geometry nullable. Later migrations also include data-bearing application areas, and several have downgrade operations that drop data/columns. Review and backup are required before staging upgrade.
- Acceptance status: **blocked**. Required next input: intended staging target via the established secret environment configuration, confirmation that `APP_ENV=staging`, and backup/restore evidence. Then run the read-only diagnostic, inspect the target migration state, review the full upgrade plan, and apply only after approval.

### C. Geographic data

- Actual configured SQLite `local_places`: 1,104 rows total; 1,011 active. Seven local search indexes are present. Among active rows, 50 have valid nonzero coordinates, 961 have missing coordinates, zero are numerically invalid, and zero fall outside the configured FCT bounding rectangle. No active row lacks an area-council value.
- Actual database rows grouped by stored source and type (1,104 total): unknown source 10; `chigo-curated` 47; `directory-parent-reference` 32; `wikipedia-fct-electoral-ward` 77 facility candidates; `wikipedia-fct-postal` 706 settlements; `zipcode.com.ng` 232 (23 post offices, 169 settlements, 40 streets). These database counts are distinct from the source import report's candidate/reconciled counts.
- Active council strings are not canonicalized consistently: `AMAC=1`, `Abuja Municipal Area Council=230`, `Bwari=1`, `Bwari Area Council=75`, `Kuje=1`, `Kuje Area Council=158`, `Gwagwalada Area Council=296`, `Kwali Area Council=147`, and `Abaji Area Council=102`. This totals 1,011. Do not merge or rename solely from this report; reconcile values against authoritative IDs and booking references.
- Duplicate normalized-name audit: 198 active duplicate groups and 220 excess rows. These require conservative review using type, council, source IDs, and coordinates; no merge was performed.
- The FCT directory report describes 1,053 reconciled source entries, 1,047 inserted and 6 updated in its recorded apply run, 916 postcodes, zero verified coordinates, and coverage labels for all six councils. These report numbers are **not** evidence that those rows exist in the current SQLite database. Actual post-import target counts by source/type remain unverified.
- Locally available files: GeoNames Nigeria archive (`NG.zip`, about 1.9 MB), Nigeria OSM PBF (about 677 MB), GeoBoundaries ADM1 (about 2.2 MB) and ADM2 (about 9 MB), and the FCT directory report. No WOF bundle or OpenAddresses dataset was found in the local geodata directory. Existing code provides import paths for OSM, GeoNames, WOF-local-file, and boundaries; no new adapters or imports were attempted before staging readiness.
- No coordinates were fabricated. Address-point coverage is not established; locality/postcode directory entries and settlement centroids are not verified residential addresses. WOF/OpenAddresses coverage, licenses, and attribution still require dataset-specific assessment before use.

### D. Test results

- Complete command: `.venv/bin/python -m pytest -q --tb=short` (executed with the absolute project venv path). **308 passed, 0 failed, 0 skipped, 48,597 warnings**, 113.41 seconds. No test selection filters were used. Warnings are primarily repeated SQLAlchemy/Python deprecations and schema teardown warnings. A second full run with warning bodies suppressed also passed 308/308 in 111.22 seconds.
- Earlier full-suite attempts timed out/interrupted and one pre-fix attempt exposed two Photon signature compatibility failures. Those compatibility tests were fixed and passed; the final unfiltered run above completed all 308 tests and supersedes the earlier partial counts.
- Booking checkpoint: 7 passed, covering location coordinate persistence, route success and provider failure, route retry into quote calculation, residential quote acceptance, cleaning single-location requirements, and conditional storage collection/return locations.
- Earlier 41-test focused location/booking/route run passed. The provider API compatibility tests also passed after the signature was restored.
- JavaScript `node --check` passed for `location-picker.js` and `booking.js`; `git diff --check` passed. Editor diagnostics showed no errors in the touched Python files.
- No browser automation was run. The 308-test suite uses project test clients and mocks; it does not establish browser interaction or staging backend behavior.

### E. Booking verification

- Automated residential workflow test reached quote acceptance and verified persisted quote state/line items. Separate location workflow tests verify selected location metadata and coordinates are persisted. Route tests cover valid OSRM route data, provider unavailability, and server-side retry for a pending route.
- Cleaning test verified a single service location and no destination. Storage tests verified collection and known-return location rules.
- These are SQLite-backed Flask test-client tests with mocked provider operations. No real staging booking, browser journey, payment, partner contact, or production charge was performed. Consequently the end-to-end staging acceptance criterion is **not met**.

### F. Provider integration

- Existing local-first search plus Photon fallback, Nominatim geocoding, and OSRM routing adapters remain in place. No new external dataset integration was started because the required database and booking gates are blocked.
- Latest local diagnostic sample (20 cache-cleared requests): autocomplete p50 6.4 ms, p95 7.5 ms, p99 8.1 ms; direct local matcher p50 5.3 ms, p95 7.0 ms, p99 265.2 ms. These are local SQLite measurements with a small sample, not staging/PostGIS load evidence. Previous matcher p95 values ranged from 6.9 ms to 182.3 ms; tail variation confirms that larger repeated/concurrent measurement is needed before asserting the 200 ms objective.
- Photon and OSRM returned HTTP 200 during the diagnostic smoke checks. No claims about commercial licensing, service capacity, data completeness, or provider uptime are made.

### G. Files changed in this checkpoint

- `moving_company/services/location_service.py`: explicit search/provider failure handling and indexed SQLite exact/prefix predicates.
- `moving_company/routes/locations.py`: safe retryable 503 response for search infrastructure failures.
- `moving_company/__init__.py`: test-only bcrypt rounds to keep the full suite executable within the environment limit.
- `scripts/location_diagnostics.py`: read-only health/data/latency report.
- `tests/test_app.py`: database/provider failure and compatibility regressions.
- `README.md` and this report: diagnostic usage and verified readiness evidence.
- The repository has other pre-existing modified files; this checkpoint did not revert or claim ownership of those unrelated edits.

### H. Outstanding blockers

- Staging credentials/target and backup evidence: configure the intended staging secrets and confirm environment identity; then perform read-only Postgres/PostGIS checks before any migration.
- Migration/schema drift: compare target schema and every migration from its actual revision, produce a reviewed upgrade plan, capture backup evidence, then apply only approved revisions and validate.
- Geographic imports: after staging schema readiness, run each source in dry-run mode, assess licensing/attribution, import transactionally, and verify actual database counts/coverage. Reconcile the 198 local duplicate-name groups conservatively.
- Full browser booking flow: provide a staging URL and safe test account/data; verify selection, backward navigation, persistence, routing, quote, and controlled failure recovery in a real browser. Do not complete payment or contact partners.
- Full production-quality latency: run repeated and concurrent PostGIS benchmarks with P50/P95/P99 and query plans after data is loaded.

### I. Deployment recommendation

**Not ready for staging verification.** The code and full test suite are verified locally, but this workspace has no identified staging database, no PostgreSQL/PostGIS verification, no applied staging migrations/imports, and no browser staging booking evidence. Do not deploy or import until the staging target and recoverable backup are confirmed.

## Completion and repair evidence (2026-10-22)

### Baseline and root causes

- Workspace Python: the configured `.venv` uses Python 3.12.13. Calling the system `pytest` executable selected system Python and failed collection with `ModuleNotFoundError: moving_company`; rerunning as `.venv/bin/python -m pytest` resolved the environment issue.
- Baseline focused suite: 39 passed, 135 deselected across location learning, FCT import, autocomplete, booking location, routing, and quote retry tests.
- Application startup and route registration succeeded using the default local SQLite configuration. `scripts/db_health_check.py` reported `DATABASE_URL` missing; PostgreSQL/PostGIS connectivity is therefore not configured or verified in this workspace.
- Read-only inspection of `moving_company.db`: 1,104 `local_places` rows and seven indexes, including active/normalized-name and postal-code indexes. `location_records`, `location_aliases`, `location_observations`, and `administrative_areas` are absent. The stored Alembic revision is `20261006_upgrade_booking_location_schema`, behind the current repository migration head. SQLite does not provide the canonical PostGIS search tables.
- The available FCT directory import report contains 1,053 reconciled entries, all six councils, 916 postcodes, and zero verified coordinates. These records support locality/postcode matching, not verified street-address coordinates. This import report does not establish that those records were applied to the local database; SQLite reports 1,104 active and inactive local-place rows combined.
- Root cause reproduced by inspection and regression: `_search_postgis_locations` caught SQLAlchemy errors and returned `[]`; a follow-up observations-table error was also hidden. `search_places` then treated the empty result as a normal miss and could continue to LocalPlace/Photon. `_request_json` likewise returned `None` for provider timeouts, indistinguishable from a provider returning no payload. This made infrastructure errors appear to be successful empty searches.

### Repair

- PostGIS search and observations database failures now raise a typed unavailable-search exception instead of falling through as no matches.
- Photon search timeouts/transport failures are distinguished from a valid empty result. The autocomplete API returns HTTP 503 with a stable retryable message and logs only the failure kind; it does not log the query or raw exception. SQLAlchemy errors from other search layers also return the same safe 503 response.
- SQLite exact and prefix matching no longer wraps the indexed `normalized_name` in `lower()`. The inspected query plan showed the old predicate used only the `active` index key; exact matching now uses both keys and SQLite `GLOB` prefix matching uses the composite index range. No index or migration was needed.
- Added `scripts/location_diagnostics.py`, a read-only check for DB connectivity, PostGIS and `pg_trgm`, required tables/columns/indexes, counts, coordinate quality, source provenance, migration revision, importer report, Photon health, actual autocomplete response, optional cache-cleared p95 timing, and safe error counts from an explicitly supplied application log.
- The booking selection path already resolves local-reference IDs server-side and retains provider/location metadata in booking fields; existing route/pricing integration and service-specific address requirements remain unchanged.

### Post-repair validation

- New database-failure and provider-timeout endpoint regressions: 2 passed. Both assert that the user receives a retryable failure rather than a successful empty result; database failure does not invoke local or external fallback.
- JavaScript syntax checks passed for `location-picker.js` and `booking.js`.
- Real endpoint probes: `Gwarinpa` returned HTTP 200 with 8 local-reference suggestions; alternate spelling `Gwarimpa` returned HTTP 200 with 8 results; a unique nonexistent query returned HTTP 200 with 0 results. Photon health returned HTTP 200.
- Before the index predicate repair, the direct local matcher p95 was 221.8 ms and the cache-cleared autocomplete p95 was 260.4 ms over 20 requests. After the repair, direct SQLite matcher p95 was 182.3 ms and cache-cleared endpoint p95 was 8.3 ms over 20 requests. This is a local SQLite measurement, not a production/PostGIS benchmark.
- Four direct failure regressions passed, covering PostGIS SQL error wrapping, provider timeout wrapping, API handling of database errors without fallback, and retryable provider failure responses. The final focused location/booking/route suite passed **41 tests** (137 deselected) in 24.92 seconds; the earlier pre-change selection passed 39 tests.
- `node --check` passed for `location-picker.js` and `booking.js`; editor diagnostics show no errors in the modified Python files. Startup and route registration succeeded under default local SQLite configuration.
- Full repository suite: 289 passed, 2 failed, then pytest was interrupted during a slow unrelated test after 216.19 seconds. The two failures were the existing Photon fallback and default-host tests, initially caused by a temporary `_photon_request` signature incompatibility in this repair. That compatibility issue was fixed, and both tests passed in the subsequent six-test provider/database validation run. The entire repository suite was not rerun to completion after that fix, so a clean whole-suite result is not claimed.

### Deployment and rollback

1. Back up the target PostgreSQL database and review the schema changes already present in the release branch. Configure `DATABASE_URL`/`USE_POSTGIS` and provider URLs in the hosting environment; do not copy credentials into repository files.
2. In staging, apply the repository's additive migrations with `flask --app run.py db upgrade`, then run `python scripts/location_diagnostics.py --benchmark 20`. Confirm PostGIS, canonical tables/indexes, import counts, and representative search results before production. The current workspace did not verify these steps against PostgreSQL.
3. Deploy the code and confirm the same diagnostic plus booking creation, editing, route calculation, and quote recalculation in staging before rollout.
4. For code rollback, restore the previous successful application release and run diagnostics. Do not downgrade the location-directory migration: it is intentionally irreversible and rollback must not delete imported location data. Database rollback requires a reviewed backup restore, not table recreation.

## Repository audit

The application already has one location blueprint at `/api/locations/autocomplete` (with `/api/locations/search` as a compatibility alias), shared `search_places` logic, SQLite `LocalPlace` support, a PostGIS canonical store, import utilities, and booking/location regression coverage. The booking route persists selected place/provider references and uses the existing route-distance service for quote calculations. The six FCT councils are represented in the curated seed data.

The autocomplete service is local-first. It searches PostGIS when configured, then local curated records; the current external place-discovery fallback is Photon, called only when local search has no results. The service also maintains a bounded in-process result cache. The legacy `location-picker.js` had stale-response protection but did not abort obsolete fetches and used a 300 ms debounce. The response adapter spread the full internal search object into JSON, and inferred `approximate` precision merely from the presence of coordinates.

## Changes in this update

- `moving_company/routes/locations.py`: replaced the broad result spread with an explicit public projection. Existing picker fields, canonical IDs, provider references, display/address values, and query context remain available. Internal ranking or provider payload fields are not serialized. Address objects are restricted to recognized public address components. Coordinate presence is checked with the existing coordinate validator; unknown precision stays `unknown` instead of being inferred.
- `moving_company/static/js/location-picker.js`: aborts a superseded autocomplete request using `AbortController` and reduces the debounce interval to 200 ms. Existing request-sequence protection remains.
- `tests/test_app.py`: verifies legacy fields are retained and arbitrary internal score/provider payload fields are excluded.

The API accepts `q` or `query`, `limit` (1–10), optional `area_council`, and optional paired `lat`/`lon`; query length is bounded to 160 and search requires at least two characters. It returns `success` and `results`, including the stable fields `feature_type`, `state_or_territory`, `parent_name`, `address_components`, `precision`, `source_summary`, `verified`, and `requires_confirmation`, plus the legacy selection fields used by current booking and home-decor pickers.

## Data and source status

- Chigo curated FCT seeds: implemented and used by local search. Their coordinates are curated application data; a seed coordinate does not establish address-point precision.
- FCT postal/village directory: importer implemented. The generated report at `data/geodata/reports/fct-location-import-latest.json` records an apply run on 2026-10-20: 1,053 reconciled records, 1,047 inserted, 6 updated, 0 unchanged, and all six councils represented. Reported counts by council/type are in that JSON. It lists 916 records with postcodes and 0 with verified coordinates. These are directory/locality records, not verified street addresses.
- OpenStreetMap: a 677 MB Nigeria PBF is present and a parser/import path exists in `moving_company/services/postgis_ingestion.py`. The parse-only scan reached 27 million objects, then was interrupted and exited abnormally; no complete OSM coverage count is available. No PostGIS import is verified.
- GeoNames: the Nigeria `NG.zip` archive is present and the importer is implemented. The importer now uses GeoNames feature-class and feature-code columns, retains source/admin metadata, and maps each coordinate to its containing FCT council boundary. A no-write dry run parsed 76,332 national records and selected 541 FCT points: 441 settlements, 1 city, 5 administrative seats, 7 administrative areas, 40 water features, 15 landforms, 15 facilities, and 17 other geographic features. All 541 received a council parent from the boundary overlay. No PostGIS import is verified.
- GeoBoundaries: ADM1 and ADM2 GeoJSON files are present; a no-write dry run retained 7 boundary records and resolved all six councils. Database application of those boundaries is not verified.
- Who's On First: an opt-in importer now accepts an already-acquired local GeoJSON file or directory, filters to FCT geometry, records council parent, WOF ID/parent ID, geometry precision, original properties, and attribution in source provenance. It does not download or auto-import WOF data. The Nigeria admin repository is available upstream; its README points to the general WOF license, which requires a license link and cautions that upstream sources can carry separate attribution terms. Review each record's source properties and legal terms before production import. The upstream repository's SPDX metadata is `NOASSERTION`.
- OpenAddresses: no Nigeria source directory or source JSON was present in the OpenAddresses source catalog checked, and the Nigeria source endpoint returned 404. No importer or local dataset exists here; address-level coverage remains unavailable/unverified from OpenAddresses.
- External discovery/routing: Photon, Nominatim, and OSRM configuration hooks exist, with public service defaults in configuration. Their terms, commercial suitability, and production capacity have not been verified in this task. Configure an appropriately licensed/self-hosted or contracted service before relying on unrestricted commercial traffic. No Redis/shared cache, provider circuit breaker, or autocomplete rate limit was found in the inspected path.

## Schema and deployment

The PostGIS schema is additive and defines canonical records, sources, aliases, provenance, administrative geometries, trigram indexes, and GiST indexes. The Alembic graph reports one head: `20261022_fct_location_directory`. This checks the migration graph only; migrations were not applied or verified against PostgreSQL. `DATABASE_URL` is not configured in this workspace, so PostGIS query plans, import writes, migration execution, and staging/production rollout could not be verified. SQLite remains the available local test environment.

The no-write ingester report includes per-source record counts, feature types, council distribution (including zeroes for all six councils), valid/missing/invalid coordinate counts, parent counts, missing-name counts, and duplicate source-identifier counts. The completed GeoNames/boundary dry run reported GeoNames candidates by council: AMAC 163, Bwari 61, Gwagwalada 66, Kuje 152, Kwali 59, Abaji 40. Curated seed counts were respectively 30, 5, 5, 3, 2, and 2. The combined 588 candidates (541 GeoNames plus 47 curated) are parser output only; they were not written to PostgreSQL. WOF can be measured with `--import-wof --wof-path <local-file-or-directory> --dry-run` after the dataset has been obtained and reviewed; there is no automatic WOF download.

When PostGIS is available, apply migrations first with `flask --app run.py db upgrade`, then run the relevant ingestion command in dry-run mode and review its report before importing. The PostGIS ingester supports `--all --dry-run` and individual `--import-osm`, `--import-geonames`, and `--import-boundaries` options. The FCT directory importer is dry-run by default and requires explicit `--apply` to write. Back up the database before applying new imports. No migration was added in this update.

## Validation evidence

- `pytest tests/test_app.py -k autocomplete -q`: 7 passed.
- `.venv/bin/python -m pytest tests/test_app.py tests/test_fct_location_import.py tests/test_location_learning.py -q`: 110 passed, 1 failed. The only failure is the independently reproduced dashboard assertion `test_dashboard_uses_live_booking_payment_and_resource_data` expecting `₦100`; it does not touch location/import code.
- `pytest tests/test_booking_engine.py -k 'location or route or quote' -q`: 18 passed.
- `pytest tests/test_app.py -k 'geodata_coverage_summary or boundary_ingestion_dry_run' -q`: 2 passed.
- `node --check moving_company/static/js/location-picker.js`: passed.
- `.venv/bin/flask --app run.py db heads`: returned `20261022_fct_location_directory (head)`.
- `.venv/bin/python -m moving_company.services.postgis_ingestion --data-dir data/geodata --import-geonames --import-boundaries --dry-run`: completed with 76,332 GeoNames records parsed, 541 FCT candidate points, 7 boundary records, and all six councils resolved; no database writes.
- `pytest tests/test_app.py -k 'wof_parser or geonames_parser' -q`: 2 passed, including synthetic boundary clipping and source-provenance checks.
- `.venv/bin/python -m pytest tests/test_app.py::test_boundary_ingestion_dry_run_reports_polygons_without_database tests/test_app.py::test_geonames_parser_uses_feature_class_and_area_council_parent tests/test_app.py::test_wof_parser_clips_fct_and_preserves_source_provenance -q`: 3 passed after making WOF CLI arguments optional for existing programmatic callers.
- A whole-repository `pytest -q` run was interrupted after 193 tests had passed and showed two failures at that point: the boundary dry-run compatibility issue (subsequently fixed and verified by the 3-test command above) and the independently reproduced dashboard currency assertion. The run was not completed, so no whole-suite pass is claimed.

No representative PostGIS load test was run. Cold/warm latency percentiles, throughput, cache hit rate, error rate, query plans, and provider timeout behavior remain unmeasured; the stated performance targets are not claimed as achieved.

## Remaining work

1. Configure PostgreSQL/PostGIS and verify migrations, indexes, importer writes, and coverage against database contents.
2. Complete and retain the whole-country OSM parse-only report; the prior run did not finish.
3. Obtain the WOF dataset, review embedded source attribution/licensing, run its new local-path dry run, and then validate it before any database import.
4. Recheck OpenAddresses source coverage over time; no Nigeria source appeared in the country catalog queried for this audit.
5. Decide on production geocoder/router services and set their usage policies, timeouts, caching, and availability controls.
6. Add shared rate limiting and provider circuit-breaking if required by the production traffic model.
7. Benchmark local search with representative PostGIS data and concurrency; separately measure warm-cache behavior.
8. Resolve the unrelated admin dashboard payment-total assertion, run the complete regression suite to completion, and verify staging migration/rollback before production deployment.
