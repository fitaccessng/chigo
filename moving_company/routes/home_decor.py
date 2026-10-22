import secrets
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict

from ..extensions import db
from ..models import Booking, BookingEvent, HomeDecorAttachment, HomeDecorRequest, Notification
from ..services.booking_engine_service import create_booking_request
from ..services.home_decor_service import (
    ATTACHMENT_CATEGORIES, CONTACT_METHODS, DECOR_SERVICES, DESIGN_STYLES,
    INSPECTION_OPTIONS, PROJECT_SCOPES, PROPERTY_STATUSES, PROPERTY_TYPES,
    ROOM_OPTIONS, clean_decor_answers, configured_budget_ranges,
)
from ..services.image_upload_service import delete_image, save_home_decor_attachment
from ..services.location_service import geocode_address, get_local_place, validate_coordinates, validate_service_area

home_decor_bp = Blueprint('home_decor', __name__)
STEPS = {
    1: ('Client information', 'Your contact details'),
    2: ('Property information', 'The space you want to transform'),
    3: ('Décor services', 'Choose the services and rooms'),
    4: ('Design preferences', 'Tell us about your style and inspiration'),
    5: ('Budget and scope', 'Set expectations for the project'),
    6: ('Timeline and review', 'Check everything before submitting'),
}
DRAFT_KEYS = {
    'full_name', 'phone', 'email', 'contact_method', 'property_type', 'property_status',
    'location', 'location_search', 'location_address', 'location_city', 'location_state',
    'location_country', 'location_area', 'location_area_council', 'location_district',
    'location_neighborhood', 'location_formatted_address', 'location_latitude',
    'location_longitude', 'location_place_id', 'location_provider', 'location_provider_raw_id',
    'location_type', 'bedroom_count', 'property_size', 'inspection_access', 'other_service',
    'other_style', 'preferred_colours', 'desired_outcome', 'retain_items', 'items_to_source',
    'room_measurements', 'reference_link', 'budget_range', 'project_scope', 'source_materials',
    'material_requirements', 'preferred_start_date', 'desired_completion_date',
    'timeline_flexible', 'preferred_consultation_date', 'instructions', 'contact_consent',
}
LIST_KEYS = {'decor_services', 'rooms', 'reference_links'}
STEP_FIELDS = {
    1: {'full_name', 'phone', 'email', 'contact_method'},
    2: {'property_type', 'property_status', 'location', 'location_search', 'location_address', 'location_city',
        'location_state', 'location_country', 'location_area', 'location_area_council', 'location_district',
        'location_neighborhood', 'location_formatted_address', 'location_latitude', 'location_longitude',
        'location_place_id', 'location_provider', 'location_provider_raw_id', 'location_type', 'bedroom_count',
        'property_size', 'inspection_access'},
    3: {'decor_services', 'rooms', 'other_service'},
    4: {'design_style', 'other_style', 'preferred_colours', 'desired_outcome', 'retain_items', 'items_to_source',
        'room_measurements', 'reference_links'},
    5: {'budget_range', 'project_scope', 'source_materials', 'material_requirements'},
    6: {'preferred_start_date', 'desired_completion_date', 'timeline_flexible',
        'preferred_consultation_date', 'instructions', 'contact_consent'},
}


def _draft_values(form, previous, step):
    values = dict(previous or {})
    allowed = DRAFT_KEYS | {'design_style', 'decor_services', 'rooms', 'reference_links'}
    for key in allowed:
        if key in LIST_KEYS:
            if key in form:
                values[key] = form.getlist(key)
        elif key in form:
            values[key] = str(form.get(key) or '').strip()
    return values


