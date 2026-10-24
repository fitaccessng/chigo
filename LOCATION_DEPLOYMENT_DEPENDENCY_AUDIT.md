# Chigo Deployment Dependency Audit

Audit date: 2026-10-22  
Target application: DockHive app `chigo-africa`, version 32, domains `www.chigomove.online` and `chigomove.online`  
Decision: **Fix prepared locally; do not redeploy. Local coordinator is regression-tested, but the live database/index remains unverified.**

## Executive Finding

This read-only audit confirms the deploy package excludes the local SQLite file, uploads, and geodata sources; `.env` is also excluded by DockHive's packaging behavior. The Dockerfile requires PostgreSQL configuration and turns off SQLite fallback, so the running app is intended to use an injected PostgreSQL `DATABASE_URL`. However, read-only deployment inventory does not expose the connected database resource, schema, counts, or import provenance. No authorized database identity/read path was available in the current tool interface.

The public autocomplete endpoint was tested directly before this local fix. It returned HTTP 200 with an empty successful result for `Gwarinpa`, `Gwarimpa`, `Wuse`, `Kuje`, and a deliberate no-match. For `Abuja`, it returned eight Photon results whose names and state fields were French localities, with `provider=photon`, `verified=false`, and `requires_confirmation=true`.

**Confirmed code defect for the France results:** `search_places()` only appended Abuja/FCT/Nigeria context when the raw query did not already contain `abuja`, `fct`, or “federal capital territory.” Thus a bare query `Abuja` was sent to Photon as the unconstrained global `q=Abuja`, without a `countrycode` or geographic bias. Photon can return same-name results anywhere; the live French results are consistent with that exact request path. For other queries such as `Gwarinpa`, local no-hit also falls through to Photon. This explains the foreign results, but does not establish why local lookups for Gwarinpa/Wuse/Kuje did not find rows: the live database is not inspectable through the available tools, so missing/unimported data and any deployment-side query issue remain possible contributors.

The separate public Photon control query for `Gwarinpa, Abuja, FCT, Nigeria` returned a Gwarinpa-area feature. This proves Photon is reachable, not that Chigo's provider query/ranking or local persistence is correct.

## Actual Autocomplete Execution Path

1. `moving_company/routes/locations.py::autocomplete` validates `q`/`query`, limit, optional area council and paired coordinates, then calls `search_places`.
2. `moving_company/services/location_service.py::search_places` checks its in-process cache, then calls `_search_postgis_locations`.
3. `_search_postgis_locations` runs only when Flask has an app context and SQLAlchemy reports PostgreSQL. It queries `location_records` joined to `location_aliases`, then queries `location_observations` to fill remaining result slots. Database SQL errors should raise `LocationSearchUnavailable` and the route should return 503.
4. If that returns no hits (or PostGIS is not selected), `_search_local_abuja_places` queries active `LocalPlace` rows using exact/prefix/alias/keyword/address/council matching.
5. If both local sources produce no results, `search_places` calls Photon at the configured `PHOTON_BASE_URL` (default `https://photon.komoot.io`). Before the fix, a query containing `Abuja` was sent globally without restrictions. The local fix now adds FCT/Nigeria query context, supplies Photon `bbox=6.6,8.2,7.85,9.85`, and accepts only candidates with valid coordinates inside the FCT bounds plus Nigeria country/country code and FCT state or an FCT council. The route then projects results into the public response contract.
6. Nominatim is used by geocode/reverse-geocode functions, not by the autocomplete search path. OSRM is used by route calculation, not autocomplete.

Live `Abuja` results exposed `provider=photon` and `source=photon`, confirming the fallback. Live `Gwarinpa`, `Gwarimpa`, `Wuse`, and `Kuje` returned empty lists, but those responses alone do not distinguish a genuine no-match from absent local data. Before the local fix, the deliberate no-match also returned HTTP 200 / `success=true` with an empty list. The endpoint contract treats this as a normal no-match; local DB and provider failures are coded to return 503, but the live DB failure state is not verifiable from HTTP results alone.

The independent Photon control query `q=Gwarinpa, Abuja, FCT, Nigeria` returned a feature in the FCT bounds. The bounded Photon query `q=Abuja&bbox=6.6,8.2,7.85,9.85` returned Nigerian/FCT candidates but also one Nasarawa-state feature inside the rectangle. This is why the local fix uses both the bbox and country/state/council validation rather than trusting bbox alone.

