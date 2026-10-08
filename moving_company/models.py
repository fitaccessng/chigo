from datetime import datetime

from flask_login import UserMixin

from .extensions import bcrypt, db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    role = db.Column(db.String(50), default='customer')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bookings = db.relationship('Booking', back_populates='customer', foreign_keys='Booking.customer_id')
    notifications = db.relationship('Notification', back_populates='user')
    reviews = db.relationship('Review', back_populates='user')
    support_conversations = db.relationship(
        'SupportConversation', back_populates='customer', foreign_keys='SupportConversation.customer_id',
        cascade='all, delete-orphan',
    )
    support_requests = db.relationship('SupportRequest', back_populates='customer', foreign_keys='SupportRequest.customer_id', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()


class SupportConversation(db.Model):
    __tablename__ = 'support_conversations'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    assigned_agent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='AI_ASSISTED', index=True)
    human_requested = db.Column(db.Boolean, nullable=False, default=False)
    human_requested_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    customer = db.relationship(
        'User', back_populates='support_conversations', foreign_keys=[customer_id]
    )
    assigned_agent = db.relationship('User', foreign_keys=[assigned_agent_id])
    booking = db.relationship('Booking', foreign_keys=[booking_id])
    messages = db.relationship('SupportMessage', back_populates='conversation', cascade='all, delete-orphan')
    requests = db.relationship('SupportRequest', back_populates='conversation', cascade='all, delete-orphan')


