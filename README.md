# Chigo Relocations

A Flask-based moving and relocation platform MVP for a professional relocation company serving Abuja and Nigeria. The app is structured around a booking-led workflow and uses SQLite by default for local development.

## Features implemented

- Public marketing pages and service pages
- Multi-step booking flow: location, property, inventory, services, schedule, quote, review
- SQLite-backed database model layer with SQLAlchemy
- Secure session-based auth with Flask-Login and hashed passwords
- Customer dashboard and booking detail views
- Admin dashboard and booking listings
- Pricing calculation service with configurable defaults
- OpenStreetMap + Leaflet map visualization for booking locations
- Nominatim-based autocomplete and geocoding for Abuja/FCT and wider Nigeria searches
- OSRM route calculation for road-based driving distance and duration
- Notification storage for customer updates
- Error pages and project-level app factory

## Project structure

- `moving_company/__init__.py` — app factory and seed setup
- `moving_company/config.py` — configuration and environment values
- `moving_company/models.py` — SQLAlchemy models
- `moving_company/routes/` — Flask blueprints for public, auth, booking, customer, admin
- `moving_company/services/pricing_service.py` — server-side pricing engine
- `moving_company/templates/` — Jinja templates and reusable layout
- `tests/test_app.py` — app factory smoke test

## Routes created

Public:
- `/`
- `/services`
- `/services/moving`
- `/services/packing`
- `/services/cleaning`
- `/services/unpacking`
- `/services/storage`
- `/how-it-works`
- `/pricing`
- `/about`
- `/contact`
- `/faq`
- `/terms` and `/terms-of-service`
- `/privacy` and `/privacy-policy`
- `/get-a-quote`

Auth:
- `/auth/login`
- `/auth/register`
- `/auth/forgot-password`
- `/auth/reset-password/<token>`
- `/auth/logout`

Booking:
- `/booking/start`
- `/booking/location`
- `/booking/property`
- `/booking/inventory`
- `/booking/services`
- `/booking/schedule`
- `/booking/quote`
- `/booking/review`
- `/booking/payment`

Customer:
- `/customer/dashboard`
- `/customer/bookings`
- `/customer/bookings/<id>`
- `/customer/profile`

Admin:
- `/admin/dashboard`
- `/admin/bookings`
- `/admin/bookings/<id>`
- `/admin/pricing`
- `/admin/trucks`
- `/admin/movers`
- `/admin/cleaners`

## Environment variables

See `.env.example`.

Required:
- `SECRET_KEY`
- `DATABASE_URL` (defaults to SQLite in development)

Optional:
- `STRIPE_SECRET_KEY` for Stripe Checkout
- `STRIPE_WEBHOOK_SECRET` for verifying Stripe webhook signatures
- `NOMINATIM_BASE_URL`
- `OSRM_BASE_URL`
- `OSM_TILE_URL`
- `OSM_USER_AGENT`
- `FLASK_ENV`
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` for password reset email delivery
- `PASSWORD_RESET_MAX_AGE` in seconds (defaults to 3600)

Password reset emails require SMTP settings. Copy `.env.example` to `.env` and configure the mail server credentials for your provider. Reset links expire after the configured lifetime and become invalid after the password is changed.

Stripe deposits use Checkout in NGN. Configure both Stripe values in `.env`, and register `POST /booking/payment/webhook` as a Stripe webhook endpoint for `checkout.session.completed` and `checkout.session.async_payment_succeeded`. For local webhook testing, forward Stripe CLI events to `http://localhost:5000/booking/payment/webhook` and use the CLI-provided signing secret as `STRIPE_WEBHOOK_SECRET`.

## Local setup

1. Create and activate a virtual environment
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment file and set values
   ```bash
   cp .env.example .env
   ```
4. Start the app
   ```bash
   python run.py
   ```
   or:
   ```bash
   PORT=5001 python run.py
   ```

## Initialize database

