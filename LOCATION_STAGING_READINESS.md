# Chigo Location Staging Readiness

Audit timestamp: 2026-10-22T05:18:29+01:00  
Repository commit: `f19174c` on branch `main`  
Decision: **NOT READY**

## Executive Decision

**NOT READY** — the workspace identifies development SQLite, not staging; there is no positive staging target, no proven production/staging separation, and no verified recoverable staging backup. No database mutation, migration, import, deployment, browser booking journey, or PostGIS benchmark was run. Do not proceed to staging writes.

## Phase 1: Environment and Recovery

### Inspected

- Current branch/commit and dirty worktree. Numerous application, migration, database, template, and test changes were already present; none were reverted.
- Flask configuration, app factory, database URL precedence, Alembic configuration, deployment file inventory, database location, source cache, and local backup filenames.
- `.env` key names only. Values, credentials, and connection strings were not displayed.
- Read-only DockHive app inventory. It lists a public uploaded `chigo-africa` app on the Chigo domains, but no separately identified staging app. This does not identify the app's database or prove isolation.
- Read-only resource inventory was repeated during this continuation; it still lists the public `chigo-africa` app and no staging database resource inventory suitable for positive identification.

### Results

- `APP_ENV=development`; backend is SQLite at the local project `moving_company.db`; `DATABASE_URL` is not configured; `USE_POSTGIS=false`; SQLite fallback is enabled.
- No deployment definition in the repository identifies staging. No staging marker, database resource ID, or staging URL key was present among `.env` key names.
- No staging database was connected to. PostgreSQL/PostGIS, target schema, production separation, and matching app/migration-tool target are unknown.
- The app inventory does not reveal the app's effective `DATABASE_URL`, database resource ID, role/permissions, or environment isolation. Successful listing of an app is not evidence of its database target.
- A dated file exists at `data/geodata/fct-directory-cache/backups/moving_company-20261020T143945Z.sqlite3`. Read-only SQLite `PRAGMA quick_check` returned `ok`; it has revision `20261006_upgrade_booking_location_schema`, 57 `local_places` rows and 50 active. The configured local database has the same revision but 1,104 rows and 1,011 active. This is a smaller local snapshot, not evidence of a staging backup. No isolated restore was tested; recoverability is unverified.
- No migrations or imports were applied. No live resource was modified.

### Commands/evidence

- `git branch --show-current`, `git rev-parse --short HEAD`, `git status --short`
- Redacted configuration-presence check (key names/booleans only)
- SQLite commands used `-readonly`, `PRAGMA query_only`, or `mode=ro`
- Read-only DockHive app list
- `data/location-readiness-evidence/20261022T051816+0100/`

### Phase 1 gate

**Failed / blocked.** To resume: provide an authoritative staging resource identity (or configure the correct staging secret environment and confirm `APP_ENV=staging`), demonstrate that the application and migration tooling use that same target, establish production isolation, provide a current backup plus documented restore procedure, and test restore into an isolated disposable database. Do not run migrations/imports before those conditions and explicit mutation approval are satisfied.

## Phase 2: Alembic and Schema Reconciliation (Read-Only)

- Static parse: 22 migration files, one root, one head (`20261022_fct_location_directory`), no missing parent revisions or duplicate revision IDs. `flask db heads` agrees.
- Local database Alembic marker: `20261006_upgrade_booking_location_schema`; repository history has 20 subsequent application revisions.
- Local SQLite schema has 41 `local_places` columns and later additions such as `source_name`, `source_identifier`, `postal_code`, `source_metadata`, and `verification_status`, plus seven indexes including a unique source identifier index. These objects are not represented by the recorded revision alone.
- **Likely cause, not proven historical fact:** when `AUTO_CREATE_SCHEMA` is enabled, `create_app()` calls `upgrade_booking_schema()`, which directly issues `ALTER TABLE`, `CREATE INDEX`, and at least one data backfill without updating Alembic history. This explains how local schema/data can advance while the revision marker remains old, but it does not prove which path changed each object.
- The local backup snapshot carries the same revision but only 57 `local_places` rows, so it does not resolve the discrepancy.
- Full object matrix: `data/location-readiness-evidence/20261022T051816+0100/schema-reconciliation-matrix.json`.
- The matrix covers each of the 20 revisions after the local revision, including location extensions/tables, media and incident backfills, referral seed data, nullable booking location changes, dispatch data, unique constraints, and downgrade data-loss risks.
- No `alembic stamp`, downgrade, migration, or schema alteration was run.

### Reconciliation plan (proposal only)

1. Positively identify staging and restore a verified backup to an isolated clone.
2. Compare the clone's catalog and data against every revision from its actual revision and the current SQLAlchemy metadata. Do not infer that local SQLite findings apply to staging.
3. Reproduce startup's direct schema upgrade on a disposable clone with read-only/dry-run safeguards or a catalog diff; enumerate all structural and data mutations.
4. Establish whether each missing revision's effects are already present and valid. Never stamp head solely to clear the discrepancy.
5. Prepare an approved forward-only migration/stamp repair plan with preconditions, row-count assertions, constraints, rollback-by-restore, and post-migration query validation. Apply only after backup/clone rehearsal and explicit authorization.

Risk: multiple later migrations add data-bearing tables and constraints; some downgrades drop columns/tables. The migration chain is statically linear, but target safety is unknown until staging is identified.

## Phase 3: Offline Geographic Audit

