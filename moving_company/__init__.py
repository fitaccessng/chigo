import os

import click
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.engine import make_url

from .config import Config
from .extensions import bcrypt, csrf, db, login_manager, migrate
from .models import Booking, User
from .routes.admin import admin_bp
from .routes.auth import auth_bp
from .routes.booking_engine import booking_api_bp, booking_bp
from .routes.customer import customer_bp
from .routes.locations import locations_bp
from .routes.public import public_bp
from .routes.support_admin import support_admin_bp
from .routes.support_chat import support_bp
from .services.google_oauth_service import configure_google_oauth


login_manager.login_view = 'auth.login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def validate_database_connection(app):
    database_url = app.config['SQLALCHEMY_DATABASE_URI']
    backend = make_url(database_url).get_backend_name()
    if backend != 'postgresql':
        return
    try:
        with app.app_context():
            engine = db.engines[None] if hasattr(db, 'engines') else db.engine
            with engine.connect() as connection:
                connection.execute(text('SELECT 1')).scalar_one()
                connection.execute(text('SELECT PostGIS_Full_Version()')).scalar_one()
    except SQLAlchemyError as error:
        cause = getattr(error, 'orig', error)
        detail = str(cause).splitlines()[0][:240]
        raise RuntimeError(
            f'PostgreSQL/PostGIS is required but unavailable at the configured host/port: {detail}'
        ) from error


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(Config)
    if testing:
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
        app.config['AUTO_CREATE_SCHEMA'] = True
    configure_google_oauth(app)
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    if not testing and not os.getenv('FLASK_SKIP_DB_PREFLIGHT'):
        validate_database_connection(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(booking_bp, url_prefix='/booking')
    app.register_blueprint(booking_api_bp)
    app.register_blueprint(customer_bp, url_prefix='/customer')
    app.register_blueprint(locations_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(support_admin_bp, url_prefix='/admin')
    app.register_blueprint(support_bp)

    @app.errorhandler(404)
    def not_found(_error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template('errors/403.html'), 403

    @app.errorhandler(CSRFError)
    def handle_csrf_error(_error):
        if request.accept_mimetypes.best == 'application/json':
            return {'success': False, 'error': 'Your session expired. Refresh the page and try again.'}, 400

        view_args = request.view_args or {}
        request_id = view_args.get('request_id') or session.get('active_booking_request_id')
        flash('Your session expired for security. Please try again.', 'error')

        if request.path.startswith('/booking/') or request.path.startswith('/booking'):
            if request_id:
                booking = Booking.query.filter_by(booking_request_id=request_id).first()
                if booking is not None:
                    stage = view_args.get('stage')
                    if stage not in {'property', 'inventory', 'services', 'schedule', 'quote', 'review'}:
                        stage = 'payment' if request.endpoint == 'booking.payment' else 'property'
                    return redirect(url_for('booking.workflow', request_id=request_id, stage=stage))
            return redirect(url_for('booking.start', service_type='residential'))

        target = request.referrer or url_for('public.home')
        return redirect(target)

    @app.errorhandler(500)
    def server_error(_error):
        return render_template('errors/500.html'), 500

    @app.cli.command('create-super-admin')
    def create_super_admin_command():
        """Create the first super-admin account for this database."""
        with app.app_context():
            if User.query.filter_by(role='super_admin').first():
                raise click.ClickException('A super-admin account already exists.')

            full_name = click.prompt('Full name').strip()
            email = click.prompt('Email').strip().lower()
            if len(full_name) < 2 or '@' not in email:
                raise click.ClickException('Enter a valid name and email address.')

            password = click.prompt('Password', hide_input=True, confirmation_prompt=True)
            if len(password) < 8:
                raise click.ClickException('Password must be at least 8 characters.')
            if User.query.filter_by(email=email).first():
                raise click.ClickException('An account with that email already exists.')

            name_parts = full_name.split(maxsplit=1)
            user = User(
                first_name=name_parts[0],
                last_name=name_parts[1] if len(name_parts) > 1 else '',
                email=email,
                phone='',
                role='super_admin',
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            click.echo(f'Super-admin account created for {email}.')

    @app.cli.command('seed-demo')
    def seed_demo_command():
        """Seed isolated development fleet, partner, mover, and team records."""
        from .services.demo_seed import seed_demo_data
        with app.app_context():
            report = seed_demo_data()
            db.session.commit()
            click.echo(
                f"Seeded {report['partners']} truck partners, {report['vehicles']} vehicles, "
                f"{report['movers']} movers, and {report['teams']} teams."
            )

    @app.cli.command('clear-demo')
    def clear_demo_command():
        """Remove only records explicitly marked as demo data."""
        from .services.demo_seed import clear_demo_data
        with app.app_context():
            report = clear_demo_data()
            db.session.commit()
            click.echo(
                f"Cleared {report['partners']} truck partners, {report['vehicles']} vehicles, "
                f"{report['movers']} movers, and {report['teams']} teams."
            )

    @app.cli.command('reset-demo')
    def reset_demo_command():
        """Clear demo records and recreate the development fleet."""
        from .services.demo_seed import clear_demo_data, seed_demo_data
        with app.app_context():
            clear_demo_data()
            report = seed_demo_data()
            db.session.commit()
            click.echo(
                f"Reset demo data: {report['partners']} truck partners, {report['vehicles']} vehicles, "
                f"{report['movers']} movers, and {report['teams']} teams."
            )

    with app.app_context():
        if app.config.get('AUTO_CREATE_SCHEMA'):
            try:
                db.create_all()
            except OperationalError as error:
                if 'already exists' not in str(error).casefold():
                    raise
                db.session.rollback()
                db.create_all()
            upgrade_booking_schema()
            seed_defaults()

    return app


def seed_defaults():
    from .models import LocalPlace, ServicePricing, User, VehicleType
    from .services.location_service import seed_fct_locations
    from .services.vehicle_catalogue import DEFAULT_VEHICLE_TYPES

    if not User.query.filter_by(email='admin@chigo.com').first():
        admin = User(
            first_name='Admin',
            last_name='User',
            email='admin@chigo.com',
            phone='+2348000000000',
            role='super_admin',
        )
        admin.set_password('admin123')
        db.session.add(admin)

    default_pricing = {
        'base_moving_fee': '25000',
        'price_per_km': '800',
        'property_surcharge': '15000',
        'floor_surcharge': '5000',
        'packing_partial': '18000',
        'packing_full': '35000',
        'cleaning_move_out': '15000',
        'cleaning_move_in': '15000',
        'cleaning_both': '25000',
        'unpacking': '12000',
        'service_assembly_yes': '12000',
        'service_disassembly_yes': '10000',
        'storage_week': '8000',
        'special_handling': '15000',
        'weekend_surcharge': '10000',
        'holiday_surcharge': '20000',
        'tax_rate': '0',
        'deposit_percentage': '30',
        'additional_mover_fee': '8000',
        'base_mover_hourly_fee': '4000',
        'access_difficulty_surcharge': '7000',
        'outside_service_area_surcharge': '0',
        'volume_safety_factor': '1.15',
    }
    for key, value in default_pricing.items():
        if not ServicePricing.query.filter_by(key=key).first():
            db.session.add(ServicePricing(key=key, value=float(value), label=key.replace('_', ' ').title()))

    for name, slug, description, body_type, capacity, payload, length, width, height, icon, best_for, base_price, per_km, minimum, order in DEFAULT_VEHICLE_TYPES:
        if not VehicleType.query.filter_by(slug=slug).first():
            db.session.add(VehicleType(
                name=name, slug=slug, description=description, body_type=body_type,
                capacity_m3=capacity, max_payload_kg=payload,
                length_m=length, width_m=width, height_m=height,
                icon=icon, best_for=best_for, base_price=base_price,
                price_per_km=per_km, minimum_price=minimum, is_active=True,
                sort_order=order,
            ))

    default_places = [
        ('Jikwoyi', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Jikwoyi', 'Jikwoyi', 'Jikwoyi, Abuja, Federal Capital Territory, Nigeria', 9.072, 7.420, 'local', 'abuja-jikwoyi', True, 'Jikoyi,Jikowyi,Jikwoyi Market,Market Square Jikwoyi,Market Square Jikowyi'),
        ('Gwarinpa', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Gwarinpa', 'Gwarinpa', 'Gwarinpa, Abuja, Federal Capital Territory, Nigeria', 9.118, 7.423, 'local', 'abuja-gwarinpa', True, 'Gwarimpa,Gwarinpa Estate,Gwarinpa 1st Avenue,Gwarinpa 2nd Avenue'),
        ('Wuse', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Wuse', 'Wuse', 'Wuse, Abuja, FCT', 9.056, 7.487, 'local', 'abuja-wuse', True, 'Wuse 2,Wuse II,Wuse Market'),
        ('Maitama', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Maitama', 'Maitama', 'Maitama, Abuja, FCT', 9.088, 7.491, 'local', 'abuja-maitama', True, 'Maitama'),
        ('Jabi', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Jabi', 'Jabi', 'Jabi, Abuja, FCT', 9.072, 7.418, 'local', 'abuja-jabi', True, 'Jabi Lake,Jabi Lake Mall'),
        ('Kubwa', 'DISTRICT', 'Bwari', 'Bwari Area Council', 'Kubwa', 'Kubwa', 'Kubwa, Abuja, FCT', 9.169, 7.385, 'local', 'abuja-kubwa', True, 'Kubwa Express,Kubwa Expressway'),
        ('Kuje', 'DISTRICT', 'Kuje', 'Kuje Area Council', 'Kuje', 'Kuje', 'Kuje, Abuja, FCT', 8.879, 7.240, 'local', 'abuja-kuje', True, 'Kuje Central'),
        ('Lugbe', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Lugbe', 'Lugbe', 'Lugbe, Abuja, FCT', 8.973, 7.383, 'local', 'abuja-lugbe', True, 'Lugbe'),
        ('Nyanya', 'DISTRICT', 'AMAC', 'Abuja Municipal Area Council', 'Nyanya', 'Nyanya', 'Nyanya, Abuja, FCT', 9.038, 7.565, 'local', 'abuja-nyanya', True, 'Nyanya'),
        ('Market Square', 'LANDMARK', 'AMAC', 'Abuja Municipal Area Council', 'Jikwoyi', 'Karu corridor', 'Market Square, Jikwoyi, Abuja, FCT', 9.082, 7.440, 'local', 'abuja-market-square', True, 'Market Square Jikwoyi,Market Square Jikowyi,Market Square Jikoyi'),
    ]
    for name, place_type, area_council, district, neighborhood, area, label, latitude, longitude, provider, provider_place_id, verified, aliases in default_places:
        normalized = name.strip()
        if not LocalPlace.query.filter_by(normalized_name=normalized).first():
            db.session.add(LocalPlace(
                name=name,
                normalized_name=normalized,
                place_type=place_type,
                area_council=area_council,
                district=district,
                neighborhood=neighborhood,
                area=area,
                lga=area_council,
                aliases=aliases,
                latitude=latitude,
                longitude=longitude,
                provider=provider,
                provider_place_id=provider_place_id,
                verified=verified,
                active=True,
            ))

    seed_fct_locations()
    db.session.commit()


def upgrade_booking_schema():
    additions = {
        'bookings': {
            'booking_request_id': 'VARCHAR(32)', 'workflow_state': "VARCHAR(40) DEFAULT 'CONFIRMED' NOT NULL",
            'quote_status': "VARCHAR(30) DEFAULT 'Draft' NOT NULL", 'workflow_data': "JSON DEFAULT '{}' NOT NULL",
            'calculation_data': "JSON DEFAULT '{}' NOT NULL", 'estimated_duration_minutes': 'INTEGER',
            'flexible_date': 'BOOLEAN DEFAULT FALSE NOT NULL', 'flexible_time': 'BOOLEAN DEFAULT FALSE NOT NULL',
            'schedule_feasible': 'BOOLEAN', 'manually_overridden_total': 'FLOAT', 'price_override_reason': 'TEXT',
            'pickup_country': "VARCHAR(100) DEFAULT 'Nigeria' NOT NULL", 'destination_country': "VARCHAR(100) DEFAULT 'Nigeria' NOT NULL",
            'pickup_area_council': 'VARCHAR(100)', 'pickup_district': 'VARCHAR(120)',
            'pickup_neighborhood': 'VARCHAR(120)', 'pickup_location_type': 'VARCHAR(60)',
            'pickup_original_input': 'VARCHAR(500)', 'destination_area_council': 'VARCHAR(100)',
            'destination_district': 'VARCHAR(120)', 'destination_neighborhood': 'VARCHAR(120)',
            'destination_location_type': 'VARCHAR(60)', 'destination_original_input': 'VARCHAR(500)',
            'pickup_normalized_location': 'VARCHAR(500)', 'destination_normalized_location': 'VARCHAR(500)',
            'pickup_local_place_id': 'INTEGER REFERENCES local_places(id)',
            'destination_local_place_id': 'INTEGER REFERENCES local_places(id)',
            'route_duration_minutes': 'FLOAT', 'service_area_status': 'VARCHAR(40)',
            'route_provider': 'VARCHAR(40)', 'route_status': "VARCHAR(30) DEFAULT 'PENDING' NOT NULL",
            'route_calculated_at': 'TIMESTAMP',
            'pickup_area': 'VARCHAR(120)', 'pickup_formatted_address': 'TEXT', 'pickup_latitude': 'FLOAT DEFAULT 0',
            'pickup_longitude': 'FLOAT DEFAULT 0', 'pickup_place_id': 'VARCHAR(255)', 'pickup_provider': "VARCHAR(50) DEFAULT 'manual'",
            'pickup_provider_raw_id': 'VARCHAR(255)', 'pickup_access': "VARCHAR(30) DEFAULT 'Not sure'",
            'pickup_inside_estate': 'BOOLEAN DEFAULT FALSE', 'pickup_narrow_road': 'BOOLEAN DEFAULT FALSE',
            'pickup_parking_close': 'BOOLEAN DEFAULT TRUE', 'pickup_floor': "VARCHAR(20) DEFAULT 'Ground'",
            'pickup_stairs': 'BOOLEAN DEFAULT FALSE', 'destination_area': 'VARCHAR(120)', 'destination_formatted_address': 'TEXT',
            'destination_latitude': 'FLOAT DEFAULT 0', 'destination_longitude': 'FLOAT DEFAULT 0', 'destination_place_id': 'VARCHAR(255)',
            'destination_provider': "VARCHAR(50) DEFAULT 'manual'", 'destination_provider_raw_id': 'VARCHAR(255)',
            'destination_access': "VARCHAR(30) DEFAULT 'Not sure'", 'destination_inside_estate': 'BOOLEAN DEFAULT FALSE',
            'destination_narrow_road': 'BOOLEAN DEFAULT FALSE', 'destination_parking_close': 'BOOLEAN DEFAULT TRUE',
            'destination_floor': "VARCHAR(20) DEFAULT 'Ground'", 'destination_stairs': 'BOOLEAN DEFAULT FALSE',
            'distance_km': 'FLOAT DEFAULT 0', 'number_of_bedrooms': 'INTEGER DEFAULT 0',
            'number_of_living_rooms': 'INTEGER DEFAULT 0', 'number_of_kitchens': 'INTEGER DEFAULT 0',
            'number_of_bathrooms': 'INTEGER DEFAULT 0', 'number_of_floors': 'INTEGER DEFAULT 1',
            'estimated_volume_m3': 'FLOAT DEFAULT 0', 'estimated_weight_kg': 'FLOAT DEFAULT 0',
            'recommended_vehicle_type_id': 'INTEGER REFERENCES vehicle_types(id)',
            'assigned_vehicle_type_id': 'INTEGER REFERENCES vehicle_types(id)',
            'assigned_partner_vehicle_id': 'INTEGER REFERENCES partner_vehicles(id)',
            'vehicle_review_required': 'BOOLEAN DEFAULT FALSE', 'vehicle_override_reason': 'TEXT',
            'overridden_by': 'INTEGER REFERENCES users(id)', 'overridden_at': 'TIMESTAMP',
        },
        'booking_items': {
            'size': "VARCHAR(20) DEFAULT 'medium'", 'special_handling': 'BOOLEAN DEFAULT FALSE',
            'estimated_volume_m3': 'FLOAT DEFAULT 0', 'estimated_weight_kg': 'FLOAT DEFAULT 0',
            'details': "JSON DEFAULT '{}' NOT NULL", 'dimensions_cm': 'JSON', 'photo_path': 'VARCHAR(255)',
        },
        'payments': {
            'gateway': 'VARCHAR(40)', 'authorization_url': 'TEXT',
            'currency': "VARCHAR(3) DEFAULT 'NGN' NOT NULL", 'transaction_status': 'VARCHAR(30)',
        },
        'local_places': {
            'parent_id': 'INTEGER REFERENCES local_places(id)', 'state': "VARCHAR(100) DEFAULT 'Federal Capital Territory' NOT NULL",
            'country': "VARCHAR(100) DEFAULT 'Nigeria' NOT NULL", 'address': 'TEXT',
            'search_keywords': 'TEXT', 'popularity_score': 'FLOAT DEFAULT 0 NOT NULL',
            'place_type': "VARCHAR(60) DEFAULT 'AREA'", 'area_council': "VARCHAR(80) DEFAULT 'AMAC'",
            'district': 'VARCHAR(120)', 'neighborhood': 'VARCHAR(120)', 'area': 'VARCHAR(120)',
            'lga': 'VARCHAR(80)', 'aliases': 'TEXT', 'latitude': 'FLOAT DEFAULT 0',
            'longitude': 'FLOAT DEFAULT 0', 'provider': "VARCHAR(50) DEFAULT 'local'",
            'provider_place_id': 'VARCHAR(255)', 'verified': 'BOOLEAN DEFAULT FALSE', 'active': 'BOOLEAN DEFAULT TRUE',
        },
    }
    with db.engine.begin() as connection:
        for table, columns in additions.items():
            for name, sql_type in columns.items():
                present = {column['name'] for column in inspect(connection).get_columns(table)}
                if name not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_bookings_booking_request_id ON bookings (booking_request_id)'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_local_places_active_normalized_name ON local_places (active, normalized_name)'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_local_places_council_normalized_name ON local_places (area_council, normalized_name)'))