def _validate_step(step, data):
    if step == 1:
        if len(data.get('full_name', '')) < 2:
            raise ValueError('Enter your full name.')
        phone = data.get('phone', '')
        if len(phone) < 7 or len(phone) > 30 or not any(char.isdigit() for char in phone):
            raise ValueError('Enter a valid phone number.')
        if data.get('contact_method') not in CONTACT_METHODS:
            raise ValueError('Choose a preferred contact method.')
        email = data.get('email', '')
        if email and ('@' not in email or len(email) > 254):
            raise ValueError('Enter a valid email address or leave it blank.')
        if data['contact_method'] == 'Email' and not email:
            raise ValueError('Add an email address to use email as your preferred contact method.')
    elif step == 2:
        if data.get('property_type') not in PROPERTY_TYPES:
            raise ValueError('Choose a property type.')
        if data.get('property_status') not in PROPERTY_STATUSES:
            raise ValueError('Choose the current property status.')
        if data.get('inspection_access') not in INSPECTION_OPTIONS:
            raise ValueError('Choose an inspection access option.')
        if not data.get('location_search') and not data.get('location'):
            raise ValueError('Search for and select the property location.')
        if data.get('property_type') != 'Office or commercial space':
            try:
                bedrooms = int(data.get('bedroom_count', ''))
            except (TypeError, ValueError):
                raise ValueError('Enter the number of bedrooms.') from None
            if bedrooms < 0 or bedrooms > 100:
                raise ValueError('Bedrooms must be between 0 and 100.')
    elif step == 3:
        services = data.get('decor_services', [])
        rooms = data.get('rooms', [])
        if not services or any(value not in DECOR_SERVICES for value in services):
            raise ValueError('Select at least one valid décor service.')
        if not rooms or any(value not in ROOM_OPTIONS for value in rooms):
            raise ValueError('Select at least one valid room or area.')
        if 'Other' in services and not data.get('other_service'):
            raise ValueError('Describe the additional décor service you need.')
    elif step == 4:
        if data.get('design_style') not in DESIGN_STYLES:
            raise ValueError('Choose a design style.')
        if data.get('design_style') == 'Other' and not data.get('other_style'):
            raise ValueError('Describe your preferred design style.')
        if not data.get('desired_outcome'):
            raise ValueError('Describe the outcome you would like Chigo to help you achieve.')
        _links(data.get('reference_links', []))
    elif step == 5:
        budgets = {item['value'] for item in configured_budget_ranges()}
        if data.get('budget_range') not in budgets:
            raise ValueError('Choose an estimated budget range.')
        if data.get('project_scope') not in PROJECT_SCOPES:
            raise ValueError('Choose an intended project scope.')
        if data.get('source_materials') not in {'yes', 'no'}:
            raise ValueError('Choose whether you want Chigo to source furniture or materials.')
    elif step == 6:
        if data.get('timeline_flexible') not in {'yes', 'no'}:
            raise ValueError('Choose whether your timeline is flexible.')
        data_multi = MultiDict()
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    data_multi.add(key, item)
            else:
                data_multi.add(key, value)
        clean_decor_answers(data_multi, current_user)


def _links(values):
    values = [str(value).strip() for value in values if str(value).strip()]
    if len(values) > 10 or any(len(value) > 500 or not value.startswith(('https://', 'http://')) for value in values):
        raise ValueError('Reference links must be valid HTTP or HTTPS URLs, with no more than 10 links.')
    return values


def _resolve_location(data):
    address = str(data.get('location_address') or data.get('location_search') or data.get('location') or '').strip()
    if len(address) < 2 or len(address) > 500:
        raise ValueError('Select a valid property location in the supported service area.')
    result = None
    place_id = str(data.get('location_place_id') or '')
    if data.get('location_provider') == 'local-reference' and place_id.startswith(('local-', 'postgis-', 'observation-')):
        result = get_local_place(place_id)
    if result is None:
        result = geocode_address(address)
    if not result:
        raise ValueError('We could not verify that location. Choose an autocomplete result inside Chigo’s service area.')
    coordinates = validate_coordinates(result.get('latitude'), result.get('longitude'))
    if not coordinates or validate_service_area(*coordinates) != 'SERVICEABLE':
        raise ValueError('Home Décor requests are currently accepted only within Chigo’s verified FCT service area.')
    return {
        'pickup_address': address[:255],
        'pickup_city': str(result.get('city') or data.get('location_city') or '').strip()[:100] or None,
        'pickup_state': str(result.get('state') or data.get('location_state') or '').strip()[:100] or None,
        'pickup_country': str(result.get('country') or data.get('location_country') or '').strip()[:100] or None,
        'pickup_area': result.get('area') or data.get('location_area'),
        'pickup_area_council': result.get('area_council') or data.get('location_area_council'),
        'pickup_district': result.get('district') or data.get('location_district'),
        'pickup_neighborhood': result.get('neighborhood') or data.get('location_neighborhood'),
        'pickup_location_type': result.get('location_type') or data.get('location_type'),
        'pickup_original_input': data.get('location_search') or address,
        'pickup_normalized_location': result.get('normalized_location') or result.get('formatted_address') or address,
        'pickup_formatted_address': result.get('formatted_address') or address,
        'pickup_latitude': coordinates[0], 'pickup_longitude': coordinates[1],
        'pickup_place_id': result.get('place_id') or result.get('provider_place_id') or place_id,
        'pickup_provider': result.get('provider') or data.get('location_provider') or 'manual',
        'pickup_provider_raw_id': result.get('provider_raw_id') or data.get('location_provider_raw_id'),
        'pickup_route_resolution': 'exact' if result.get('provider') == 'local-reference' else 'geocoded',
        'route_status': 'NOT_REQUIRED', 'distance_precision': 'not_applicable',
        'service_area_status': 'SERVICEABLE', 'distance_km': 0.0,
    }