The app creates the database automatically when it boots using SQLAlchemy.

```bash
python -c "from moving_company import create_app; app = create_app(); print(app.config['SQLALCHEMY_DATABASE_URI'])"
```

## Admin account

For a local SQLite development database, first startup creates a development super-admin:
- Email: `admin@chigo.com`
- Password: `admin123`

For an existing PostgreSQL database, create the first super-admin explicitly. The command prompts for the account details and does not echo the password:

```bash
flask --app run.py create-super-admin
```

This command refuses to run after a super-admin already exists. Sign in at `/admin/login`; a super-admin can create additional admin accounts from the dashboard.

## Run tests

```bash
source .venv/bin/activate
python -m pytest -q
```

## Location system

Location search is local-first: curated Abuja/FCT records in `local_places` are searched by indexed normalized name and bounded alias/keyword queries before a single short-timeout Nominatim fallback is attempted. Results are ranked and cached briefly. The curated hierarchy includes all six FCT Area Councils and common Abuja districts, estates, roads, destinations, institutions, and the international airport. Local place records and admin imports are maintained in the database, not in frontend code.

- Local Chigo location database: immediate autocomplete and authoritative coordinates for curated entries
- Nominatim: bounded fallback geocoding/search when no local result matches
- OSRM: road-network distance and driving duration, cached for repeat requests
- Map UI: not required for readable route details; route summary is displayed directly in the booking form

An unavailable route provider leaves a saved booking with `route_status=PENDING`; quote calculation retries routing server-side and does not accept a client-calculated distance. Location selections persist their LocalPlace foreign key where applicable, original input, normalized hierarchy, coordinates, provider reference, council, district/neighborhood, and route metadata.

### FCT geodata ingestion

PostgreSQL/PostGIS ingestion is an explicit operations task; the web app never downloads or parses source datasets at startup. The importer caches source files under `POSTGIS_DATA_DIR`, uses the geoBoundaries Nigeria ADM1/ADM2 polygons to clip data to the actual FCT and resolve its Area Councils, and upserts source provenance without deleting existing records. OSM uses a disk-backed node-location index. Imported public records remain `imported`; existing Chigo-curated records remain verified. Repeated free-form booking inputs are kept in a separate, noncanonical observation queue for admin review.

Large source downloads retain a `.part` file and retry interrupted reads. When the server supports HTTP byte ranges, a retry resumes at the saved offset; incomplete files are never treated as valid downloads. OSM imports record © OpenStreetMap contributors / ODbL attribution, GeoNames is CC BY 4.0, and geoBoundaries is CC BY 4.0.

Configure `DATABASE_URL` with a PostgreSQL/PostGIS connection, set `DATABASE_FALLBACK_TO_SQLITE=false` and `AUTO_CREATE_SCHEMA=false` for production, and choose a writable `POSTGIS_DATA_DIR`. The web application does not create or alter production tables at startup. For a new database, apply the complete schema before deploying the web process:

```bash
flask --app run.py db upgrade
```

For an existing Chigo database whose 20 model tables and legacy booking/location columns already exist, back up the database and inspect it against the baseline first; then stamp the existing schema at the baseline and apply only the additive compatibility and geodata revisions:

```bash
flask --app run.py db stamp 20261006_baseline_chigo_schema
flask --app run.py db upgrade
```

Do not stamp until the existing schema has been verified. The baseline is for new databases; it is intentionally irreversible and does not drop data.

On this machine, use `FLASK_SKIP_DB_PREFLIGHT=1` only for Alembic commands so the CLI can load while the provider network path is blocked. Do not use it to start the web application:

```bash
FLASK_SKIP_DB_PREFLIGHT=1 flask --app run.py db upgrade
```

Normal PostgreSQL web startup performs a bounded connection and `PostGIS_Full_Version()` check and fails clearly if either is unavailable. SQLite auto-creation remains enabled only by default for non-PostgreSQL local development and isolated tests.

