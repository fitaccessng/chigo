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
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` for Google OpenID Connect sign-in
- `APP_ENV` (`development`, `staging`, or `production`)
- `CANONICAL_ORIGIN` (the HTTPS production origin, e.g. `https://www.chigomove.online`)
- `GA4_MEASUREMENT_ID` (production GA4 web-stream ID; leave empty outside production)
- `GTM_CONTAINER_ID` (optional production GTM container ID; leave empty unless using GTM)
- `SESSION_COOKIE_SECURE` (set to `true` for HTTPS production)
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_SSL`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` for welcome and password reset email delivery
- `PASSWORD_RESET_MAX_AGE` in seconds (defaults to 3600)

Account welcome and password reset emails use the configured SMTP account. The Chigo mailbox defaults are `MAIL_SERVER=chigomove.online`, `MAIL_PORT=465`, `MAIL_USE_SSL=true`, `MAIL_USE_TLS=false`, and `MAIL_USERNAME=hello@chigomove.online`; set `MAIL_PASSWORD` as a secret in the hosting platform. Rotate the mailbox password if it has been shared outside the password manager. Reset links expire after the configured lifetime and become invalid after the password is changed. Mail delivery failures do not undo account creation, and reset requests keep the same non-enumerating response.

### Google sign-in

Google sign-in uses OpenID Connect with only `openid email profile`. Google identity is keyed by its stable `sub` claim. A verified Google email that already belongs to a password account is never linked automatically; sign into that Chigo account and use **Link Google account** in account settings. New Google accounts receive the customer role only.

Configure Google Cloud Console:

1. Create or select a Google Cloud project and configure its OAuth consent screen. Add `chigomove.online` as an authorized domain for production. Add test users while the consent screen is in testing, or publish it when ready.
2. Create an OAuth client ID with application type **Web application**.
3. Add the deployed origin (for example `https://www.chigomove.online`) to Authorized JavaScript origins if required by the console. The client secret is server-side only and is never placed in browser code.
4. Add the exact production redirect URI `https://www.chigomove.online/auth/google/callback`. Add a separate exact local redirect URI such as `http://localhost:5000/auth/google/callback` for development.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` as encrypted hosting environment variables. `GOOGLE_REDIRECT_URI` must exactly match the URI registered in Google Cloud. Set `SESSION_COOKIE_SECURE=true` on HTTPS production deployments.
6. Apply the additive migration with `flask --app run.py db upgrade`. It creates only the OAuth identity table and preserves existing user and booking data.

The OAuth callback validates state, uses S256 PKCE, and relies on Authlib OIDC validation for the Google ID-token signature, issuer, audience, expiry, and nonce. The active booking request is retained through the login session refresh.

## Analytics, Search Console, SEO, and performance

### Environment separation

Set these values independently for each environment in its hosting or local environment configuration. Do not commit actual tracking IDs or verification tokens:

- Local development: `APP_ENV=development`, empty `CANONICAL_ORIGIN`, `GA4_MEASUREMENT_ID`, and `GTM_CONTAINER_ID`.
- Staging: use `APP_ENV=staging`, a staging canonical origin only if staging is intentionally crawlable, and leave analytics IDs empty unless staging has a separate property/container.
- Production: use `APP_ENV=production`, `CANONICAL_ORIGIN=https://www.chigomove.online` (replace with the selected canonical host), and the real Google IDs supplied by the relevant Google account.

Tracking is disabled unless the environment is production, the canonical origin is a valid HTTPS origin, and the configured ID has the expected Google format. A production public page shows an opt-in consent prompt. Google tags do not load on login, account, booking, checkout, payment callback, API, or admin pages. Events created during a private workflow are retained in the signed application session and delivered on a later eligible public page visit, only after consent. Delivery uses an event ID and browser-side acknowledgement to prevent repeated callbacks or refreshes from resending conversions. Conversion parameters are limited to event IDs and, where relevant, the validated service category; no customer, address, booking-reference, payment, or private-URL fields are sent.