def _create_draft():
    active_drafts = HomeDecorRequest.query.filter_by(customer_id=current_user.id, status='Draft').count()
    if active_drafts >= current_app.config['MAX_DECOR_DRAFTS_PER_CUSTOMER']:
        abort(429, description='You have several unfinished décor requests. Resume or submit one before starting another.')
    reference = f"HD-{datetime.utcnow():%Y}-{secrets.token_hex(4).upper()}"
    draft = HomeDecorRequest(
        customer_id=current_user.id, public_reference=reference,
        idempotency_key=secrets.token_urlsafe(24), status='Draft', draft_step=1,
        draft_data={
            'full_name': current_user.full_name,
            'phone': current_user.phone or '', 'email': current_user.email,
            'contact_method': 'WhatsApp',
        },
    )
    db.session.add(draft)
    db.session.commit()
    return draft


@home_decor_bp.route('/home-decor', methods=['GET'])
@login_required
def start():
    if not current_user.can_use_service('home_decor'):
        flash('This service is not available for your account. Contact support for help.', 'error')
        return redirect(url_for('customer.dashboard'))
    return redirect(url_for('home_decor.step', request_id=_create_draft().id, step=1))


@home_decor_bp.route('/home-decor/<int:request_id>/step/<int:step>', methods=['GET', 'POST'])
@login_required
def step(request_id, step):
    if step not in STEPS:
        abort(404)
    decor_request = HomeDecorRequest.query.filter_by(id=request_id, customer_id=current_user.id).with_for_update().first_or_404()
    if decor_request.submitted_at or decor_request.status != 'Draft':
        return redirect(url_for('home_decor.confirmation', request_id=decor_request.id))
    if step > decor_request.draft_step + 1:
        return redirect(url_for('home_decor.step', request_id=decor_request.id, step=decor_request.draft_step))
    if request.method == 'POST':
        action = request.form.get('action', 'continue')
        data = _draft_values(request.form, decor_request.draft_data, step)
        try:
            if action in {'continue', 'submit'}:
                _validate_step(step, data)
            if action == 'upload':
                _save_attachments(decor_request)
                decor_request.draft_data = data
                db.session.commit()
                flash('Files added to your décor request.', 'success')
                return redirect(url_for('home_decor.step', request_id=request_id, step=step))
            decor_request.draft_data = data
            if action == 'back':
                decor_request.draft_step = max(1, step - 1)
                db.session.commit()
                return redirect(url_for('home_decor.step', request_id=request_id, step=decor_request.draft_step))
            if action == 'continue':
                decor_request.draft_step = min(6, step + 1)
                db.session.commit()
                return redirect(url_for('home_decor.step', request_id=request_id, step=decor_request.draft_step))
            if action == 'submit' and step == 6:
                _submit_request(decor_request, data)
                return redirect(url_for('home_decor.confirmation', request_id=decor_request.id))
            raise ValueError('Choose a valid step action.')
        except (ValueError, RuntimeError, OSError) as error:
            db.session.rollback()
            flash(str(error), 'error')
            decor_request = HomeDecorRequest.query.filter_by(id=request_id, customer_id=current_user.id).first_or_404()
            decor_request.draft_data = data
            db.session.commit()
    return render_template(
        'move_request/home_decor.html', decor_request=decor_request,
        step=step, steps=STEPS, values=decor_request.draft_data or {},
        now_date=date.today().isoformat(),
        budget_ranges=configured_budget_ranges(), property_types=PROPERTY_TYPES,
        property_statuses=PROPERTY_STATUSES, inspection_options=INSPECTION_OPTIONS,
        decor_services=DECOR_SERVICES, rooms=ROOM_OPTIONS, styles=DESIGN_STYLES,
        project_scopes=PROJECT_SCOPES, contact_methods=CONTACT_METHODS,
    )