class SupportMessage(db.Model):
    __tablename__ = 'support_messages'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('support_conversations.id'), nullable=False, index=True)
    sender_type = db.Column(db.String(20), nullable=False)
    sender_id = db.Column(db.Integer, nullable=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    conversation = db.relationship('SupportConversation', back_populates='messages', foreign_keys=[conversation_id])


class SupportRequest(db.Model):
    __tablename__ = 'support_requests'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('support_conversations.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(30), nullable=False, default='WAITING_FOR_AGENT', index=True)
    assigned_at = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    email_status = db.Column(db.String(20), nullable=False, default='pending')

    customer = db.relationship('User', back_populates='support_requests', foreign_keys=[customer_id])
    conversation = db.relationship('SupportConversation', back_populates='requests', foreign_keys=[conversation_id])
    booking = db.relationship('Booking', foreign_keys=[booking_id])


class Booking(db.Model):
    __tablename__ = 'bookings'

    id = db.Column(db.Integer, primary_key=True)
    booking_request_id = db.Column(db.String(32), unique=True, nullable=True, index=True)
    booking_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(50), default='Booking Received')
    primary_service = db.Column(db.String(30), nullable=False, default='residential', index=True)
    workflow_state = db.Column(db.String(40), nullable=False, default='CONFIRMED')
    quote_status = db.Column(db.String(30), nullable=False, default='Draft')
    workflow_data = db.Column(db.JSON, nullable=False, default=dict)
    calculation_data = db.Column(db.JSON, nullable=False, default=dict)
    estimated_duration_minutes = db.Column(db.Integer)
    flexible_date = db.Column(db.Boolean, nullable=False, default=False)
    flexible_time = db.Column(db.Boolean, nullable=False, default=False)
    schedule_feasible = db.Column(db.Boolean)
    manually_overridden_total = db.Column(db.Float)
    price_override_reason = db.Column(db.Text)
    move_date = db.Column(db.Date, nullable=True)
    preferred_time = db.Column(db.String(20), default='Morning')
    property_type = db.Column(db.String(50), default='2 Bedroom')
    pickup_address = db.Column(db.String(255), nullable=False)
    pickup_unit = db.Column(db.String(100))
    pickup_area = db.Column(db.String(120))
    pickup_area_council = db.Column(db.String(100))
    pickup_district = db.Column(db.String(120))
    pickup_neighborhood = db.Column(db.String(120))
    pickup_location_type = db.Column(db.String(60))
    pickup_original_input = db.Column(db.String(500))
    pickup_normalized_location = db.Column(db.String(500))
    pickup_local_place_id = db.Column(db.Integer, db.ForeignKey('local_places.id'), nullable=True, index=True)
    pickup_location_id = db.Column(db.Integer, nullable=True, index=True)
    pickup_nearest_landmark = db.Column(db.String(180))
    pickup_nearest_road = db.Column(db.String(180))
    pickup_route_resolution = db.Column(db.String(40))
    pickup_city = db.Column(db.String(100), nullable=False)
    pickup_state = db.Column(db.String(100), nullable=False)
    pickup_country = db.Column(db.String(100), nullable=False, default='Nigeria')
    pickup_formatted_address = db.Column(db.Text)
    pickup_latitude = db.Column(db.Float, default=0.0)
    pickup_longitude = db.Column(db.Float, default=0.0)
    pickup_place_id = db.Column(db.String(255))
    pickup_provider = db.Column(db.String(50), default='manual')
    pickup_provider_raw_id = db.Column(db.String(255))
    pickup_notes = db.Column(db.Text)
    pickup_access = db.Column(db.String(30), default='Not sure')
    pickup_inside_estate = db.Column(db.Boolean, default=False)
    pickup_narrow_road = db.Column(db.Boolean, default=False)
    pickup_parking_close = db.Column(db.Boolean, default=True)
    pickup_floor = db.Column(db.String(20), default='Ground')
    pickup_stairs = db.Column(db.Boolean, default=False)
    destination_address = db.Column(db.String(255), nullable=False)
    destination_unit = db.Column(db.String(100))
    destination_area = db.Column(db.String(120))
    destination_area_council = db.Column(db.String(100))
    destination_district = db.Column(db.String(120))
    destination_neighborhood = db.Column(db.String(120))
    destination_location_type = db.Column(db.String(60))
    destination_original_input = db.Column(db.String(500))
    destination_normalized_location = db.Column(db.String(500))
    destination_local_place_id = db.Column(db.Integer, db.ForeignKey('local_places.id'), nullable=True, index=True)
    dropoff_location_id = db.Column(db.Integer, nullable=True, index=True)
    destination_nearest_landmark = db.Column(db.String(180))
    destination_nearest_road = db.Column(db.String(180))
    destination_route_resolution = db.Column(db.String(40))
    destination_city = db.Column(db.String(100), nullable=False)
    destination_state = db.Column(db.String(100), nullable=False)
    destination_country = db.Column(db.String(100), nullable=False, default='Nigeria')
    destination_formatted_address = db.Column(db.Text)
    destination_latitude = db.Column(db.Float, default=0.0)
    destination_longitude = db.Column(db.Float, default=0.0)
    destination_place_id = db.Column(db.String(255))
    destination_provider = db.Column(db.String(50), default='manual')
    destination_provider_raw_id = db.Column(db.String(255))
    destination_notes = db.Column(db.Text)
    destination_access = db.Column(db.String(30), default='Not sure')
    destination_inside_estate = db.Column(db.Boolean, default=False)
    destination_narrow_road = db.Column(db.Boolean, default=False)
    destination_parking_close = db.Column(db.Boolean, default=True)
    destination_floor = db.Column(db.String(20), default='Ground')
    destination_stairs = db.Column(db.Boolean, default=False)
    distance_km = db.Column(db.Float, default=0.0)
    route_duration_minutes = db.Column(db.Float)
    route_provider = db.Column(db.String(40))
    distance_source = db.Column(db.String(80))
    distance_precision = db.Column(db.String(30))
    route_status = db.Column(db.String(30), nullable=False, default='PENDING')
    route_calculated_at = db.Column(db.DateTime)
    service_area_status = db.Column(db.String(40))
    number_of_bedrooms = db.Column(db.Integer, default=0)
    number_of_living_rooms = db.Column(db.Integer, default=0)
    number_of_kitchens = db.Column(db.Integer, default=0)
    number_of_bathrooms = db.Column(db.Integer, default=0)
    number_of_floors = db.Column(db.Integer, default=1)
    estimated_volume_m3 = db.Column(db.Float, default=0.0)
    estimated_weight_kg = db.Column(db.Float, default=0.0)
    recommended_vehicle_type_id = db.Column(db.Integer, db.ForeignKey('vehicle_types.id'), nullable=True)
    assigned_vehicle_type_id = db.Column(db.Integer, db.ForeignKey('vehicle_types.id'), nullable=True)
    assigned_partner_vehicle_id = db.Column(db.Integer, db.ForeignKey('partner_vehicles.id'), nullable=True)
    vehicle_review_required = db.Column(db.Boolean, default=False)
    vehicle_override_reason = db.Column(db.Text)
    overridden_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    overridden_at = db.Column(db.DateTime)
    estimated_total = db.Column(db.Float, default=0.0)
    deposit_amount = db.Column(db.Float, default=0.0)
    balance_amount = db.Column(db.Float, default=0.0)
    payment_status = db.Column(db.String(30), default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship('User', back_populates='bookings', foreign_keys=[customer_id])
    items = db.relationship('BookingItem', back_populates='booking', cascade='all, delete-orphan')
    inventory = db.relationship('BookingInventory', back_populates='booking', cascade='all, delete-orphan')
    photos = db.relationship('BookingPhoto', back_populates='booking', cascade='all, delete-orphan')
    payments = db.relationship('Payment', back_populates='booking', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', back_populates='booking', cascade='all, delete-orphan')
    reviews = db.relationship('Review', back_populates='booking', cascade='all, delete-orphan')
    incidents = db.relationship('IncidentReport', back_populates='booking', cascade='all, delete-orphan')
    assignments = db.relationship('BookingAssignment', back_populates='booking', cascade='all, delete-orphan')
    events = db.relationship('BookingEvent', back_populates='booking', cascade='all, delete-orphan')
    quote_lines = db.relationship('BookingQuoteLine', back_populates='booking', cascade='all, delete-orphan')
    recommended_vehicle_type = db.relationship('VehicleType', foreign_keys=[recommended_vehicle_type_id])
    assigned_vehicle_type = db.relationship('VehicleType', foreign_keys=[assigned_vehicle_type_id])
    assigned_partner_vehicle = db.relationship('PartnerVehicle', foreign_keys=[assigned_partner_vehicle_id])
    pickup_local_place = db.relationship('LocalPlace', foreign_keys=[pickup_local_place_id])
    destination_local_place = db.relationship('LocalPlace', foreign_keys=[destination_local_place_id])


class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    category = db.Column(db.String(60), nullable=False, index=True)
    description = db.Column(db.Text)
    image = db.Column(db.String(255))
    estimated_volume_m3 = db.Column(db.Float, nullable=False, default=0.0)
    estimated_weight_kg = db.Column(db.Float, nullable=False, default=0.0)
    dimensions_cm = db.Column(db.JSON)
    aliases = db.Column(db.Text)
    fragile = db.Column(db.Boolean, nullable=False, default=False)
    special_handling = db.Column(db.Boolean, nullable=False, default=False)
    vehicle_compatibility = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    selections = db.relationship('BookingInventory', back_populates='catalogue_item', cascade='all, delete-orphan')


class BookingInventory(db.Model):
    __tablename__ = 'booking_inventory'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False, index=True)
    inventory_item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=True, index=True)
    custom_name = db.Column(db.String(120))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    size = db.Column(db.String(20), nullable=False, default='medium')
    estimated_volume_m3 = db.Column(db.Float, nullable=False, default=0.0)
    estimated_weight_kg = db.Column(db.Float, nullable=False, default=0.0)
    category = db.Column(db.String(60), nullable=False)
    notes = db.Column(db.Text)
    fragile = db.Column(db.Boolean, nullable=False, default=False)
    large = db.Column(db.Boolean, nullable=False, default=False)
    special_handling = db.Column(db.Boolean, nullable=False, default=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    dimensions_cm = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    booking = db.relationship('Booking', back_populates='inventory')
    catalogue_item = db.relationship('InventoryItem', back_populates='selections')
    photos = db.relationship('BookingPhoto', back_populates='booking_inventory', cascade='all, delete-orphan')

    @property
    def display_name(self):
        return self.custom_name or (self.catalogue_item.name if self.catalogue_item else 'Custom item')


class BookingItem(db.Model):
    __tablename__ = 'booking_items'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)
    fragile = db.Column(db.Boolean, default=False)
    large = db.Column(db.Boolean, default=False)
    size = db.Column(db.String(20), default='medium')
    special_handling = db.Column(db.Boolean, default=False)
    estimated_volume_m3 = db.Column(db.Float, default=0.0)
    estimated_weight_kg = db.Column(db.Float, default=0.0)
    details = db.Column(db.JSON, nullable=False, default=dict)
    dimensions_cm = db.Column(db.JSON)
    photo_path = db.Column(db.String(255))

    booking = db.relationship('Booking', back_populates='items')
    photos = db.relationship('BookingPhoto', back_populates='booking_item', cascade='all, delete-orphan')


class BookingQuoteLine(db.Model):
    __tablename__ = 'booking_quote_lines'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False, index=True)
    code = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit_amount = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(20), nullable=False, default='system')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    booking = db.relationship('Booking', back_populates='quote_lines')