Events implemented: `booking_started`, `location_completed`, `inventory_completed`, `services_selected`, `schedule_completed`, `quote_generated`, `checkout_started`, `booking_completed`, and `payment_success`. `payment_success` and `booking_completed` are queued only after Stripe confirms the expected paid Checkout session. They are not inferred from a return-page visit. If GTM is configured, the direct GA4 tag is suppressed; configure one GA4 Google tag in GTM and use the documented `dataLayer` event names, avoiding a second page-view trigger.

### Create and configure GA4

1. In Google Analytics, create or select the production property and create a Web data stream for the canonical HTTPS origin.
2. Copy the Measurement ID shown for that stream (format `G-XXXXXXXXXX`). Do not use a Measurement Protocol API secret as the Measurement ID.
3. In the production hosting environment, set `APP_ENV=production`, `CANONICAL_ORIGIN`, and `GA4_MEASUREMENT_ID` as environment variables. Leave GA4 IDs unset in local and staging environments unless using separate properties.
4. For GTM instead, create a web container, configure `GTM_CONTAINER_ID` with the real container ID, and configure a single GA4 tag in that container. Do not configure duplicate page-view triggers or another copy of the same tag. When a GTM ID is present, the application does not directly initialize `GA4_MEASUREMENT_ID`.
5. Accept analytics on a production public page and confirm the `page_view` plus the approved event names in GA4 DebugView/Realtime and the browser Network panel. Rejecting optional analytics must prevent Google tag requests. Booking conversion events are queued while private pages are open and flush only on a later consented public page.

### Verify and submit in Search Console

1. Add a **Domain** property for the real domain in Google Search Console.
2. Copy the DNS TXT verification record supplied by Google into the domain DNS provider exactly as shown. Do not paste the verification value into application code or commit it.
3. Wait for DNS propagation and use Search Console's Verify action. Verification is not performed by the application.
4. Set `CANONICAL_ORIGIN` to the chosen HTTPS host. `/robots.txt` references `/sitemap.xml` when the canonical origin is configured. The sitemap contains only the allowlisted public pages; customer, booking, checkout, API, and admin URLs are excluded.
5. In Search Console, submit `https://<canonical-host>/sitemap.xml`, inspect public URLs with URL Inspection, and request indexing where appropriate. Search Console processing and indexing are external Google operations and are not guaranteed by deployment.

The app emits page-specific titles and descriptions, canonical URLs without query strings, Open Graph metadata, `Organization` data using the published Abuja service area and existing public contact information, `Service` data on service pages, and breadcrumbs where applicable. It intentionally does not emit a street-address `LocalBusiness` listing: the repository has no verified public street address. Confirm any future business-profile address and eligibility before adding one. Login, account, booking, checkout, API, and admin views are marked `noindex, nofollow`.

### Performance checks

The home/about public imagery uses explicit intrinsic dimensions, asynchronous decoding, smaller quality/width variants, and lazy loading below the hero. The primary hero image is prioritized; other images defer loading. To test a deployed URL, open [Google PageSpeed Insights](https://pagespeed.web.dev/), enter the canonical homepage and key public service URLs, and review mobile and desktop Core Web Vitals. Re-test after production asset or template changes; the current Tailwind CDN remains a third-party render-time dependency and is a known optimization opportunity.

Google tag delivery, consent, domain verification, sitemap ingestion, and indexing require manual configuration and verification in the corresponding Google properties. The code and tests only confirm application-side behavior.

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

## Container deployment

The included `Dockerfile` starts the Flask app with Gunicorn on port `8000` and requires a PostgreSQL `DATABASE_URL`, even if a platform overrides the local SQLite fallback setting. Set `DATABASE_URL` to a reachable PostgreSQL/PostGIS database in the hosting platform and apply migrations before deploying. Without a database URL, the container exits with a clear configuration error instead of trying to write SQLite into the container filesystem. If the hosting platform overrides the image command, set its start command to:

```bash
gunicorn --bind "0.0.0.0:${PORT:-8000}" --workers 1 --threads 4 --timeout 120 run:app
```

The `run:app` target avoids shell parsing issues and uses the port provided by the hosting platform, defaulting to `8000`. Configure production environment variables, including `DATABASE_URL` and `SECRET_KEY`, in the hosting platform.

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