def _save_attachments(decor_request):
    file_groups = (
        ('current_space', 'current_space_files'),
        ('inspiration', 'inspiration_files'),
        ('floor_plan', 'floor_plan_files'),
    )
    files = [(category, file) for category, field in file_groups
             for file in request.files.getlist(field) if file and file.filename]
    if not files:
        return
    if len(decor_request.attachments) + len(files) > current_app.config['MAX_DECOR_ATTACHMENTS']:
        raise ValueError(f'You can attach no more than {current_app.config["MAX_DECOR_ATTACHMENTS"]} files.')
    upload_bytes = 0
    for _, file in files:
        file.stream.seek(0, 2)
        upload_bytes += file.stream.tell()
        file.stream.seek(0)
    if upload_bytes > current_app.config['MAX_DECOR_TOTAL_UPLOAD_SIZE']:
        raise ValueError(f'The total selected files must be no larger than {current_app.config["MAX_DECOR_TOTAL_UPLOAD_SIZE"] // (1024 * 1024)} MB per upload.')
    written = []
    try:
        for category, file in files:
            is_pdf = (file.mimetype or '').casefold() in {'application/pdf', 'application/x-pdf'}
            if is_pdf and category != 'floor_plan':
                raise ValueError('PDF documents can only be uploaded as floor plans.')
            saved = save_home_decor_attachment(
                file, f'home-decor/{decor_request.id}', current_app.config['MAX_DECOR_ATTACHMENT_SIZE'],
            )
            written.append(saved['file_url'])
            db.session.add(HomeDecorAttachment(
                request_id=decor_request.id, uploaded_by_id=current_user.id,
                storage_key=saved['file_url'], original_filename=saved['original_filename'],
                mime_type=saved['mime_type'], file_size=saved['file_size'], category=category,
            ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            for key in written:
                delete_image(key)
            raise
    except Exception:
        db.session.rollback()
        for key in written:
            delete_image(key)
        raise


def _submit_request(decor_request, data):
    if decor_request.submitted_at:
        return
    complete = dict(data)
    multi = MultiDict()
    for key, value in complete.items():
        if isinstance(value, list):
            for item in value:
                multi.add(key, item)
        else:
            multi.add(key, value)
    answers = clean_decor_answers(multi, current_user)
    location = _resolve_location(complete)
    answers['property']['location'] = location['pickup_formatted_address'] or location['pickup_address']
    db.session.begin_nested()
    booking = create_booking_request(
        customer_id=current_user.id, user_id=current_user.id,
        location=location, service_type='home_decor',
        initial_service_answers=answers,
        commit=False,
    )
    booking.status = 'Booking Received'
    booking.quote_status = 'Quote Required'
    booking.workflow_data = {
        **(booking.workflow_data or {}),
        'home_decor_request': {'reference': decor_request.public_reference, 'status': 'New'},
        'property': {'service_answers': answers},
        'services': {'home_decor': 'yes'},
    }
    decor_request.booking = booking
    decor_request.status = 'New'
    decor_request.draft_data = complete
    decor_request.draft_step = 6
    decor_request.submitted_at = datetime.utcnow()
    db.session.add(Notification(
        user_id=current_user.id, booking=booking,
        message=f"Home Décor enquiry {decor_request.public_reference} was received for operations review.",
    ))
    db.session.add(BookingEvent(
        booking_id=booking.id, user_id=current_user.id, action='home_decor.submitted',
        previous_value={'status': 'Draft'},
        new_value={'status': 'New', 'public_reference': decor_request.public_reference},
    ))
    db.session.commit()
    session['active_booking_request_id'] = booking.booking_request_id


@home_decor_bp.route('/home-decor/<int:request_id>/confirmation')
@login_required
def confirmation(request_id):
    decor_request = HomeDecorRequest.query.filter_by(id=request_id, customer_id=current_user.id).first_or_404()
    if not decor_request.submitted_at or decor_request.booking is None:
        return redirect(url_for('home_decor.step', request_id=decor_request.id, step=decor_request.draft_step))
    return render_template('move_request/home_decor_confirmation.html', decor_request=decor_request)


@home_decor_bp.route('/home-decor/<int:request_id>/attachments/<int:attachment_id>')
@login_required
def customer_attachment(request_id, attachment_id):
    decor_request = HomeDecorRequest.query.filter_by(id=request_id, customer_id=current_user.id).first_or_404()
    attachment = HomeDecorAttachment.query.filter_by(id=attachment_id, request_id=decor_request.id).first_or_404()
    upload_root = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    candidate = (upload_root / attachment.storage_key).resolve()
    try:
        candidate.relative_to(upload_root)
    except ValueError:
        abort(403)
    if not candidate.exists():
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b'')
    return send_file(
        candidate,
        as_attachment=attachment.mime_type == 'application/pdf',
        download_name=attachment.original_filename,
        mimetype=attachment.mime_type,
    )


@home_decor_bp.route('/home-decor/<int:request_id>/quote-decision', methods=['POST'])
@login_required
def quote_decision(request_id):
    decor_request = HomeDecorRequest.query.filter_by(id=request_id, customer_id=current_user.id).first_or_404()
    if decor_request.status != 'Awaiting Client Approval' or not decor_request.booking or decor_request.booking.quote_status != 'Ready':
        abort(404)
    if request.form.get('decision') != 'decline':
        abort(400)
    previous = decor_request.status
    decor_request.status = 'Rejected'
    db.session.add(BookingEvent(
        booking_id=decor_request.booking_id, user_id=current_user.id,
        action='home_decor.quote_declined', previous_value={'status': previous},
        new_value={'status': decor_request.status},
    ))
    db.session.commit()
    flash('Your décor quotation was declined. No payment was taken.', 'success')
    return redirect(url_for('home_decor.confirmation', request_id=decor_request.id))
