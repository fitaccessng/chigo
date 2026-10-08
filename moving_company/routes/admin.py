from functools import wraps
from datetime import date, datetime, time, timedelta
import json

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required, login_user
from sqlalchemy import case, func, or_, text
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..forms import AdminAccountForm, InventoryItemForm, LoginForm, MoverForm, PricingForm, TruckPartnerForm
from ..models import AuditLog, Booking, BookingAssignment, BookingPhoto, Cleaner, IncidentReport, InventoryItem, Mover, Payment, ServicePricing, SupportRequest, Team, Truck, TruckPartner, User, VehicleImage, VehicleType
from ..services.assignment_service import save_assignment
from ..services.booking_engine_service import apply_price_override, calculate_quote, record_event
from ..services.image_upload_service import delete_image, save_image
from ..services.location_service import (
    add_location_alias, merge_canonical_locations, merge_local_places,
    normalize_location_name, review_location_observation, upsert_location_records,
)

admin_bp = Blueprint('admin', __name__)

PRICING_KEYS = (
    'base_moving_fee', 'price_per_km', 'property_surcharge', 'floor_surcharge',
    'packing_partial', 'packing_full', 'cleaning_move_out', 'cleaning_move_in',
    'cleaning_both', 'unpacking', 'service_assembly_yes', 'service_disassembly_yes',
    'storage_week', 'special_handling', 'weekend_surcharge', 'holiday_surcharge',
    'additional_mover_fee', 'base_mover_hourly_fee', 'access_difficulty_surcharge',
    'outside_service_area_surcharge', 'tax_rate', 'deposit_percentage', 'volume_safety_factor',
)

RIDE_STATUS_TRANSITIONS = {
    'Draft': {'Cancelled'},
    'Booking Received': {'Cancelled'},
    'Confirmed': {'Cancelled'},
    'Assigned': {'In Progress', 'En Route', 'Cancelled'},
    'In Progress': {'En Route', 'Arrived', 'Loading', 'Cancelled'},
    'En Route': {'Arrived', 'Cancelled'},
    'Arrived': {'Loading', 'Cancelled'},
    'Loading': {'In Transit', 'Cancelled'},
    'In Transit': {'Unloading', 'Cancelled'},
    'Unloading': {'Completed', 'Cancelled'},
}

ROLE_PERMISSIONS = {
    'super_admin': frozenset({'*'}),
    'admin': frozenset({'*'}),
    'operations_manager': frozenset({
        'dashboard', 'bookings', 'jobs', 'schedule', 'customers', 'fleet',
        'movers', 'locations', 'quotes', 'services', 'inventory', 'support',
        'reviews', 'reports',
    }),
    'dispatcher': frozenset({'dashboard', 'bookings', 'jobs', 'schedule', 'fleet', 'movers', 'locations'}),
    'finance_manager': frozenset({'dashboard', 'quotes', 'payments', 'finance', 'reports'}),
    'customer_support': frozenset({'dashboard', 'customers', 'bookings', 'reviews', 'support', 'notifications'}),
    'fleet_manager': frozenset({'dashboard', 'fleet', 'movers', 'schedule'}),
    'content_marketing_manager': frozenset({'dashboard', 'services', 'promotions'}),
}


def has_admin_permission(role, permission):
    permissions = ROLE_PERMISSIONS.get(role, frozenset())
    return '*' in permissions or permission in permissions


@admin_bp.app_context_processor
def inject_admin_permissions():
    return {'can_admin': lambda permission: current_user.is_authenticated and has_admin_permission(current_user.role, permission)}


