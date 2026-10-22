# Chigo Location Data Quality Report

Audit date: 2026-10-22. Database inspected: configured development SQLite at the local project database, opened read-only. It is not staging. Machine-readable reports are under `data/location-readiness-evidence/20261022T051816+0100/`.

## Actual Local Database

- `local_places`: 1,104 rows total; 1,011 active and 93 inactive.
- Seven indexes are present, including active/normalized-name, council/normalized-name, postal code, and unique source identifier.
- Source/type aggregate rows: unknown source 10; Chigo curated 47; directory parent reference 32; Wikipedia electoral ward facility candidates 77; Wikipedia postal settlements 706; zipcode.com.ng 232 (23 post offices, 169 settlements, 40 streets). Totals equal 1,104.
- Active council values after the explicit alias map: AMAC/Abuja Municipal Area Council combine to 231; Bwari to 76; Gwagwalada 296; Kuje 159; Kwali 147; Abaji 102. Total 1,011. Original values remain unchanged in this audit.
- Source identifiers: no duplicate non-null values observed in the local database.

## Coordinate Audit

- Active rows with coordinates: 50; missing coordinates: 961; numeric invalid/zero-zero: 0; outside rough configured FCT bounds: 0.
- All 50 coordinate-bearing rows are covered by the local ADM1 FCT polygon, according to the available polygon file.
- ADM2 contains all six recognized council polygons after matching `Municipal Area Council` to AMAC. Of the coordinate-bearing rows, 46 are covered by one council polygon matching the canonicalized stored council and 4 disagree. A mismatch is a review flag, not proof that the point or council value is wrong.
- 50 rows are marked verified in current local fields. That flag alone is not treated as proof of coordinate provenance/precision.
- Missing-coordinate disposition: leave all 961 unknown until a unique trusted source match or reviewed manual coordinate is available. No coordinates were written.

## Duplicate Reconciliation

Machine-readable plan: `duplicate-reconciliation-plan.json`. Candidate groups use exact normalized name + canonicalized council alias + exact feature type. The audit found 164 groups and 164 excess rows under that stricter key. This intentionally differs from the earlier broad 198-group report, which grouped on a different key and is not comparable without the precise old query. Each candidate group is a review item, not a merge instruction.

The review plan is in `data/location-readiness-evidence/20261022T051816+0100/duplicate-reconciliation-plan.json`; names and location-like values are hashed/redacted, while local row IDs and source metadata remain for authorized review.

Recommended action for every group: compare location ID, source identifier, original council, aliases, feature type, source provenance, coordinate presence/quality, and relevant booking references. Preserve all rows until an authorized, evidence-backed mapping is approved. No automatic merges were executed.

## Import Report Versus Database

The report generated 2026-10-20 describes 1,053 reconciled source records (1,047 inserted, 6 updated in its stored result), 916 postcodes, zero verified coordinates, and 15 source reconciliation duplicate candidates. Current local SQLite has 1,104 records with source/type composition described above. Counts cannot be equated: report counts are not a live query of this target and one source includes inactive review candidates. Staging counts are unknown.

## Rejected Records

No records were ingested or rejected in this read-only audit. Existing importer report conflicts/rejections remain source-report items; they were not replayed or classified here. See `rejected-record-summary.json` and the source report. No output report claims a new import.