class BookingEvent(db.Model):
    __tablename__ = 'booking_events'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    previous_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    booking = db.relationship('Booking', back_populates='events')
    user = db.relationship('User')


class BookingPhoto(db.Model):
    __tablename__ = 'booking_photos'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False, index=True)
    booking_item_id = db.Column(db.Integer, db.ForeignKey('booking_items.id'), nullable=True, index=True)
    booking_inventory_id = db.Column(db.Integer, db.ForeignKey('booking_inventory.id'), nullable=True, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(60), default='inventory', nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    booking = db.relationship('Booking', back_populates='photos')
    booking_item = db.relationship('BookingItem', back_populates='photos')
    booking_inventory = db.relationship('BookingInventory', back_populates='photos')
    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])


class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='Pending')
    payment_reference = db.Column(db.String(100), nullable=True)
    gateway = db.Column(db.String(40))
    authorization_url = db.Column(db.Text)
    currency = db.Column(db.String(3), nullable=False, default='NGN')
    transaction_status = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    booking = db.relationship('Booking', back_populates='payments')


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='notifications')
    booking = db.relationship('Booking', back_populates='notifications')


class ServicePricing(db.Model):
    __tablename__ = 'service_pricing'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Float, default=0.0)
    label = db.Column(db.String(120), nullable=False)