def write_admin_audit(action, entity, entity_id=None, before=None, after=None):
    db.session.add(AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_value=json.dumps(before, default=str) if before is not None else None,
        after_value=json.dumps({'value': after, 'ip': request.remote_addr}, default=str) if after is not None else None,
    ))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and has_admin_permission(current_user.role, 'dashboard'):
        return redirect(url_for('admin.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and has_admin_permission(user.role, 'dashboard') and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('admin.dashboard'))
        flash('Invalid admin email or password.', 'error')
    return render_template('admin/login.html', form=form)


@admin_bp.route('/create-account', methods=['GET', 'POST'])
def create_account():
    if not current_user.is_authenticated:
        return redirect(url_for('admin.login'))
    if current_user.role != 'super_admin':
        abort(403)

    form = AdminAccountForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
        else:
            name_parts = form.full_name.data.strip().split(maxsplit=1)
            user = User(
                first_name=name_parts[0],
                last_name=name_parts[1] if len(name_parts) > 1 else '',
                email=email,
                phone='',
                role=form.role.data,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            write_admin_audit('admin.account_created', 'user', user.id, after={
                'email': email, 'role': user.role,
            })
            db.session.commit()
            flash('Admin account created.', 'success')
            return redirect(url_for('admin.dashboard'))
    return render_template('admin/create_account.html', form=form)


def admin_required(permission='dashboard'):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_admin_permission(current_user.role, permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


@admin_bp.route('/locations/import', methods=['POST'])
@login_required
@admin_required('locations')
def import_locations():
    payload = request.get_json(silent=True) or {}
    records = payload.get('locations')
    if not isinstance(records, list) or not records or len(records) > 500:
        return jsonify({'success': False, 'error': 'Provide between 1 and 500 location records.'}), 400
    try:
        imported = upsert_location_records(records)
    except (TypeError, ValueError) as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422
    return jsonify({'success': True, 'imported': imported})


@admin_bp.route('/locations/observations')
@login_required
@admin_required('locations')
def location_observations():
    if db.engine.dialect.name != 'postgresql':
        from ..models import LocalPlace
        rows = LocalPlace.query.filter_by(active=True, verified=False).order_by(LocalPlace.popularity_score.desc()).limit(500).all()
        return jsonify({'success': True, 'observations': [{
            'id': row.id, 'raw_input': row.name, 'latitude': row.latitude,
            'longitude': row.longitude, 'area_council': row.area_council,
            'district': row.district, 'nearest_landmark': None, 'nearest_road': None,
            'resolution_source': row.provider, 'confidence': None,
            'usage_count': 0, 'status': 'pending_review',
        } for row in rows], 'storage': 'local-places'})
    try:
        rows = db.session.execute(text("""
                     SELECT id, normalized_input, raw_input, formatted_address, provider_reference,
                         latitude, longitude, area_council, district, nearest_landmark,
                         nearest_road, resolution_source, route_resolution, confidence, usage_count,
                   status, first_seen_at, last_seen_at
            FROM location_observations
            WHERE status = 'pending_review'
            ORDER BY usage_count DESC, last_seen_at DESC
            LIMIT 500
        """)).mappings().all()
    except Exception:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Location review migration has not been applied.'}), 503
    return jsonify({'success': True, 'observations': [dict(row) for row in rows]})


@admin_bp.route('/locations/observations/<int:observation_id>/<action>', methods=['POST'])
@login_required
@admin_required('locations')
def review_location(observation_id, action):
    payload = request.get_json(silent=True) or {}
    if db.engine.dialect.name != 'postgresql':
        from ..models import LocalPlace
        place = LocalPlace.query.filter_by(id=observation_id).first()
        if place is None:
            return jsonify({'success': False, 'error': 'Location was not found.'}), 404
        if action == 'reject':
            place.active = False
            place.verified = False
            db.session.commit()
            return jsonify({'success': True, 'status': 'rejected', 'location_id': place.id})
        if action in {'approve', 'promote'}:
            place.name = str(payload.get('canonical_name') or place.name).strip()[:160]
            place.normalized_name = normalize_location_name(place.name)
            place.place_type = str(payload.get('place_type') or place.place_type or 'neighborhood')[:60]
            place.verified = True
            place.active = True
            db.session.commit()
            return jsonify({'success': True, 'status': 'promoted' if action == 'promote' else 'approved', 'location_id': place.id})
        return jsonify({'success': False, 'error': 'Choose approve, reject, or promote.'}), 422
    try:
        result = review_location_observation(
            observation_id, action,
            canonical_name=payload.get('canonical_name'),
            place_type=payload.get('place_type', 'neighborhood'),
        )
    except LookupError as error:
        return jsonify({'success': False, 'error': str(error)}), 404
    except (TypeError, ValueError, RuntimeError) as error:
        return jsonify({'success': False, 'error': str(error)}), 422
    return jsonify({'success': True, **result})


@admin_bp.route('/locations/<int:location_id>/aliases', methods=['POST'])
@login_required
@admin_required('locations')
def add_location_alias_endpoint(location_id):
    payload = request.get_json(silent=True) or {}
    try:
        add_location_alias(location_id, payload.get('alias', ''))
    except LookupError as error:
        return jsonify({'success': False, 'error': str(error)}), 404
    except (ValueError, RuntimeError) as error:
        return jsonify({'success': False, 'error': str(error)}), 422
    return jsonify({'success': True})


@admin_bp.route('/locations/merge', methods=['POST'])
@login_required
@admin_required('locations')
def merge_locations_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        source_id = int(payload.get('source_id'))
        target_id = int(payload.get('target_id'))
        if db.engine.dialect.name == 'postgresql':
            target_id = merge_canonical_locations(source_id, target_id)
        else:
            target_id = merge_local_places(source_id, target_id)
    except (TypeError, ValueError, LookupError, RuntimeError) as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 422
    return jsonify({'success': True, 'target_id': target_id})


@admin_bp.route('/dashboard')
@login_required
@admin_required('dashboard')
def dashboard():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    start_of_day = datetime.combine(today, time.min)
    start_of_tomorrow = datetime.combine(tomorrow, time.min)
    active_statuses = ('In Progress', 'Loading', 'In Transit', 'Unloading')
    bookings = Booking.query.options(
        joinedload(Booking.customer),
        selectinload(Booking.assignments).joinedload(BookingAssignment.truck),
    ).order_by(Booking.created_at.desc()).limit(8).all()
    schedule_options = (
        joinedload(Booking.customer),
        selectinload(Booking.assignments).joinedload(BookingAssignment.truck),
    )
    todays_schedule = Booking.query.options(*schedule_options).filter(
        Booking.move_date == today,
        Booking.status != 'Cancelled',
    ).order_by(Booking.preferred_time, Booking.created_at).all()
    upcoming_jobs = Booking.query.options(*schedule_options).filter(
        Booking.move_date > today,
        Booking.status.notin_(('Cancelled', 'Completed')),
    ).order_by(Booking.move_date, Booking.preferred_time).limit(8).all()
    attention_candidates = Booking.query.options(selectinload(Booking.assignments)).filter(
        Booking.status.notin_(('Draft', 'Cancelled', 'Completed')),
    ).order_by(Booking.updated_at.desc()).limit(100).all()
    human_requests = SupportRequest.query.filter(
        SupportRequest.status.in_(['WAITING_FOR_AGENT', 'HUMAN_ACTIVE'])
    ).order_by(SupportRequest.created_at.desc()).limit(8).all()
    attention_items = []
    for booking in attention_candidates:
        reasons = []
        if booking.route_status in {'PENDING', 'FAILED'}:
            reasons.append('Location or route needs review')
        if booking.quote_status == 'Draft':
            reasons.append('Quote is still in draft')
        if booking.payment_status in {'Pending', 'Failed'}:
            reasons.append(f'Payment {booking.payment_status.lower()}')
        if booking.status in {'Confirmed', 'Assigned'} and not booking.assignments:
            reasons.append('Move team not assigned')
        if booking.move_date and booking.move_date < today:
            reasons.append('Scheduled date has passed')
        if reasons:
            attention_items.append({'booking': booking, 'reasons': reasons})
        if len(attention_items) == 8:
            break
    verified_revenue_today = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status.in_(('Successful', 'Paid')),
        Payment.created_at >= start_of_day,
        Payment.created_at < start_of_tomorrow,
    ).scalar()
    payments_by_booking = db.session.query(
        Payment.booking_id.label('booking_id'), func.sum(Payment.amount).label('paid'),
    ).filter(Payment.status.in_(('Successful', 'Paid'))).group_by(Payment.booking_id).subquery()
    outstanding_amount = db.session.query(func.coalesce(func.sum(
        case(
            (Booking.estimated_total > func.coalesce(payments_by_booking.c.paid, 0),
             Booking.estimated_total - func.coalesce(payments_by_booking.c.paid, 0)),
            else_=0,
        )
    ), 0)).outerjoin(payments_by_booking, payments_by_booking.c.booking_id == Booking.id).filter(
        Booking.status.notin_(('Cancelled', 'Completed')),
    ).scalar()
    status_counts = db.session.query(Booking.status, func.count(Booking.id)).group_by(Booking.status).order_by(Booking.status).all()
    vehicle_status_counts = db.session.query(Truck.status, func.count(Truck.id)).group_by(Truck.status).order_by(Truck.status).all()
    mover_status_counts = db.session.query(Mover.status, func.count(Mover.id)).group_by(Mover.status).order_by(Mover.status).all()
    metrics = {
        'today_bookings': Booking.query.filter(Booking.created_at >= start_of_day, Booking.created_at < start_of_tomorrow).count(),
        'upcoming_moves': Booking.query.filter(Booking.move_date >= today, Booking.status.notin_(('Cancelled', 'Completed'))).count(),
        'active_jobs': Booking.query.filter(Booking.status.in_(active_statuses)).count(),
        'pending_quotes': Booking.query.filter(Booking.quote_status == 'Draft', Booking.status != 'Draft').count(),
        'pending_payments': Booking.query.filter(Booking.payment_status == 'Pending').count(),
        'revenue_today': float(verified_revenue_today or 0),
        'outstanding_payments': float(outstanding_amount or 0),
        'available_movers': Mover.query.filter_by(status='Available').count(),
        'available_vehicles': Truck.query.filter_by(status='Available').count(),
    }
    return render_template(
        'admin/dashboard.html', bookings=bookings, metrics=metrics,
        todays_schedule=todays_schedule, upcoming_jobs=upcoming_jobs,
        attention_items=attention_items, status_counts=status_counts,
        vehicle_status_counts=vehicle_status_counts, mover_status_counts=mover_status_counts,
        human_requests=human_requests,
    )


@admin_bp.route('/bookings')
@login_required
@admin_required('bookings')
def bookings():
    query = Booking.query.outerjoin(Booking.customer).options(joinedload(Booking.customer))
    search = request.args.get('q', '').strip()
    if search:
        pattern = f'%{search}%'
        query = query.filter(or_(
            Booking.booking_number.ilike(pattern), Booking.pickup_address.ilike(pattern),
            Booking.destination_address.ilike(pattern), Booking.pickup_city.ilike(pattern),
            Booking.destination_city.ilike(pattern), User.first_name.ilike(pattern),
            User.last_name.ilike(pattern), User.email.ilike(pattern), User.phone.ilike(pattern),
        ))
    status = request.args.get('status', '')
    quote_status = request.args.get('quote_status', '')
    payment_status = request.args.get('payment_status', '')
    if status:
        query = query.filter(Booking.status == status)
    if quote_status:
        query = query.filter(Booking.quote_status == quote_status)
    if payment_status:
        query = query.filter(Booking.payment_status == payment_status)
    if request.args.get('created') == 'today':
        today = date.today()
        query = query.filter(Booking.created_at >= datetime.combine(today, time.min),
                             Booking.created_at < datetime.combine(today + timedelta(days=1), time.min))
    if request.args.get('move') == 'upcoming':
        query = query.filter(Booking.move_date >= date.today(), Booking.status.notin_(('Cancelled', 'Completed')))
    if request.args.get('paid_on') == 'today':
        today = date.today()
        query = query.join(Payment, Payment.booking_id == Booking.id).filter(
            Payment.status.in_(('Successful', 'Paid')),
            Payment.created_at >= datetime.combine(today, time.min),
            Payment.created_at < datetime.combine(today + timedelta(days=1), time.min),
        )
        query = query.distinct()
    for param, column, lower in (
        ('from', Booking.move_date, True), ('to', Booking.move_date, False),
    ):
        raw_date = request.args.get(param, '')
        if raw_date:
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError:
                flash(f'Invalid {param} date filter.', 'error')
            else:
                query = query.filter(column >= parsed_date if lower else column <= parsed_date)
    sort_columns = {
        'created': Booking.created_at,
        'move_date': Booking.move_date,
        'status': Booking.status,
        'quote': Booking.estimated_total,
        'booking': Booking.booking_number,
    }
    sort = request.args.get('sort', 'created')
    sort_column = sort_columns.get(sort, Booking.created_at)
    order = sort_column.asc() if request.args.get('direction') == 'asc' else sort_column.desc()
    page = max(request.args.get('page', 1, type=int), 1)
    pagination = query.order_by(order, Booking.id.desc()).paginate(page=page, per_page=50, error_out=False)
    previous_url = next_url = None
    if pagination.has_prev:
        previous_args = request.args.to_dict()
        previous_args['page'] = pagination.prev_num
        previous_url = url_for('admin.bookings', **previous_args)
    if pagination.has_next:
        next_args = request.args.to_dict()
        next_args['page'] = pagination.next_num
        next_url = url_for('admin.bookings', **next_args)
    return render_template('admin/bookings.html', bookings=pagination.items,
                           pagination=pagination, filters=request.args,
                           previous_url=previous_url, next_url=next_url)


@admin_bp.route('/bookings/<int:booking_id>')
@login_required
@admin_required('bookings')
def booking_detail(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    return render_template(
        'admin/booking_detail.html', booking=booking,
        trucks=Truck.query.order_by(Truck.registration_number).all(),
        vehicle_types=VehicleType.query.filter_by(is_active=True).order_by(VehicleType.sort_order).all(),
        allowed_ride_statuses=sorted(RIDE_STATUS_TRANSITIONS.get(booking.status, set())),
    )


@admin_bp.route('/trucks/<int:truck_id>/images', methods=['POST'])
@login_required
@admin_required('fleet')
def upload_truck_image(truck_id):
    truck = Truck.query.get_or_404(truck_id)
    files = request.files.getlist('images')
    if not files or not any(file.filename for file in files):
        flash('Choose at least one vehicle image.', 'error')
        return redirect(url_for('admin.trucks'))
    count = VehicleImage.query.filter_by(truck_id=truck.id).count()
    if count + len(files) > current_app.config['MAX_VEHICLE_IMAGES']:
        flash('This vehicle has reached its image limit.', 'error')
        return redirect(url_for('admin.trucks'))
    try:
        for index, file_storage in enumerate(files):
            if file_storage.filename:
                saved = save_image(file_storage, f'vehicles/{truck.id}', current_app.config['MAX_IMAGE_SIZE'])
                db.session.add(VehicleImage(truck_id=truck.id, file_name=saved['file_url'], original_filename=saved['original_filename'], mime_type=saved['mime_type'], file_size=saved['file_size'], sort_order=count + index))
    except (ValueError, OSError) as error:
        db.session.rollback()
        flash(str(error), 'error')
        return redirect(url_for('admin.trucks'))
    db.session.commit()
    flash('Vehicle images uploaded.', 'success')
    return redirect(url_for('admin.trucks'))


@admin_bp.route('/trucks/<int:truck_id>/images/<int:image_id>', methods=['POST'])
@login_required
@admin_required('fleet')
def reorder_truck_image(truck_id, image_id):
    truck = Truck.query.get_or_404(truck_id)
    image = VehicleImage.query.filter_by(id=image_id, truck_id=truck.id).first_or_404()
    action = request.form.get('action', '').strip()
    if action == 'delete':
        delete_image(image.file_name)
        db.session.delete(image)
        db.session.commit()
        flash('Vehicle image removed.', 'success')
    elif action == 'primary':
        VehicleImage.query.filter_by(truck_id=truck.id).update({'is_primary': False})
        image.is_primary = True
        db.session.commit()
        flash('Primary image updated.', 'success')
    else:
        direction = request.form.get('direction', 'up')
        images = VehicleImage.query.filter_by(truck_id=truck.id).order_by(VehicleImage.sort_order, VehicleImage.id).all()
        current_index = next((index for index, candidate in enumerate(images) if candidate.id == image.id), 0)
        target_index = max(0, min(len(images) - 1, current_index - 1 if direction == 'up' else current_index + 1))
        if target_index != current_index:
            images[current_index].sort_order, images[target_index].sort_order = images[target_index].sort_order, images[current_index].sort_order
            db.session.commit()
            flash('Image order updated.', 'success')
    return redirect(url_for('admin.trucks'))


@admin_bp.route('/trucks/<int:truck_id>/images/<path:filename>')
@login_required
@admin_required('fleet')
def serve_truck_image(truck_id, filename):
    truck = Truck.query.get_or_404(truck_id)
    image = VehicleImage.query.filter_by(truck_id=truck.id, file_name=f'vehicles/{truck.id}/{filename}').first_or_404()
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], image.file_name)


@admin_bp.route('/bookings/<int:booking_id>/attachments/<path:filename>')
@login_required
@admin_required('bookings')
def serve_booking_attachment(booking_id, filename):
    booking = Booking.query.get_or_404(booking_id)
    photo = BookingPhoto.query.filter_by(booking_id=booking.id, file_name=f'booking/{filename}').first_or_404()
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], photo.file_name)
@login_required
@admin_required('quotes')
def override_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    before = {
        'total': booking.manually_overridden_total if booking.manually_overridden_total is not None else booking.estimated_total,
        'recommended_vehicle_type_id': booking.recommended_vehicle_type_id,
    }
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('A reason is required for every manual override.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    try:
        amount = request.form.get('amount', '').strip()
        if amount:
            apply_price_override(booking, float(amount), reason, current_user.id)
        vehicle_id = request.form.get('vehicle_type_id', '').strip()
        if vehicle_id:
            vehicle = VehicleType.query.filter_by(id=int(vehicle_id), is_active=True).first()
            if vehicle is None:
                raise ValueError('Choose an active vehicle type.')
            previous = booking.recommended_vehicle_type.name if booking.recommended_vehicle_type else None
            booking.recommended_vehicle_type_id = vehicle.id
            booking.vehicle_override_reason = reason[:1000]
            booking.overridden_by = current_user.id
            booking.overridden_at = datetime.utcnow()
            record_event(booking, 'vehicle.recommendation_overridden', previous=previous, new=vehicle.name, user_id=current_user.id)
            if booking.quote_status in {'Ready', 'Accepted'}:
                booking.workflow_state = 'SCHEDULE_COMPLETED'
                booking.quote_status = 'Draft'
                calculate_quote(booking, current_user.id)
        if not amount and not vehicle_id:
            raise ValueError('Enter an override price or choose a vehicle.')
        write_admin_audit('booking.override_updated', 'booking', booking.id, before=before, after={
            'total': booking.manually_overridden_total if booking.manually_overridden_total is not None else booking.estimated_total,
            'recommended_vehicle_type_id': booking.recommended_vehicle_type_id,
            'reason': reason,
        })
        db.session.commit()
        flash('Booking override saved and recorded.', 'success')
    except (ValueError, TypeError) as error:
        db.session.rollback()
        flash(str(error), 'error')
    return redirect(url_for('admin.booking_detail', booking_id=booking.id))


@admin_bp.route('/bookings/<int:booking_id>/assign', methods=['POST'])
@login_required
@admin_required('schedule')
def assign_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    previous_assignment = BookingAssignment.query.filter_by(booking_id=booking.id).first()
    before = {
        'truck_id': previous_assignment.truck_id if previous_assignment else None,
        'driver_name': previous_assignment.driver_name if previous_assignment else None,
        'movers': [getattr(previous_assignment, f'mover_{index}') for index in range(1, 5) if previous_assignment and getattr(previous_assignment, f'mover_{index}')],
    }
    truck_value = request.form.get('truck_id', '').strip()
    if truck_value and not truck_value.isdigit():
        flash('Select an available truck.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    truck = Truck.query.get(int(truck_value)) if truck_value.isdigit() else None
    if truck_value and (truck is None or truck.status != 'Available'):
        flash('Select an available truck.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))

    movers = [name.strip() for name in request.form.get('movers', '').split(',') if name.strip()]
    if len(movers) > 4:
        flash('A move can have up to four movers.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    try:
        save_assignment(
            booking,
            truck_id=truck.id if truck else None,
            driver_name=request.form.get('driver_name', '').strip(),
            mover_names=movers,
            cleaner=request.form.get('cleaner', '').strip(),
        )
        write_admin_audit('booking.assignment_updated', 'booking', booking.id, before=before, after={
            'truck_id': truck.id if truck else None,
            'driver_name': request.form.get('driver_name', '').strip(),
            'movers': movers,
        })
        record_event(booking, 'operations.team_assigned', new={
            'truck_id': truck.id if truck else None, 'driver': request.form.get('driver_name', '').strip(),
            'movers': movers,
        }, user_id=current_user.id)
        db.session.commit()
        flash('Move team assigned.', 'success')
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'error')
    return redirect(url_for('admin.booking_detail', booking_id=booking.id))


@admin_bp.route('/incidents/<int:incident_id>', methods=['POST'])
@login_required
@admin_required('support')
def update_incident(incident_id):
    incident = IncidentReport.query.get_or_404(incident_id)
    before = {'status': incident.status, 'resolution': incident.resolution}
    status = request.form.get('status', '').strip()
    resolution = request.form.get('resolution', '').strip()
    if status not in {'Open', 'Investigating', 'Resolved', 'Closed'}:
        flash('Choose a valid incident status.', 'error')
    else:
        incident.status = status
        incident.resolution = resolution[:5000] or None
        write_admin_audit('incident.updated', 'incident', incident.id, before=before,
                  after={'status': incident.status, 'resolution': incident.resolution})
        db.session.commit()
        flash('Incident report updated.', 'success')
    return redirect(url_for('admin.booking_detail', booking_id=incident.booking_id))


@admin_bp.route('/bookings/<int:booking_id>/status', methods=['POST'])
@login_required
@admin_required('jobs')
def update_booking_status(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    target = request.form.get('status', '').strip()
    if target not in RIDE_STATUS_TRANSITIONS.get(booking.status, set()):
        flash('That booking status transition is not allowed.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    requires_assignment = target in {'In Progress', 'En Route', 'Arrived', 'Loading', 'In Transit', 'Unloading', 'Completed'}
    if requires_assignment and not BookingAssignment.query.filter_by(booking_id=booking.id).first():
        flash('Assign a vehicle or move team before advancing the ride status.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    if target == 'Completed' and booking.payment_status not in {'Paid', 'Deposit Paid'}:
        flash('Record successful payment before completing the move.', 'error')
        return redirect(url_for('admin.booking_detail', booking_id=booking.id))
    previous = {'status': booking.status, 'workflow_state': booking.workflow_state}
    booking.status = target
    if target in {'In Progress', 'En Route', 'Arrived', 'Loading', 'In Transit', 'Unloading'}:
        booking.workflow_state = 'IN_PROGRESS'
    elif target == 'Completed':
        booking.workflow_state = 'COMPLETED'
    elif target == 'Cancelled':
        booking.workflow_state = 'CANCELLED'
    record_event(booking, 'ride.status_changed', previous=previous,
                 new={'status': booking.status, 'workflow_state': booking.workflow_state}, user_id=current_user.id)
    write_admin_audit('ride.status_changed', 'booking', booking.id, before=previous,
                      after={'status': booking.status, 'workflow_state': booking.workflow_state})
    db.session.commit()
    flash(f'Booking status changed to {target}.', 'success')
    return redirect(url_for('admin.booking_detail', booking_id=booking.id))


@admin_bp.route('/pricing', methods=['GET', 'POST'])
@login_required
@admin_required('quotes')
def pricing():
    form = PricingForm()
    rows = {item.key: item for item in ServicePricing.query.all()}
    if request.method == 'GET':
        for key in PRICING_KEYS:
            row = rows.get(key)
            getattr(form, key).data = float(row.value if row else 0) if key in {'volume_safety_factor', 'tax_rate', 'deposit_percentage'} else int(row.value if row else 0)
    elif form.validate_on_submit():
        values = {key: getattr(form, key).data for key in PRICING_KEYS}
        if any(value is None or value < 0 for value in values.values()):
            flash('Prices must be zero or greater.', 'error')
        else:
            before = {key: rows[key].value if key in rows else None for key in PRICING_KEYS}
            for key, value in values.items():
                row = rows.get(key)
                if row is None:
                    row = ServicePricing(key=key, label=key.replace('_', ' ').title())
                    db.session.add(row)
                row.value = value
            after = {key: value for key, value in values.items()}
            write_admin_audit('pricing.updated', 'service_pricing', None, before=before, after=after)
            db.session.commit()
            flash('Pricing saved.', 'success')
            return redirect(url_for('admin.pricing'))
    return render_template('admin/pricing.html', form=form)


@admin_bp.route('/inventory', methods=['GET', 'POST'])
@login_required
@admin_required('inventory')
def inventory_catalogue():
    query = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    catalogue_query = InventoryItem.query
    if query:
        pattern = f'%{query.casefold()}%'
        catalogue_query = catalogue_query.filter(or_(
            InventoryItem.name.ilike(pattern), InventoryItem.description.ilike(pattern),
            InventoryItem.aliases.ilike(pattern), InventoryItem.category.ilike(pattern),
        ))
    if category:
        catalogue_query = catalogue_query.filter_by(category=category)
    items = catalogue_query.order_by(InventoryItem.sort_order, InventoryItem.name).all()
    categories = sorted({item.category for item in InventoryItem.query.all()})
    form = InventoryItemForm()
    item_id = request.args.get('item_id', type=int)
    if item_id:
        form_data = InventoryItem.query.filter_by(id=item_id).first_or_404()
        form = InventoryItemForm(
            name=form_data.name,
            category=form_data.category,
            description=form_data.description,
            estimated_volume_m3=form_data.estimated_volume_m3,
            estimated_weight_kg=form_data.estimated_weight_kg,
            dimensions_length_cm=(form_data.dimensions_cm or {}).get('length'),
            dimensions_width_cm=(form_data.dimensions_cm or {}).get('width'),
            dimensions_height_cm=(form_data.dimensions_cm or {}).get('height'),
            aliases=form_data.aliases,
            fragile=form_data.fragile,
            special_handling=form_data.special_handling,
            vehicle_compatibility=form_data.vehicle_compatibility,
            active=form_data.active,
            sort_order=form_data.sort_order,
        )
    if request.method == 'POST' and request.form.get('action') == 'delete':
        item_id = request.form.get('item_id', type=int)
        item = InventoryItem.query.filter_by(id=item_id).first_or_404()
        if item.selections:
            flash('Archive the item after removing it from every booking selection.', 'error')
        else:
            db.session.delete(item)
            write_admin_audit('inventory_item.deleted', 'inventory_item', item.id, after={'name': item.name})
            db.session.commit()
            flash('Catalogue item deleted.', 'success')
        return redirect(url_for('admin.inventory_catalogue'))
    if request.method == 'POST' and request.form.get('action') == 'save':
        item_id = request.form.get('item_id', type=int)
        item = InventoryItem.query.filter_by(id=item_id).first_or_404() if item_id else InventoryItem()
        is_new_item = item.id is None
        if form.validate_on_submit():
            name = form.name.data.strip()
            duplicate = InventoryItem.query.filter(InventoryItem.name == name, InventoryItem.id != item.id).first()
            if duplicate:
                form.name.errors.append('An item with this name already exists.')
            else:
                dimensions = {}
                for field in ('dimensions_length_cm', 'dimensions_width_cm', 'dimensions_height_cm'):
                    value = getattr(form, field).data
                    if value is not None:
                        dimensions[field.removeprefix('dimensions_').removesuffix('_cm')] = value
                item.name = name
                item.category = form.category.data
                item.description = (form.description.data or '').strip() or None
                item.estimated_volume_m3 = form.estimated_volume_m3.data or 0
                item.estimated_weight_kg = form.estimated_weight_kg.data or 0
                item.dimensions_cm = dimensions or None
                item.aliases = (form.aliases.data or '').strip() or None
                item.fragile = form.fragile.data
                item.special_handling = form.special_handling.data
                item.vehicle_compatibility = (form.vehicle_compatibility.data or '').strip() or None
                item.active = form.active.data
                item.sort_order = form.sort_order.data or 0
                db.session.add(item)
                if is_new_item:
                    db.session.flush()
                audit_action = 'inventory_item.created' if is_new_item else 'inventory_item.updated'
                write_admin_audit(audit_action, 'inventory_item', item.id, after={'name': name, 'active': item.active})
                db.session.commit()
                flash('Catalogue item saved.', 'success')
                return redirect(url_for('admin.inventory_catalogue'))
    return render_template('admin/inventory.html', items=items, categories=categories,
                           form=form, query=query, category=category)


@admin_bp.route('/trucks', methods=['GET', 'POST'])
@login_required
@admin_required('fleet')
def trucks():
    form = TruckPartnerForm()
    if form.validate_on_submit():
        partner = TruckPartner(
            name=form.name.data,
            company=form.company.data or None,
            phone=form.phone.data or None,
            email=form.email.data or None,
            vehicle_type=form.vehicle_type.data,
            capacity=form.capacity.data or None,
            rate=form.rate.data,
            operating_areas=form.operating_areas.data or None,
            status='Available',
        )
        db.session.add(partner)
        db.session.flush()
        write_admin_audit('truck_partner.created', 'truck_partner', partner.id, after={
            'name': partner.name, 'company': partner.company, 'vehicle_type': partner.vehicle_type,
            'rate': partner.rate,
        })
        db.session.commit()
        flash('Truck partner added.', 'success')
        return redirect(url_for('admin.trucks'))

    status = request.args.get('status', '')
    vehicle_type = request.args.get('vehicle_type', '')
    area = request.args.get('area', '')
    resource_query = Truck.query
    partner_query = TruckPartner.query
    if status:
        resource_query = resource_query.filter_by(status=status)
        partner_query = partner_query.filter_by(status=status)
    if vehicle_type:
        resource_query = resource_query.filter(Truck.vehicle_type.ilike(f'%{vehicle_type}%'))
    if area:
        resource_query = resource_query.filter(Truck.current_location.ilike(f'%{area}%'))
    resources = resource_query.order_by(Truck.registration_number).all()
    partners = partner_query.order_by(TruckPartner.name).all()
    return render_template(
        'admin/resources.html', page_title='Truck partners',
        description='Partner companies and fleet vehicles available for move assignments.',
        trucks=resources, partners=partners, partner_form=form,
        status_filter=status, vehicle_type_filter=vehicle_type, area_filter=area,
    )


@admin_bp.route('/movers', methods=['GET', 'POST'])
@login_required
@admin_required('movers')
def movers():
    form = MoverForm()
    if form.validate_on_submit():
        mover = Mover(
            name=form.name.data,
            phone=form.phone.data or None,
            skills=form.skills.data or None,
            experience=form.experience.data or None,
            status='Available',
        )
        db.session.add(mover)
        db.session.flush()
        write_admin_audit('mover.created', 'mover', mover.id, after={
            'name': mover.name, 'phone': mover.phone, 'skills': mover.skills,
        })
        db.session.commit()
        flash('Mover added.', 'success')
        return redirect(url_for('admin.movers'))

    query = Mover.query
    status = request.args.get('status', '')
    team_id = request.args.get('team', '')
    if status:
        query = query.filter_by(status=status)
    if team_id:
        query = query.filter_by(team_id=int(team_id))
    movers = query.order_by(Mover.name).all()
    teams = Team.query.order_by(Team.name).all()
    return render_template(
        'admin/resources.html', page_title='Movers',
        description='Mover availability and experience for booking assignments.',
        movers=movers, mover_form=form, status_filter=status, teams=teams,
        team_filter=team_id,
    )


@admin_bp.route('/cleaners')
@login_required
@admin_required('fleet')
def cleaners():
    cleaners = Cleaner.query.order_by(Cleaner.name).all()
    rows = [[cleaner.name, cleaner.contact or 'Not set', cleaner.location or 'Not set', cleaner.service_areas or 'Not set', cleaner.status, f'NGN {cleaner.rate:,.0f}'] for cleaner in cleaners]
    return render_template('admin/resources.html', page_title='Cleaners', description='Cleaning partners and service coverage.', columns=['Name', 'Contact', 'Location', 'Service areas', 'Status', 'Rate'], rows=rows)