**No redeploy was performed.** No environment variables, data, schema, database, files in the app, or production resources were changed. DockHive remains at version 32; the local fix has not reached production.

## Local Fix and Regression Evidence

- `moving_company/services/location_service.py`: retains local-first search, combines existing PostGIS and `LocalPlace` index results, deduplicates by source ID and conservatively by normalized name/context/nearby coordinates, ranks exact/prefix/alias and FCT metadata, and preserves source labels as `source_provenance`. Photon is called once only when both local indexes return no hits, with bounded timeout, FCT bbox, and Abuja/FCT/Nigeria query context. Provider features must have valid FCT-bounds coordinates, Nigeria country/country-code metadata, and FCT state or council evidence. Malformed coordinates and foreign/non-FCT candidates are discarded.
- The merge preserves `id` and `local_place_id` through normalization so local/canonical selections remain compatible with booking persistence. Same-name results from different councils remain separate; reused source IDs are not merged if council/type conflicts or coordinates differ by more than 150 m.
- `moving_company/routes/locations.py`: public response projection now exposes the non-sensitive contributing `source_provenance` field while retaining the existing location contract.
- `tests/test_app.py`: request bbox/query-context, mixed Photon records, malformed coordinates, local-index cross-merge, source provenance, cross-council duplicate preservation, and source-ID collision regressions.
- `tests/test_location_learning.py`: existing Photon mock now uses realistic GeoJSON structure and FCT metadata.
- Focused coordinator tests: 12 passed, 105 deselected across autocomplete/Photon/import/local learning cases.
- Final booking/location/routing regression command: `.venv/bin/python -m pytest -q tests/test_location_learning.py tests/test_fct_location_import.py tests/test_app.py tests/test_booking_engine.py -k 'autocomplete or location or route_distance or osrm_route or quote_retries_pending_road_route'`: **41 passed, 142 deselected** after the final collision guard. Earlier runs exposed and fixed local ID preservation and source-ID collision behavior.
- Pylance reported no errors in touched Python files; `git diff --check` passed. This is local verification only; no production/staging verification has happened.
- This earlier SQLite benchmark was superseded by the final coordinator diagnostic below.
- The test frontend uses `engine.html` inline autocomplete and `home_decor.html`; `location-picker.js` is not referenced by active templates found in this audit. Existing inline handlers cancel stale requests, distinguish non-2xx responses, and selection posts the existing hidden location IDs/provider/coordinates. Syntax checks passed; no browser journey was run.

### Unified Coordinator Follow-Up

- The previous fix was extended locally so PostGIS and `LocalPlace` results are both consulted and merged before any Photon request. Cross-source duplicates use source identifiers first, then exact normalized name, council, feature type, and a 150 m distance ceiling. Same-name records in different councils remain distinct. The merge preserves `id`/`local_place_id` and returns source labels in `source_provenance`.
- Local candidates with contradictory non-Nigeria/non-FCT metadata are excluded. Photon remains the only autocomplete fallback; no Nominatim/OSRM autocomplete fan-out was added. It is bounded by a 1.2 s request timeout, a supported FCT bbox, Nigeria/FCT query context, and validation requiring valid coordinates, Nigeria metadata, no explicit non-FCT state, and FCT state/council evidence.
- Focused coordinator regression command after the final source-ID collision guard: `.venv/bin/python -m pytest -q tests/test_location_learning.py tests/test_fct_location_import.py tests/test_app.py tests/test_booking_engine.py -k 'autocomplete or location or route_distance or osrm_route or quote_retries_pending_road_route'`; **41 passed, 142 deselected**. This covers autocomplete, local aliases, FCT filtering, local ID persistence, route behavior, booking location, and quote retry. Five explicit coordinator edge cases passed.
- Complete unfiltered suite before the final localized source-ID collision guard/test: **312 passed, 0 failed, 0 skipped in 121.68 seconds** using `PYTHONWARNINGS=ignore .venv/bin/python -m pytest -q --tb=short`. The full suite was not rerun after that localized change; the focused location/booking suite was rerun and passed. These are local SQLite/test-client results.
- Latest local diagnostic against configured SQLite (20 cache-cleared requests): endpoint p50/p95/p99 **8.9/11.0/35.8 ms**, direct local matcher **6.1/11.6/205.9 ms**. One no-match overlapped a Photon transport failure and correctly returned HTTP 503. This is a local SQLite benchmark only; live PostGIS latency and representative concurrency are unknown, so it is not a production SLA result.
- No test suite result, local benchmark, or provider smoke test is evidence that the live database contains location rows. No deploy, DB read/write, import, migration, or browser journey occurred; deployed app remains version 32.

