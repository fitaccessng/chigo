import json
from datetime import date

from flask import current_app

DECOR_STATUS_TRANSITIONS = {
    'New': {'Under Review', 'Cancelled'},
    'Under Review': {'Inspection/Consultation', 'Quotation Prepared', 'On Hold', 'Rejected', 'Cancelled'},
    'Inspection/Consultation': {'Quotation Prepared', 'On Hold', 'Rejected', 'Cancelled'},
    'Quotation Prepared': {'Awaiting Client Approval', 'Under Review', 'Cancelled'},
    'Awaiting Client Approval': {'Approved', 'Rejected', 'On Hold', 'Cancelled'},
    'Approved': {'In Progress', 'On Hold', 'Cancelled'},
    'In Progress': {'Completed', 'On Hold', 'Cancelled'},
    'On Hold': {'Under Review', 'Inspection/Consultation', 'Quotation Prepared', 'Awaiting Client Approval', 'Approved', 'In Progress', 'Cancelled'},
    'Completed': set(), 'Rejected': set(), 'Cancelled': set(),
}

PROPERTY_TYPES = ('Apartment', 'Duplex', 'Bungalow', 'Penthouse', 'Office or commercial space', 'Other')
PROPERTY_STATUSES = ('Newly acquired', 'Currently occupied', 'Under renovation', 'Vacant')
INSPECTION_OPTIONS = ('Available', 'Unavailable', 'Needs arrangement')
DECOR_SERVICES = (
    'Full-home interior styling', 'Living room makeover', 'Bedroom styling',
    'Kitchen and dining area styling', 'Bathroom styling', 'Curtains and blinds',
    'Furniture sourcing and arrangement', 'Wall finishes, painting, or wallpaper',
    'Lighting and decorative accessories', 'Office or commercial interior styling', 'Other',
)
ROOM_OPTIONS = ('Living room', 'Bedroom', 'Kitchen', 'Dining area', 'Bathroom', 'Office', 'Hallway', 'Balcony', 'Other')
DESIGN_STYLES = ('Modern', 'Minimalist', 'Luxury', 'Contemporary', 'Classic', 'African-inspired', 'Not sure — recommend a style', 'Other')
CONTACT_METHODS = ('WhatsApp', 'Phone call', 'Email')
PROJECT_SCOPES = ('Consultation only', 'Design and styling', 'Furniture and décor sourcing', 'Materials and installation', 'Complete project', 'Not sure yet')
ATTACHMENT_CATEGORIES = ('current_space', 'inspiration', 'floor_plan')


def configured_budget_ranges():
    try:
        values = json.loads(current_app.config['HOME_DECOR_BUDGET_RANGES_JSON'])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('HOME_DECOR_BUDGET_RANGES_JSON must contain a JSON array.') from None
    if not isinstance(values, list) or not values:
        raise RuntimeError('HOME_DECOR_BUDGET_RANGES_JSON must contain at least one option.')
    result = []
    seen = set()
    for option in values:
        if not isinstance(option, dict):
            raise RuntimeError('Each Home Décor budget option needs a value and label.')
        value, label = option.get('value'), option.get('label')
        if not isinstance(value, str) or not value or len(value) > 40 or value in seen:
            raise RuntimeError('Home Décor budget option values must be unique short strings.')
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise RuntimeError('Home Décor budget option labels must be non-empty and at most 100 characters.')
        seen.add(value)
        result.append({'value': value, 'label': label.strip()})
    return result