class Review(db.Model):
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    booking = db.relationship('Booking', back_populates='reviews')
    user = db.relationship('User', back_populates='reviews')


class IncidentReport(db.Model):
    __tablename__ = 'incident_reports'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    item = db.Column(db.String(120), nullable=True)
    incident_type = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    photos = db.Column(db.Text)
    reported_by = db.Column(db.String(120), nullable=False)
    date_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default='Open')
    resolution = db.Column(db.Text)

    booking = db.relationship('Booking', back_populates='incidents')


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    entity = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(50), nullable=True)
    before_value = db.Column(db.Text)
    after_value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LocalPlace(db.Model):
    __tablename__ = 'local_places'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    normalized_name = db.Column(db.String(160), nullable=False, index=True)
    place_type = db.Column(db.String(60), default='AREA')
    parent_id = db.Column(db.Integer, db.ForeignKey('local_places.id'), index=True)
    area_council = db.Column(db.String(80), default='AMAC')
    district = db.Column(db.String(120))
    neighborhood = db.Column(db.String(120))
    area = db.Column(db.String(120))
    lga = db.Column(db.String(80))
    state = db.Column(db.String(100), nullable=False, default='Federal Capital Territory')
    country = db.Column(db.String(100), nullable=False, default='Nigeria')
    city = db.Column(db.String(100))
    address = db.Column(db.Text)
    landmark = db.Column(db.String(180))
    location_type = db.Column(db.String(60))
    confidence = db.Column(db.Float)
    precision_level = db.Column(db.String(30))
    aliases = db.Column(db.Text)
    search_keywords = db.Column(db.Text)
    popularity_score = db.Column(db.Float, nullable=False, default=0.0)
    latitude = db.Column(db.Float, default=0.0)
    longitude = db.Column(db.Float, default=0.0)
    provider = db.Column(db.String(50), default='local')
    provider_place_id = db.Column(db.String(255))
    coordinate_precision = db.Column(db.String(30), default='unknown')
    verified = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True)
    search_count = db.Column(db.Integer, nullable=False, default=0)
    selection_count = db.Column(db.Integer, nullable=False, default=0)
    booking_count = db.Column(db.Integer, nullable=False, default=0)
    last_used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent = db.relationship('LocalPlace', remote_side=[id], back_populates='children')
    children = db.relationship('LocalPlace', back_populates='parent')


class TruckPartner(db.Model):
    __tablename__ = 'truck_partners'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    vehicle_type = db.Column(db.String(60), default='Canter')
    capacity = db.Column(db.String(50))
    status = db.Column(db.String(30), default='Available')
    rate = db.Column(db.Float, default=0.0)
    operating_areas = db.Column(db.String(255))
    notes = db.Column(db.Text)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    seed_source = db.Column(db.String(40), nullable=False, default='production')
    vehicles = db.relationship('PartnerVehicle', back_populates='partner', cascade='all, delete-orphan')
    trucks = db.relationship('Truck', back_populates='partner', cascade='all, delete-orphan')