## Source Strategy Status

- Chigo-curated and directory records exist in the local SQLite file only as verified local evidence; corresponding live DB rows are unknown.
- OSM, GeoNames, GeoBoundaries, and WOF are ingestion inputs, not search-time adapters. A local GeoNames/boundary dry-run produced candidates but no persistent DB writes. OSM import success is unknown; no WOF bundle or OpenAddresses data is present locally.
- Photon is the only external autocomplete fallback in `search_places`; Nominatim is used by geocode/reverse-geocode and OSRM by routing. No parallel request fan-out, task queue, or new provider was introduced.
- No migration, source import, database mutation, environment change, browser test, or redeploy occurred.

## Verified Facts

- DockHive lists `chigo-africa` as an uploaded app, active version 32, deployment `dep-a201c45c7242445b85c6`, on Chigo's public domains.
- The prior deployment upload reported 221 files and 642.7 KB. The repository's geodata directory is about 695 MB, including a 677 MB Nigeria OSM PBF. The large geodata is excluded by `.gitignore` (`data/geodata/`); the small upload size independently confirms that the PBF was not in the deployment package.
- `.dockhiveignore` excludes `moving_company.db`, SQLite WAL/SHM files, and `uploads/`. The Docker ignore file also excludes `.env` and `moving_company.db`; DockHive documents that `.env` files are never uploaded and environment values are injected separately.
- `Dockerfile` sets `CHIGO_REQUIRE_DATABASE_URL=true`, `DATABASE_FALLBACK_TO_SQLITE=false`, and `AUTO_CREATE_SCHEMA=false`. Application configuration therefore refuses to select SQLite if these image settings are active and `DATABASE_URL` is absent.
- The deployment build/start completed and the app became live. The actual database resource identity, URL target, role, schema, row counts, and import source counts are not exposed by the available read-only DockHive inventory tools.
- The local development SQLite database contains 1,104 `local_places` rows, but it is specifically excluded from deployment and is not evidence about the live PostgreSQL database.
- `POSTGIS_DATA_DIR` is read by the explicit PostGIS ingestion command. It is not read by the regular autocomplete route. The canonical location tables are queried at runtime when PostGIS is selected; otherwise SQLite `LocalPlace` is queried. Startup seeding only runs under `AUTO_CREATE_SCHEMA=true`, which the image sets false.
- The OSM PBF, GeoNames archive, GeoBoundaries files, WOF files, and OpenAddresses files are importer inputs, not automatic runtime location stores. OSM import has not been verified against any persistent database. GeoNames/boundary dry-run candidates are not imported DB rows. WOF and OpenAddresses datasets were not found locally.
- `uploads/` is used for booking, inventory, vehicle, home-decor, and incident evidence files; database records hold paths/filenames and routes serve files from the local `UPLOAD_FOLDER`. No cloud object-store adapter or Docker `VOLUME` declaration was found in the inspected repository. Whether DockHive supplies a persistent volume outside the image is unknown.

## Dependency Table

