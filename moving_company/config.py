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


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
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
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = normalize_database_url(DATABASE_URL)
    elif USE_POSTGIS:
        SQLALCHEMY_DATABASE_URI = (
            f'postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
        )
    else:
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
    MAX_VEHICLE_IMAGES = 12
    PASSWORD_RESET_MAX_AGE = int(os.getenv('PASSWORD_RESET_MAX_AGE', '3600'))
    NOMINATIM_BASE_URL = os.getenv('NOMINATIM_BASE_URL', 'https://nominatim.openstreetmap.org')
    PHOTON_BASE_URL = os.getenv('PHOTON_BASE_URL', 'https://photon.kom')
    OSRM_BASE_URL = os.getenv('OSRM_BASE_URL', 'https://router.project-osrm.org')
    OSM_TILE_URL = os.getenv('OSM_TILE_URL', 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')
    OSM_USER_AGENT = os.getenv('OSM_USER_AGENT', 'ChigoRelocations/1.0')
    GOOGLE_MAPS_BROWSER_API_KEY = os.getenv('GOOGLE_MAPS_BROWSER_API_KEY')
    GOOGLE_MAPS_SERVER_API_KEY = os.getenv('GOOGLE_MAPS_SERVER_API_KEY')
    GOOGLE_MAPS_MAP_ID = os.getenv('GOOGLE_MAPS_MAP_ID')
    GOOGLE_MAPS_API_KEY = GOOGLE_MAPS_SERVER_API_KEY
    MAIL_SERVER = os.getenv('MAIL_SERVER')
    MAIL_PORT = int(os.getenv('MAIL_PORT', '587'))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', 'hello@chigo.com')
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
