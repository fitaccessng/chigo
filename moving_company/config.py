import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def normalize_database_url(database_url):
    url = make_url(database_url)
    if url.drivername in {'postgres', 'postgresql'}:
        url = url.set(drivername='postgresql+psycopg2')
    return url.render_as_string(hide_password=False)


def is_strong_secret(secret):
    value = str(secret or '')
    return (
        len(value) >= 32
        and len(set(value)) >= 16
        and value not in {'dev-secret-key', 'change-me'}
    )


class Config:
    APP_ENV = os.getenv('APP_ENV', 'development').strip().lower()
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    if APP_ENV == 'production' and not is_strong_secret(SECRET_KEY):
        raise RuntimeError('Production requires a strong SECRET_KEY of at least 32 characters.')
    DB_HOST = os.getenv('POSTGIS_HOST', 'localhost')
    DB_PORT = os.getenv('POSTGIS_PORT', '5432')
    DB_NAME = os.getenv('POSTGIS_DB', 'chigo')
    DB_USER = os.getenv('POSTGIS_USER', 'postgres')
    DB_PASSWORD = os.getenv('POSTGIS_PASSWORD', 'postgres')
    USE_POSTGIS = os.getenv('USE_POSTGIS', 'false').lower() == 'true'
    DATABASE_CONNECT_TIMEOUT = int(os.getenv('DATABASE_CONNECT_TIMEOUT', '5'))
    SQLITE_DATABASE_URI = f'sqlite:///{BASE_DIR / "moving_company.db"}'
    POSTGIS_DATA_DIR = os.getenv('POSTGIS_DATA_DIR', str(BASE_DIR / 'data' / 'geodata'))
    DATABASE_URL = os.getenv('DATABASE_URL')
    DATABASE_FALLBACK_TO_SQLITE = os.getenv('DATABASE_FALLBACK_TO_SQLITE', 'true').lower() == 'true'
    REQUIRE_POSTGRES_DATABASE_URL = os.getenv('CHIGO_REQUIRE_DATABASE_URL', 'false').lower() == 'true'
    if REQUIRE_POSTGRES_DATABASE_URL and not DATABASE_URL:
        raise RuntimeError('Container deployments require a PostgreSQL DATABASE_URL.')
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = normalize_database_url(DATABASE_URL)
        if REQUIRE_POSTGRES_DATABASE_URL and make_url(SQLALCHEMY_DATABASE_URI).get_backend_name() != 'postgresql':
            raise RuntimeError('Container deployments require DATABASE_URL to use PostgreSQL.')
    elif USE_POSTGIS:
        SQLALCHEMY_DATABASE_URI = (
            f'postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
        )
    else:
        if REQUIRE_POSTGRES_DATABASE_URL or not DATABASE_FALLBACK_TO_SQLITE:
            raise RuntimeError(
                'DATABASE_URL is required when DATABASE_FALLBACK_TO_SQLITE is false.'
            )
        SQLALCHEMY_DATABASE_URI = SQLITE_DATABASE_URI
    AUTO_CREATE_SCHEMA = os.getenv(
        'AUTO_CREATE_SCHEMA',
        'false' if make_url(SQLALCHEMY_DATABASE_URI).get_backend_name() == 'postgresql' else 'true',
    ).lower() == 'true'
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': int(os.getenv('DATABASE_POOL_RECYCLE', '300')),
    }
    if make_url(SQLALCHEMY_DATABASE_URI).get_backend_name() == 'postgresql':
        SQLALCHEMY_ENGINE_OPTIONS['connect_args'] = {'connect_timeout': DATABASE_CONNECT_TIMEOUT}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = int(os.getenv('WTF_CSRF_TIME_LIMIT', '14400'))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_IMAGE_SIZE = 10 * 1024 * 1024
    MAX_BOOKING_ATTACHMENTS = 20
    MAX_DECOR_ATTACHMENTS = int(os.getenv('MAX_DECOR_ATTACHMENTS', '10'))
    MAX_DECOR_ATTACHMENT_SIZE = int(os.getenv('MAX_DECOR_ATTACHMENT_SIZE', str(8 * 1024 * 1024)))
    MAX_DECOR_TOTAL_UPLOAD_SIZE = int(os.getenv('MAX_DECOR_TOTAL_UPLOAD_SIZE', str(15 * 1024 * 1024)))
    MAX_DECOR_DRAFTS_PER_CUSTOMER = int(os.getenv('MAX_DECOR_DRAFTS_PER_CUSTOMER', '8'))
    HOME_DECOR_BUDGET_RANGES_JSON = os.getenv(
        'HOME_DECOR_BUDGET_RANGES_JSON',
        '[{"value":"below_250k","label":"Below ₦250,000"},{"value":"250k_500k","label":"₦250,000–₦500,000"},{"value":"500k_1m","label":"₦500,000–₦1,000,000"},{"value":"above_1m","label":"Above ₦1,000,000"},{"value":"not_sure","label":"Not sure — need guidance"}]',
    )
    MAX_VEHICLE_IMAGES = 12
    PASSWORD_RESET_MAX_AGE = int(os.getenv('PASSWORD_RESET_MAX_AGE', '3600'))
    NOMINATIM_BASE_URL = os.getenv('NOMINATIM_BASE_URL', 'https://nominatim.openstreetmap.org')
    PHOTON_BASE_URL = os.getenv('PHOTON_BASE_URL', 'https://photon.komoot.io')
    OSRM_BASE_URL = os.getenv('OSRM_BASE_URL', 'https://router.project-osrm.org')
    OSM_TILE_URL = os.getenv('OSM_TILE_URL', 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')
    OSM_USER_AGENT = os.getenv('OSM_USER_AGENT', 'ChigoRelocations/1.0')
    CANONICAL_ORIGIN = os.getenv('CANONICAL_ORIGIN', '').strip().rstrip('/')
    GA4_MEASUREMENT_ID = os.getenv('GA4_MEASUREMENT_ID', '').strip()
    GTM_CONTAINER_ID = os.getenv('GTM_CONTAINER_ID', '').strip()
    GOOGLE_MAPS_BROWSER_API_KEY = os.getenv('GOOGLE_MAPS_BROWSER_API_KEY')
    GOOGLE_MAPS_SERVER_API_KEY = os.getenv('GOOGLE_MAPS_SERVER_API_KEY')
    GOOGLE_MAPS_MAP_ID = os.getenv('GOOGLE_MAPS_MAP_ID')
    GOOGLE_MAPS_API_KEY = GOOGLE_MAPS_SERVER_API_KEY
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.getenv(
        'SESSION_COOKIE_SECURE', 'true' if REQUIRE_POSTGRES_DATABASE_URL else 'false'
    ).lower() == 'true'
    SESSION_COOKIE_SAMESITE = 'Lax'
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'chigomove.online')
    MAIL_PORT = int(os.getenv('MAIL_PORT', '465'))
    MAIL_USE_SSL = os.getenv('MAIL_USE_SSL', 'true').lower() == 'true'
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'false').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', 'hello@chigomove.online')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', 'hello@chigomove.online')
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
    DISPATCH_OFFER_TTL_MINUTES = max(5, int(os.getenv('DISPATCH_OFFER_TTL_MINUTES', '20')))
    AI_PROVIDER = os.getenv('CHIGO_AI_PROVIDER', '').strip().lower()
    AI_API_BASE_URL = os.getenv('CHIGO_AI_API_BASE_URL', '').strip().rstrip('/')
    AI_API_KEY = os.getenv('CHIGO_AI_API_KEY')
    AI_MODEL = os.getenv('CHIGO_AI_MODEL', '').strip()
    AI_REQUEST_TIMEOUT_SECONDS = max(1, int(os.getenv('CHIGO_AI_REQUEST_TIMEOUT_SECONDS', '12')))
    AI_DAILY_REQUEST_LIMIT = max(0, int(os.getenv('CHIGO_AI_DAILY_REQUEST_LIMIT', '0')))