| Excluded path/config | Consumers | Runtime or ingestion-only | Persistent elsewhere? | Expected impact if excluded | Evidence / status |
|---|---|---|---|---|---|
| `data/geodata/` (including `nigeria-latest.osm.pbf`, `NG.zip`, ADM1/ADM2 boundaries, source caches) | `moving_company/services/postgis_ingestion.py`; `scripts/import_fct_locations.py`; offline diagnostics/audits | Explicit ingestion, dry-run, or audit only; regular autocomplete does not load these files | Imported DB persistence is unknown. No proof OSM has ever been imported to the intended live DB | Ingestion cannot use cached files unless downloaded again; runtime search should not require the PBF if the persistent index is complete | `.gitignore` excludes the directory; prior upload size excludes the 677 MB PBF. No live DB query access available. |
| `.env` | `moving_company/config.py`, `postgis_ingestion.py`, local scripts | Local development/config convenience; deploy expects platform-injected environment values | DockHive stores app environment separately; exact values not readable through current tools | Safe not to bake into image. Missing/incorrect platform values can break DB, mail, payment, or provider features | Build succeeded; `missing_env=[]`; secrets were not printed. No read-only env-name/value inspection API available. |
| `moving_company.db` and SQLite `-wal`/`-shm` | `Config.SQLITE_DATABASE_URI`, development/tests, FCT import fallback/backup logic | Development fallback only in container because Dockerfile requires Postgres and disables fallback | Live PostgreSQL is intended, but exact resource and its contents are unverified | Excluding this local file is correct only if the persistent Postgres DB contains the needed location index. That is not verified; live known-place searches currently fail. | `.dockhiveignore` and `.dockerignore` exclude it; local SQLite has location rows but cannot stand in for live DB. **Critical unresolved dependency.** |
| `uploads/` | `image_upload_service.py`; booking, customer, admin, home-decor, and evidence routes | Runtime user-generated files | No repo-level persistent mount/object store is configured; DockHive persistence is unknown | Existing stored filenames may point to files not present in a fresh image/container. Redeploy may lose or orphan uploads absent a persistent volume | Both ignore files exclude uploads; route/model references confirmed; no volume declaration or storage adapter in repo. **Data preservation risk unresolved.** |
| `LocalPlace` / `local_places` | Location autocomplete, place detail resolution, booking selection, curated seeding | Runtime critical when SQLite mode is active; not seeded in production image because `AUTO_CREATE_SCHEMA=false` | Intended to be in external persistent PostgreSQL or local SQLite, but live table/rows not inspected | Missing/unpopulated table means known FCT queries have no local results; provider may return irrelevant/global results | Live `Gwarinpa`, `Gwarimpa`, `Wuse`, `Kuje` all returned zero. Persistent DB state unknown. |
| `location_records`, `location_aliases`, `location_observations`, `administrative_areas`, `location_sources`, `location_provenance` | PostGIS search, place detail, learning, route reference resolution, importer | Runtime critical on PostgreSQL path; created/populated only by reviewed migrations/imports | Live table presence and row counts unknown | Missing schema may cause query errors, fallback, or no results depending on deployed code version/error handling | Dockerfile requires PostgreSQL; no direct DB inspection capability or resource ID available. |
| Photon / Nominatim / OSRM public endpoints | Photon autocomplete fallback, geocoding, route calculation | External runtime services | External, not persisted as Chigo local index | Fallback may be inaccurate, rate-limited, unavailable, or return no match. Live `Abuja` test returned French localities from Photon. | Photon direct control returned a Gwarinpa feature; Chigo's query output did not. Nominatim/OSRM runtime health not inspected in this audit. |
| WOF / OpenAddresses datasets | Optional importer paths | Ingestion only | No local data found; no DB import verified | No additional WOF/OpenAddresses coverage; not a runtime requirement for current routes | Repo supports local WOF input; no WOF bundle/OpenAddresses package found. |
| `data/location-readiness-evidence/` | Audit documentation only | Not runtime-required | Not applicable | Extra audit artifacts may be included in deploy because only `data/geodata/` is ignored, not the evidence subtree; they are not served by application routes but add package content | Path is not matched by `.gitignore`/`.dockhiveignore`; deployment package was 642.7 KB. No change made. |

## Per-Source Runtime Availability

| Source | Actual evidence | Runtime availability conclusion |
|---|---|---|
| Chigo curated FCT seed records | Local app seeding code defines 10 representative places and `seed_fct_locations()` only runs inside the `AUTO_CREATE_SCHEMA` startup block. Dockerfile sets `AUTO_CREATE_SCHEMA=false`. Local SQLite also contains 47 `chigo-curated` records. | Available in the local development DB. Whether they exist in the live database is unknown; startup configuration does not seed them in the intended container path. |
| FCT directory (Wikipedia/zipcode.com.ng) | Local SQLite has rows with these source labels. A local report records a prior apply run; these are not live DB counts. Import script's SQLite write path targets the selected local DB; Postgres path depends on configured DB URL. | Imported into the local development DB. Live persistent target import is unknown. |
| GeoNames | Cached `NG.zip`; parser dry-run selected 541 FCT candidate points, zero DB writes. | Not proven imported to any persistent runtime DB. Runtime search can only use it if rows were independently imported to the configured DB. |
| OpenStreetMap PBF | 677 MB local PBF, explicitly ignored/excluded from upload; prior parse did not complete. Importer writes PostGIS only when explicitly run without dry-run. | Not proven imported. The PBF itself is not available in the deployed image. |
| geoBoundaries | ADM1/ADM2 used in local parser dry-run, which resolved boundaries. | Boundary files are not runtime search tables. Boundary geometries are usable at runtime only if imported into PostGIS `administrative_areas`; live table/data status unknown. |
| Who's On First | Importer accepts an explicit local WOF path; no bundle found. | Not imported/available from local source files; live DB provenance unknown. |
| OpenAddresses | No local dataset or import adapter confirmed. | Not imported/available from local source files; live DB data unknown. |
| Photon | Live Chigo `Abuja` results identify Photon as provider; direct external control query returned a Gwarinpa feature. | Externally available at runtime. Chigo's observed fallback results can be unrelated to FCT, so this is not a safe substitute for verified local coverage. |
| Nominatim / OSRM | Configuration and adapters exist. They are geocoding/reverse-geocoding and routing providers, respectively, not autocomplete providers. | Intended external runtime services by config; no health check from this endpoint trace. |

