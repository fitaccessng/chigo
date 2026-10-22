# Chigo Staging Database Migration Runbook

Status: **PROPOSAL ONLY — NOT AUTHORIZED TO EXECUTE**  
Prepared: 2026-10-22  
Repository head observed: `20261022_fct_location_directory`  
Local database revision observed: `20261006_upgrade_booking_location_schema` (development SQLite only)

## 1. Scope and Hard Stops

This runbook is not proof of staging identity, backup recovery, or target schema. No staging resource was identified in this audit. Do not execute the migration command until every gate in this document is evidenced and an authorized owner explicitly approves the specific staging change window.

Never point this procedure at production. Never use the local SQLite snapshot as a PostgreSQL backup. Do not stamp Alembic to head, drop/recreate tables, merge locations, or run source imports as a substitute for reconciliation.

## 2. Current Repository Migration Chain

The repository contains one root, one head, and no missing parent identifiers in its statically parsed revision graph. The 20 revisions after the currently recorded local revision are:

| Revision | Principal upgrade effects | Data/operational risk |
|---|---|---|
| `20261006_add_postgis_location_tables` | Enables `postgis` and `pg_trgm`; creates source, boundary, canonical record, alias, provenance, and observation tables; B-tree, GiST, and trigram indexes. | Requires extension permissions. Downgrade drops all new canonical location/source data. |
| `20261006_expand_location_resolution` | Adds address/provider/nearest-road/route-resolution fields and parent-area-type index. | Uses PostgreSQL `IF NOT EXISTS`; no backfill shown. Downgrade is intentionally blocked. |
| `20261007_add_demo_fleet_metadata` | Adds demo/seed/fleet attributes and team table/FKs/indexes. | Defaults apply to existing rows; downgrade removes added metadata/table. |
| `20261008_add_service_intent` | Adds `bookings.primary_service` with default and index. | Existing bookings receive default `residential`; downgrade removes service intent. |
| `20261008_add_attachment_media` | Adds attachment metadata, updates existing photo names/type/size defaults, tightens nullability, FKs/indexes, creates vehicle images. | Data backfill and constraint tightening; downgrade drops image table and new photo fields. |
| `20261008_add_inventory_catalogue` | Creates inventory catalogue/booking inventory tables and photo FK/index. | New persistent tables; downgrade removes selection/catalogue data and FK. |
| `20261008_add_location_learning_metadata` | Adds LocalPlace city/landmark/type/confidence/precision and booking location IDs/indexes. | New location references; downgrade drops columns/IDs. |
| `20261008_add_booking_route_metadata` | Adds route source and precision fields. | Downgrade drops route metadata. |
| `20261008_add_support_chat` | Creates support conversation/message/request tables and indexes. | New support records; downgrade drops those records/tables. |
| `20261009_add_oauth_identities` | Creates OAuth identity table and user index. | New identity records; downgrade drops them. |
| `20261010_add_customer_referrals` | Creates referral/campaign/attribution/reward ledger tables; inserts a campaign row; adds booking referral columns/FK/index. | Seeded business data and financial ledger; downgrade drops these records and booking terms. |
| `20261011_service_aware_locations` | Makes pickup/destination booking address/city/state nullable. | Required for services without both addresses; downgrade restores NOT NULL and can fail/lose compatibility if null values exist. |
| `20261011_service_pricing_approval` | Adds pricing configuration approval state. | Existing pricing receives defaults; downgrade removes approval state. |
| `20261012_home_decor_requests` | Creates Home Decor requests/attachments and indexes. | New customer workflow/attachment data; downgrade drops tables/data. |
| `20261016_booking_cancel_damage` | Adds cancellation/completion and incident evidence/contact/time fields; backfills `submitted_at` from legacy date/current time; makes it non-null. | Mutates incident rows and may synthesize timestamps; downgrade drops report/cancellation metadata. |
| `20261018_dispatch_reservations` | Adds partner service types and assignment FKs; creates AI usage, dispatch, offer/event, reservation tables/indexes. | Operational reservation/dispatch data; downgrade drops those records and metadata. |
| `20261019_booking_event_idempotency` | Adds nullable idempotency key and unique constraint. | Unique constraint may fail if non-null duplicate keys already exist. |
| `20261020_customer_controls` | Adds account status/service restriction columns and index. | Defaults apply to existing users; downgrade removes controls. |
| `20261021_optional_booking_countries` | Makes pickup/destination country nullable. | Downgrade to NOT NULL can fail if unknown values exist; downgrade intentionally disabled. |
| `20261022_fct_location_directory` | Adds LocalPlace source/provenance/postcode/verification fields and unique source ID index; adds booking postcodes; makes local/canonical coordinates and geometry nullable. | Unique index can fail on duplicated non-null IDs; SQLite/postgres batch behavior differs; no data backfill is encoded. Downgrade intentionally disabled. |

This is a code review of migration source, not an inspection of the unknown staging database. Migration SQL/rendered DDL has not been generated or executed against a clone.

## 3. Startup Schema Mutation Guard

`create_app()` calls `db.create_all()` and `upgrade_booking_schema()` whenever `AUTO_CREATE_SCHEMA` is true. The latter issues direct `ALTER TABLE`, `CREATE INDEX`, and an `UPDATE incident_reports` timestamp backfill without writing Alembic revision state. Configuration defaults `AUTO_CREATE_SCHEMA` false for PostgreSQL and true for SQLite, but an explicit environment override can change this. `run.py` constructs the Flask app at import time, including for Flask CLI.