class VehicleType(db.Model):
    __tablename__ = 'vehicle_types'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    body_type = db.Column(db.String(80))
    capacity_m3 = db.Column(db.Float, nullable=False)
    max_payload_kg = db.Column(db.Float, nullable=False)
    length_m = db.Column(db.Float)
    width_m = db.Column(db.Float)
    height_m = db.Column(db.Float)
    image = db.Column(db.String(255))
    icon = db.Column(db.String(80))
    best_for = db.Column(db.String(255))
    base_price = db.Column(db.Float, default=0.0)
    price_per_km = db.Column(db.Float, default=0.0)
    minimum_price = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    partner_vehicles = db.relationship('PartnerVehicle', back_populates='vehicle_type')


class PartnerVehicle(db.Model):
    __tablename__ = 'partner_vehicles'

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey('truck_partners.id'), nullable=False)
    vehicle_type_id = db.Column(db.Integer, db.ForeignKey('vehicle_types.id'), nullable=False)
    registration_number = db.Column(db.String(60), unique=True, nullable=False)
    actual_capacity_m3 = db.Column(db.Float, nullable=False)
    max_payload_kg = db.Column(db.Float, nullable=False)
    length_m = db.Column(db.Float)
    width_m = db.Column(db.Float)
    height_m = db.Column(db.Float)
    partner_rate = db.Column(db.Float, default=0.0)
    operating_areas = db.Column(db.String(255))
    status = db.Column(db.String(30), default='Available')
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    image = db.Column(db.String(255))

    partner = db.relationship('TruckPartner', back_populates='vehicles')
    vehicle_type = db.relationship('VehicleType', back_populates='partner_vehicles')


class Truck(db.Model):
    __tablename__ = 'trucks'

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey('truck_partners.id'))
    registration_number = db.Column(db.String(60), nullable=False, unique=True)
    vehicle_type = db.Column(db.String(60), default='Canter')
    capacity = db.Column(db.String(50))
    status = db.Column(db.String(30), default='Available')
    current_location = db.Column(db.String(120))
    rate = db.Column(db.Float, default=0.0)
    payload_capacity_kg = db.Column(db.Float)
    dimensions_m = db.Column(db.String(80))
    driver_required = db.Column(db.Boolean, default=True)
    fuel_assumption = db.Column(db.String(120))
    image = db.Column(db.String(255))
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    seed_source = db.Column(db.String(40), nullable=False, default='production')

    assignments = db.relationship('BookingAssignment', back_populates='truck', cascade='all, delete-orphan')
    partner = db.relationship('TruckPartner', back_populates='trucks')
    images = db.relationship('VehicleImage', back_populates='truck', cascade='all, delete-orphan')


class VehicleImage(db.Model):
    __tablename__ = 'vehicle_images'

    id = db.Column(db.Integer, primary_key=True)
    truck_id = db.Column(db.Integer, db.ForeignKey('trucks.id'), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    truck = db.relationship('Truck', back_populates='images')


class Team(db.Model):
    __tablename__ = 'teams'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    team_leader = db.Column(db.String(120))
    current_assignment = db.Column(db.String(160))
    jobs_completed = db.Column(db.Integer, nullable=False, default=0)
    availability = db.Column(db.String(30), default='Available')
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    seed_source = db.Column(db.String(40), nullable=False, default='production')
    movers = db.relationship('Mover', back_populates='team', cascade='all, delete-orphan')


class Mover(db.Model):
    __tablename__ = 'movers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    skills = db.Column(db.String(255))
    experience = db.Column(db.String(100))
    status = db.Column(db.String(30), default='Available')
    rating = db.Column(db.Float, default=4.8)
    jobs_completed = db.Column(db.Integer, nullable=False, default=0)
    current_assignment = db.Column(db.String(160))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    seed_source = db.Column(db.String(40), nullable=False, default='production')
    team = db.relationship('Team', back_populates='movers')


class Cleaner(db.Model):
    __tablename__ = 'cleaners'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120))
    location = db.Column(db.String(120))
    service_areas = db.Column(db.String(255))
    rate = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default='Available')


class BookingAssignment(db.Model):
    __tablename__ = 'booking_assignments'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    truck_id = db.Column(db.Integer, db.ForeignKey('trucks.id'), nullable=True)
    driver_name = db.Column(db.String(120))
    mover_1 = db.Column(db.String(120))
    mover_2 = db.Column(db.String(120))
    mover_3 = db.Column(db.String(120))
    mover_4 = db.Column(db.String(120))
    cleaner = db.Column(db.String(120))
    conflict_message = db.Column(db.Text)

    booking = db.relationship('Booking', back_populates='assignments')
    truck = db.relationship('Truck', back_populates='assignments')