## Database Identity Limits

The Dockerfile requires `DATABASE_URL`, disables SQLite fallback, and sets `AUTO_CREATE_SCHEMA=false`. If that exact Dockerfile was used unchanged, the application must have a PostgreSQL URL to start. The available app record labels the build mode as `drone`, and no read-only DockHive database-resource inventory or safe database connection details were available in this audit. Thus the live backend and actual database identity are **not verified**; the Dockerfile requirement is not proof of the attached DB or its location records.

## Live Search Evidence

Read-only browser requests to the live API, each with limit 8:

| Query | HTTP | success | Count | Result check |
|---|---:|---:|---:|---|
| `Gwarinpa` | 200 | true | 0 | Expected representative FCT locality not returned |
| `Gwarimpa` | 200 | true | 0 | Known alternate spelling not returned |
| `Wuse` | 200 | true | 0 | Expected FCT locality not returned |
| `Kuje` | 200 | true | 0 | Expected FCT council/locality not returned |
| `Abuja` | 200 | true | 8 | Photon results were French locations, not FCT; unverified, confirmation required |
| deliberate unique no-match | 200 | true | 0 | Indistinguishable at contract level from the known-place failures |

Additional live browser batch on v32, 2026-10-22: `Maitama` 0 results (860 ms), `Gwagwalada` 0 (1,289 ms), `Bwari` 0 (1,020 ms). A repeat batch recorded `Gwarinpa` 0 (3,058 ms), `Gwarimpa` 0 (183 ms), `Wuse` 0 (845 ms), `Kuje` 0 (906 ms), `Abuja` 8 results including France via Photon (948 ms), and the deliberate no-match 0 (3,029 ms). All responses had HTTP 200 and success=true. These browser fetch timings are uncontrolled wall-clock samples. Empty response payloads do not establish that the local DB query succeeded.

The same `success=true/results=[]` shape for both known-place failures and a deliberate no-match does not establish that the DB is healthy; it can mask absent data or degraded local/provider search.

## Redeploy Decision

**STOP — do not redeploy.** The live app's required FCT location behavior fails representative checks, and the persistent DB/index contents cannot be inspected. Re-uploading this repo cannot deliver the excluded SQLite data or geodata source files, and there is no evidence that a persistent database has the expected canonical/local-place tables and rows. The deployment also has an unresolved `uploads/` persistence risk.

Minimum safe actions before considering redeploy:

1. Obtain the DockHive database resource ID attached to `chigo-africa` and read-only connection access or a platform-provided schema/count diagnostic. Do not expose the connection string.
2. Verify PostgreSQL/PostGIS, current Alembic revision, required location tables/indexes, local/canonical row counts by source, and representative queries on that exact DB.
3. Confirm the latest DB backup and isolated restore evidence. No migration/import/merge until separately approved.
4. Confirm DockHive persistent volume/object storage configuration for `uploads/`; verify existing booking media remains available after container replacement.
5. Use the attached live DB resource identity/read-only permissions to determine whether FCT records are missing or local SQL/index behavior is failing. The local Photon fallback fix prevents global/off-FCT results, but cannot populate the live index. Preserve outputs and validate before redeploy approval.
6. Ensure the deployment ignore manifest intentionally includes only runtime-required files and excludes private audit evidence if not required in the image.
7. Deploy only after the actual database is inspected and the FCT records/index availability is confirmed; then repeat live representative queries and the safe browser booking flow.

No environment variables were changed, no app was redeployed, and no data/schema was modified. No customer or booking records were queried. A short runtime log tail was inspected for deployment health; an unrelated email-delivery error included recipient information, which is not reproduced in this report.