def clean_decor_answers(form, user):
    full_name = str(form.get('full_name') or '').strip()
    phone = str(form.get('phone') or '').strip()
    email = str(form.get('email') or '').strip().lower()
    if len(full_name) < 2 or len(full_name) > 160:
        raise ValueError('Enter your full name (2 to 160 characters).')
    if len(phone) < 7 or len(phone) > 30 or not any(character.isdigit() for character in phone):
        raise ValueError('Enter a valid phone number.')
    if email and (len(email) > 254 or '@' not in email or email.startswith('@') or email.endswith('@')):
        raise ValueError('Enter a valid email address or leave it blank.')
    contact_method = form.get('contact_method', '')
    if contact_method not in CONTACT_METHODS:
        raise ValueError('Choose a valid preferred contact method.')
    if contact_method == 'Email' and not email and not (user and user.email):
        raise ValueError('Add an email address to use email as your preferred contact method.')

    property_type = form.get('property_type', '')
    property_status = form.get('property_status', '')
    inspection_access = form.get('inspection_access', '')
    if property_type not in PROPERTY_TYPES:
        raise ValueError('Choose a valid property type.')
    if property_status not in PROPERTY_STATUSES:
        raise ValueError('Choose a valid property status.')
    if inspection_access not in INSPECTION_OPTIONS:
        raise ValueError('Choose an inspection access option.')
    location = str(form.get('location') or '').strip()
    if len(location) < 2 or len(location) > 500:
        raise ValueError('Enter a property location in Chigo’s supported service area.')
    bedroom_count = None
    if property_type != 'Office or commercial space':
        try:
            bedroom_count = int(str(form.get('bedroom_count') or '').strip())
        except ValueError:
            raise ValueError('Enter the number of bedrooms, or choose a commercial property.') from None
        if bedroom_count < 0 or bedroom_count > 100:
            raise ValueError('Bedrooms must be between 0 and 100.')
    size = str(form.get('property_size') or '').strip()
    if size and (len(size) > 80 or not any(character.isdigit() for character in size)):
        raise ValueError('Enter an approximate size with a numeric value, for example 90 m².')

    budgets = {option['value'] for option in configured_budget_ranges()}
    budget = form.get('budget_range', '')
    if budget not in budgets:
        raise ValueError('Choose a valid estimated budget range.')
    services = list(dict.fromkeys(form.getlist('decor_services')))
    rooms = list(dict.fromkeys(form.getlist('rooms')))
    if not services or any(value not in DECOR_SERVICES for value in services):
        raise ValueError('Select at least one valid décor service.')
    if not rooms or any(value not in ROOM_OPTIONS for value in rooms):
        raise ValueError('Select at least one valid room or area.')
    other_service = str(form.get('other_service') or '').strip()
    if 'Other' in services and not other_service:
        raise ValueError('Describe the additional décor service you need.')
    if len(other_service) > 500:
        raise ValueError('The additional service description must be at most 500 characters.')
    style = form.get('design_style', '')
    if style not in DESIGN_STYLES:
        raise ValueError('Choose a valid design style.')
    other_style = str(form.get('other_style') or '').strip()
    if style == 'Other' and not other_style:
        raise ValueError('Describe your preferred design style.')
    if not str(form.get('desired_outcome') or '').strip():
        raise ValueError('Describe the outcome you would like Chigo to help you achieve.')
    scope = form.get('project_scope', '')
    if scope not in PROJECT_SCOPES:
        raise ValueError('Choose a valid project scope.')
    if form.get('source_materials') not in {'yes', 'no'}:
        raise ValueError('Choose whether you want Chigo to source furniture or materials.')
    if form.get('timeline_flexible') not in {'yes', 'no'}:
        raise ValueError('Choose whether your timeline is flexible.')

    start_date = _optional_date(form.get('preferred_start_date'), 'Preferred project start date')
    completion_date = _optional_date(form.get('desired_completion_date'), 'Desired completion date')
    consultation_date = _optional_date(form.get('preferred_consultation_date'), 'Preferred consultation date')
    today = date.today()
    if start_date and start_date < today:
        raise ValueError('Preferred project start date cannot be in the past.')
    if consultation_date and consultation_date < today:
        raise ValueError('Preferred consultation date cannot be in the past.')
    if start_date and completion_date and completion_date < start_date:
        raise ValueError('Desired completion date cannot precede the preferred start date.')
    if form.get('contact_consent') != 'on':
        raise ValueError('Consent to be contacted about this enquiry is required.')

    return {
        'schema_version': 1,
        'client': {'full_name': full_name, 'phone': phone, 'email': email, 'contact_method': contact_method},
        'property': {'type': property_type, 'status': property_status, 'location': location,
                     'bedroom_count': bedroom_count, 'approximate_size': size,
                     'inspection_access': inspection_access},
        'design': {
            'services': services, 'rooms': rooms, 'other_service': other_service,
            'style': style, 'other_style': other_style,
            'preferred_colours': str(form.get('preferred_colours') or '').strip()[:300],
            'desired_outcome': str(form.get('desired_outcome') or '').strip()[:2000],
            'retain_items': str(form.get('retain_items') or '').strip()[:1000],
            'items_to_source': str(form.get('items_to_source') or '').strip()[:1000],
            'room_measurements': str(form.get('room_measurements') or '').strip()[:1000],
            'reference_links': _clean_reference_links(form.getlist('reference_links')),
        },
        'project': {'budget_range': budget, 'scope': scope,
                    'source_materials': form.get('source_materials') == 'yes',
                    'material_requirements': str(form.get('material_requirements') or '').strip()[:1000]},
        'timeline': {'preferred_start_date': start_date.isoformat() if start_date else None,
                     'desired_completion_date': completion_date.isoformat() if completion_date else None,
                     'flexible': form.get('timeline_flexible') == 'yes',
                     'consultation_date': consultation_date.isoformat() if consultation_date else None,
                     'instructions': str(form.get('instructions') or '').strip()[:2000]},
        'contact_consent': True,
    }


def _optional_date(value, label):
    value = str(value or '').strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label} must be a valid date.') from None


def _clean_reference_links(values):
    links = []
    for value in values:
        value = str(value or '').strip()
        if not value:
            continue
        if len(value) > 500 or not value.startswith(('https://', 'http://')):
            raise ValueError('Reference links must be valid HTTP or HTTPS URLs under 500 characters.')
        links.append(value)
    if len(links) > 10:
        raise ValueError('Enter no more than 10 reference links.')
    return links