Download the datasets separately (this includes the large Nigeria OSM PBF and does not write to the database):

```bash
python -m moving_company.services.postgis_ingestion --download
```

Run a boundary-only parse first, with no database writes:

```bash
python -m moving_company.services.postgis_ingestion --import-boundaries --dry-run
```

After database connectivity is available and the migration is applied, import the FCT boundary polygons, curated Chigo records, OSM, and GeoNames data:

```bash
python -m moving_company.services.postgis_ingestion --all --rebuild-index
```

Individual source operations are available with `--import-osm`, `--import-geonames`, and `--import-boundaries`. Use `--force` to redownload cached sources and refresh unverified external records. The importer prints counts measured from the downloaded data and database; it does not generate estimated import totals.

The database migration enables PostGIS and `pg_trgm`, creates GiST spatial indexes and trigram autocomplete indexes, and adds the canonical-location, alias, source provenance, administrative boundary, and pending-observation tables. PostgreSQL autocomplete queries those tables first; the existing `LocalPlace` search remains as a compatibility fallback.

Admins can inspect pending inputs at `GET /admin/locations/observations`, approve/reject/promote an observation at `POST /admin/locations/observations/<id>/<approve|reject|promote>`, add aliases at `POST /admin/locations/<id>/aliases`, and merge nearby same-council/same-type canonical locations at `POST /admin/locations/merge`. Merge rejects records that fail coordinate proximity, type, or council checks and preserves source provenance and local booking references.

On this development machine, DNS resolves the PostgreSQL hostname to `104.207.65.190`, but TCP to port `10001` times out. This is before PostgreSQL authentication, so `SELECT version()` and `PostGIS_Full_Version()` cannot run here; no PostgreSQL/PostGIS version is claimed. The provider must permit inbound access from the deployment/client egress IP and the service must be active. Therefore no OSM/GeoNames records have been imported into the production database. The verified public-source dry-runs found 76,332 GeoNames Nigeria rows with 541 points inside FCT, plus the FCT outline and six Area Council boundaries; those are parsed source counts, not database-imported records. The 677 MB Nigeria OSM PBF is cached locally; a complete FCT parse report has not yet been produced.

### API endpoints

- `GET /api/locations/autocomplete?q=Market%20Square%20Jikwoyi`
- `POST /api/locations/geocode` with `{"address": "..."}`
- `POST /api/locations/reverse-geocode` with `{"latitude": 9.0, "longitude": 7.0}`
- `POST /api/locations/validate` with location payload
- `POST /api/locations/route` with pickup/destination coordinates
- `POST /admin/locations/import` (admin/super_admin only) with `{"locations": [{"name":"...","place_type":"estate","area_council":"Abuja Municipal Area Council","parent_name":"Gwarinpa","latitude":9.12,"longitude":7.42,"aliases":["..."],"search_keywords":["..."]}]}`

### Local development

Copy `.env.example` to `.env`, then start the app normally:

```bash
python run.py
```

### Provider notes

Nominatim is used only as a search/geocoding fallback. OSRM provides actual road-distance routing for the booking and quoting flow. If either external provider is unavailable, local matches still work, location details are saved, and a route is marked pending for retry before authoritative quote calculation.

## Known limitations

This is an operational MVP and not a complete replacement for a full enterprise moving platform. Some routes are intentionally scaffolded and functional as a backend/frontend foundation, while advanced features such as expanded audit trails, role-based permission enforcement, assignment conflict detection, and full finance/profitability management are still future phases.

## Recommended next development phase

1. Add complete booking session persistence and draft save logic
2. Expand the pricing engine with admin configuration forms and real-time recalc
3. Implement a proper admin assignment workflow with truck/driver/mover conflict checks
4. Expand secure Stripe payment and webhook monitoring
5. Build photo uploads and incident management with operational review
6. Add review + analytics dashboards
# chigo