**Mandatory migration precondition:** in the confirmed staging command environment, set and verify `AUTO_CREATE_SCHEMA=false`. `FLASK_SKIP_DB_PREFLIGHT` does not disable `db.create_all()` or `upgrade_booking_schema()`; do not treat it as a safety switch. Normal PostgreSQL preflight checks `SELECT 1` and `PostGIS_Full_Version()` before CLI command dispatch.

Alembic `migrations/env.py` obtains the URL/engine from the Flask-Migrate extension, so `flask --app run.py db upgrade` and application startup should use the same process configuration. That is only a configuration fact; matching them to an actual staging target still must be proven before execution.

## 4. Required Gates Before Execution

All must be checked and signed off:

- [ ] Owner identifies staging application/resource and the exact managed database resource ID.
- [ ] `APP_ENV=staging` is positively confirmed; no production target is used.
- [ ] Read-only target fingerprint captures backend, host/resource identity (redacted), `current_database()`, `current_schema()`, server version, and role name without secrets.
- [ ] Staging application and Flask migration CLI resolve to the same fingerprint.
- [ ] Production and staging isolation is demonstrated by authoritative infrastructure configuration.
- [ ] Staging role privileges for migrations/extensions are understood; no superuser assumption.
- [ ] Current Alembic revision and schema inventory are captured read-only from staging.
- [ ] A fresh backup is tied to the identified staging DB, timestamped, retained, and restore instructions recorded.
- [ ] Backup is restored into an isolated PostgreSQL/PostGIS clone; `pg_restore`/restore exits successfully, expected schema/tables are present, and integrity/smoke queries pass.
- [ ] A reviewed matrix maps every existing table/column/index/constraint and every revision from the staging revision to head.
- [ ] Startup is guarded with `AUTO_CREATE_SCHEMA=false` for both migration and smoke-test process.
- [ ] Owner approves the migration plan, window, expected downtime/impact, and rollback procedure.

Any unchecked item means **STOP**. Current status: all staging identity/recovery checks are unchecked.

## 5. Staging Clone Rehearsal

Only after gates above and explicit approval:

1. Restore the verified staging backup to a disposable isolated PostgreSQL/PostGIS clone, never over staging.
2. Set the clone-only database secret through the approved secret manager. Do not put a URL in shell history, logs, evidence, or this repository.
3. Confirm application/migration CLI target fingerprint. Confirm `AUTO_CREATE_SCHEMA=false`.
4. Inspect current Alembic revision and schema. Compare with the migration matrix. Investigate duplicate keys and NOT NULL/data backfill preconditions.
5. Review rendered SQL/DDL for the exact revisions. Confirm `postgis`/`pg_trgm` availability or approved extension-creation privilege.
6. Run migrations on the clone, record command output without secrets, and validate revision, table/column/index/FK definitions, row counts, PostGIS spatial functions, and representative location/booking queries.
7. Run application tests against the clone with external side effects sandboxed. Do not run geographic importer writes as part of this migration rehearsal.
8. Restore the clone again from its pre-migration backup and verify restore instructions a second time if required by change policy.

## 6. Proposed Staging Execution Command

This command is **not to be run now**. It is the repository-supported Flask-Migrate command, subject to the gates above. Run it only in an approved staging shell/job that securely injects the identified staging `DATABASE_URL`; do not put credentials inline or in shell history.

```sh
APP_ENV=staging AUTO_CREATE_SCHEMA=false .venv/bin/flask --app run.py db upgrade
```

Before execution, re-confirm the staged DB identity and current revision in the same job context. If any fingerprint differs from the approved runbook, stop. Do not add `FLASK_SKIP_DB_PREFLIGHT` unless specifically required and reviewed; it skips startup DB/PostGIS validation, while leaving schema mutation risk dependent on `AUTO_CREATE_SCHEMA`.

## 7. Post-Migration Validation

On the same identified staging target, read-only unless an explicitly approved test fixture requires writes:

- `flask --app run.py db current` and `db heads`; require one current revision at `20261022_fct_location_directory`.
- Inspect `pg_extension` for PostGIS and `pg_trgm`; verify `PostGIS_Full_Version()` and `ST_AsText(ST_SetSRID(ST_MakePoint(7.3986,9.0765),4326))`.
- Compare affected columns, nullability, defaults, indexes, FK/unique constraints with the reviewed expected state.
- Verify pre/post counts for bookings, local places, booking photos, incident reports, and referral/dispatch tables. Explain every difference; never dump personal rows into reports.
- Run `python scripts/location_diagnostics.py --benchmark 20` with the approved staging environment.
- Run focused location/booking tests and full suite per release policy; use synthetic data and sandbox integrations.
- Verify booking create/edit, location search persistence, route/quote workflow, and rollback path.

## 8. Rollback and Recovery

Several revisions have irreversible downgrades, and others drop tables/columns or financial/operational records. Therefore **Alembic downgrade is not the default rollback**. Recovery is the verified isolated-tested backup restore procedure or an approved forward-fix migration. For staging, stop app writes if consistency is at risk, preserve logs/evidence, restore only after owner approval, and re-verify target identity before restore. Never restore a staging backup onto production.

## 9. Stop Conditions

Stop immediately if target identity is ambiguous; app/CLI fingerprints differ; `AUTO_CREATE_SCHEMA` is true; backup origin/restore is unproven; migration revision has multiple/unexpected heads; a unique/FK/nullability precondition fails; extension permission is missing; row counts differ unexpectedly; migration output indicates partial failure; or any external side effect/payment is not safely sandboxed.