- Local source artifacts and checksums, retrieval timestamps, reported edition, licensing evidence, and gaps: [LOCATION_SOURCE_REGISTER.md](LOCATION_SOURCE_REGISTER.md).
- Actual local SQLite rows and polygon checks: [LOCATION_DATA_QUALITY_REPORT.md](LOCATION_DATA_QUALITY_REPORT.md).
- Machine-readable council dictionary, source register, duplicate plan, coordinate remediation, rejected summary, schema matrix, and non-executable import proposal: `data/location-readiness-evidence/20261022T051816+0100/`.
- Local ADM1 resolves one FCT feature. Local ADM2 resolves all six council polygons after matching its `shapeName="Municipal Area Council"` alias to AMAC. Of the 50 coordinate-bearing active rows, 46 fall in the single matching council polygon and 4 conflict with the stored canonical council. Coordinates and council assignments were not changed.
- Duplicate plan uses exact normalized name + canonicalized council alias + exact feature type. It is a conservative candidate set with record-level IDs/provenance for manual review, not a merge authorization. No duplicate was changed.
- FCT source report is a report, not a target database count. No geographic import or database writes were performed.
- Evidence hashes are in `data/location-readiness-evidence/20261022T051816+0100/SHA256SUMS.txt`; all entries verified with `shasum -a 256 -c SHA256SUMS.txt` from that directory.
- The current cached-file dry-run reports 76,332 GeoNames rows parsed, 541 FCT points (all with coordinates and council parents), 7 boundary records, all six councils, and 588 total candidates including 47 Chigo-curated locations. This is parser output, not database content. `source-dry-run-summary.json` records it.

## Phase 4: Staging Imports

**Staging import blocked; offline parser dry-run completed.** The local PostGIS ingester was run with `--import-geonames --import-boundaries --dry-run` against cached files. It parsed 76,332 GeoNames rows, selected 541 FCT GeoNames candidates (441 settlements plus other categories), retained 7 boundary records, resolved all six councils, and reported 588 candidates including 47 Chigo-curated records. No database writes occurred. No staging import was run. The FCT-directory `--apply` mode was not used.

The dry-run aggregate report is `data/location-readiness-evidence/20261022T051816+0100/source-dry-run-summary.json`. OSM was not parsed; Who's On First/OpenAddresses data were not available locally.

## Phase 5: Browser Booking Journeys

**Not run.** Existing Playwright/Selenium/Cypress tooling was not found. There is no confirmed staging URL/test account, no browser page shared, and no evidence that external side effects are safely sandboxed. Test-client coverage is not browser evidence. No browser framework was added and no real payment or partner contact occurred.

Required before browser execution: staging identity and safe database clone, confirmed sandbox payment mode, synthetic account/data, and disabled/sandboxed email/SMS/dispatch side effects. Then use existing compatible browser tooling if available or approve minimal test tooling.

## Phase 6: PostgreSQL/PostGIS Benchmarks

**Not run.** No identified PostgreSQL/PostGIS staging clone or representative loaded dataset exists in the configured environment. Prior SQLite measurements are not extrapolated. Define an approved latency/error target before benchmarking; collect query plans, concurrency, throughput, p50/p95/p99, and resource limits on an isolated clone.

## Phase 7: Automated Checks

Historical local full-suite result from the prior checkpoint: 308 passed, 0 failed, 0 skipped, 48,597 warnings, 113.41 seconds. Focused booking tests: 7 passed. This is not staging/browser evidence. No test or migration was run as part of this read-only Phase 1/2/3 audit.

For this continuation, `python scripts/location_readiness_audit.py --help`, `node --check moving_company/static/js/location-picker.js`, `node --check moving_company/static/js/booking.js`, and `git diff --check` passed. The read-only audit CLI was run against local SQLite and wrote a new evidence bundle; the GeoNames/boundary parser was run with `--dry-run` and reported no database writes. No long test suite was repeated because no application code behavior was changed.

## Files Added

- `LOCATION_STAGING_READINESS.md`: phase gates and decision.
- `LOCATION_SOURCE_REGISTER.md`: source fingerprints, license status, and coverage limits.
- `LOCATION_DATA_QUALITY_REPORT.md`: measured local database and polygon findings.
- `scripts/location_readiness_audit.py`: repeatable read-only local audit producing aggregate and review artifacts.
- `STAGING_DATABASE_MIGRATION_RUNBOOK.md`: gated clone rehearsal, exact proposed migration command, validation and recovery procedure.
- `data/location-readiness-evidence/20261022T051816+0100/`: timestamped, non-secret evidence.

Existing dirty worktree changes were preserved. The only execution database used was the configured local SQLite database in read-only mode.

## Exact Unblock Request

Provide/configure the authoritative staging resource identifier and staging environment, confirm the app and migration tooling target the same isolated database, and provide evidence of a recent backup with an isolated restore test or approved equivalent. Until then, remain at read-only/offline work; do not run staging migration/import commands.

## Verified / Inferred / Unknown

- **Verified:** current process configuration is development SQLite; no `DATABASE_URL`; one repository Alembic head; local DB marker is the older revision; public Chigo app appears in the read-only DockHive app list; no separate staging app is listed; the dated local SQLite snapshot passes `quick_check` but has different location counts; current local `local_places` columns match the current model; cached GeoNames/boundary dry-run results are recorded.
- **Inferred from code:** `AUTO_CREATE_SCHEMA=true` on SQLite causes `create_app()` to call `db.create_all()` and direct `upgrade_booking_schema()` DDL/backfill without changing Alembic revision. PostgreSQL defaults this flag false, but an override can re-enable it. This is a plausible explanation for local revision/schema drift, not proof of the history of every object.
- **Unknown:** staging application/database resource identity, effective staging database URL/role, production separation, staging schema/revision, backup origin and recoverability, exact cause of schema drift on any remote DB, and whether all migration prerequisites hold on staging.