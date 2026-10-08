from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, FloatField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional


class LoginForm(FlaskForm):
    email = StringField('Email', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class RegistrationForm(FlaskForm):
    full_name = StringField('Full name', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Length(min=2, max=160)])
    email = StringField('Email', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8)])
    submit = SubmitField('Create Account')


class AdminAccountForm(RegistrationForm):
    role = SelectField('Role', choices=[
        ('operations_manager', 'Operations Manager'),
        ('dispatcher', 'Dispatcher'),
        ('finance_manager', 'Finance Manager'),
        ('customer_support', 'Customer Support'),
        ('fleet_manager', 'Fleet Manager'),
        ('content_marketing_manager', 'Content / Marketing Manager'),
    ], default='operations_manager', validators=[DataRequired()])


class TruckPartnerForm(FlaskForm):
    name = StringField('Contact name', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Length(max=120)])
    company = StringField('Company', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=120)])
    phone = StringField('Phone', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=30)])
    email = StringField('Email', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Email(), Length(max=120)])
    vehicle_type = StringField('Vehicle type', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Length(max=60)])
    capacity = StringField('Capacity', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=50)])
    rate = FloatField('Partner rate (NGN)', validators=[DataRequired(), NumberRange(min=0)])
    operating_areas = StringField('Operating areas', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=255)])
    submit = SubmitField('Add truck partner')


class MoverForm(FlaskForm):
    name = StringField('Mover name', filters=[lambda value: value.strip() if value else value], validators=[DataRequired(), Length(max=120)])
    phone = StringField('Phone', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=30)])
    skills = StringField('Skills', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=255)])
    experience = StringField('Experience', filters=[lambda value: value.strip() if value else value], validators=[Optional(), Length(max=100)])
    submit = SubmitField('Add mover')


class PasswordResetRequestForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send reset link')


class PasswordResetForm(FlaskForm):
    password = PasswordField('New password', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirm new password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset password')


class BookingLocationForm(FlaskForm):
    pickup_address = StringField('Pickup address', validators=[DataRequired()])
    pickup_unit = StringField('Apartment/unit', validators=[Optional()])
    pickup_area = StringField('Area', validators=[Optional(), Length(max=120)])
    pickup_area_council = StringField('Pickup Area Council', validators=[Optional(), Length(max=100)])
    pickup_district = StringField('Pickup district', validators=[Optional(), Length(max=120)])
    pickup_neighborhood = StringField('Pickup neighborhood', validators=[Optional(), Length(max=120)])
    pickup_location_type = StringField('Pickup location type', validators=[Optional(), Length(max=60)])
    pickup_original_input = StringField('Original pickup search', validators=[Optional(), Length(max=500)])
    pickup_normalized_location = StringField('Normalized pickup location', validators=[Optional(), Length(max=500)])
    pickup_city = StringField('Pickup city', validators=[Optional(), Length(max=100)])
    pickup_state = StringField('Pickup state', validators=[Optional(), Length(max=100)])
    pickup_country = StringField('Pickup country', default='Nigeria', validators=[Optional()])
    pickup_formatted_address = StringField('Pickup formatted address', validators=[Optional()])
    pickup_latitude = FloatField('Pickup latitude', validators=[Optional()])
    pickup_longitude = FloatField('Pickup longitude', validators=[Optional()])
    pickup_place_id = StringField('Pickup place id', validators=[Optional(), Length(max=255)])
    pickup_location_id = IntegerField('Pickup location id', validators=[Optional()])
    pickup_provider = StringField('Pickup provider', validators=[Optional(), Length(max=50)])
    pickup_provider_raw_id = StringField('Pickup provider raw id', validators=[Optional(), Length(max=255)])
    pickup_notes = TextAreaField('Pickup instructions', validators=[Optional()])
    pickup_access = SelectField('Pickup access', choices=[('Easy', 'Easy'), ('Moderate', 'Moderate'), ('Difficult', 'Difficult'), ('Not sure', 'Not sure')], validators=[Optional()])
    pickup_inside_estate = BooleanField('Pickup is inside an estate')
    pickup_narrow_road = BooleanField('Narrow road at pickup')
    pickup_parking_close = BooleanField('Parking close to pickup')
    pickup_floor = SelectField('Pickup floor', choices=[('Ground', 'Ground'), ('1st', '1st'), ('2nd', '2nd'), ('3rd', '3rd'), ('4th+', '4th+')], validators=[Optional()])
    pickup_stairs = BooleanField('Stairs at pickup')
    destination_address = StringField('Destination address', validators=[DataRequired()])
    destination_unit = StringField('Apartment/unit', validators=[Optional()])
    destination_area = StringField('Area', validators=[Optional(), Length(max=120)])
    destination_area_council = StringField('Destination Area Council', validators=[Optional(), Length(max=100)])
    destination_district = StringField('Destination district', validators=[Optional(), Length(max=120)])
    destination_neighborhood = StringField('Destination neighborhood', validators=[Optional(), Length(max=120)])
    destination_location_type = StringField('Destination location type', validators=[Optional(), Length(max=60)])
    destination_original_input = StringField('Original destination search', validators=[Optional(), Length(max=500)])
    destination_normalized_location = StringField('Normalized destination location', validators=[Optional(), Length(max=500)])
    destination_city = StringField('Destination city', validators=[Optional(), Length(max=100)])
    destination_state = StringField('Destination state', validators=[Optional(), Length(max=100)])
    destination_country = StringField('Destination country', default='Nigeria', validators=[Optional()])
    destination_formatted_address = StringField('Destination formatted address', validators=[Optional()])
    destination_latitude = FloatField('Destination latitude', validators=[Optional()])
    destination_longitude = FloatField('Destination longitude', validators=[Optional()])
    destination_place_id = StringField('Destination place id', validators=[Optional(), Length(max=255)])
    dropoff_location_id = IntegerField('Drop-off location id', validators=[Optional()])
    destination_provider = StringField('Destination provider', validators=[Optional(), Length(max=50)])
    destination_provider_raw_id = StringField('Destination provider raw id', validators=[Optional(), Length(max=255)])
    destination_notes = TextAreaField('Destination instructions', validators=[Optional()])
    destination_access = SelectField('Destination access', choices=[('Easy', 'Easy'), ('Moderate', 'Moderate'), ('Difficult', 'Difficult'), ('Not sure', 'Not sure')], validators=[Optional()])
    destination_inside_estate = BooleanField('Destination is inside an estate')
    destination_narrow_road = BooleanField('Narrow road at destination')
    destination_parking_close = BooleanField('Parking close to destination')
    destination_floor = SelectField('Destination floor', choices=[('Ground', 'Ground'), ('1st', '1st'), ('2nd', '2nd'), ('3rd', '3rd'), ('4th+', '4th+')], validators=[Optional()])
    destination_stairs = BooleanField('Stairs at destination')
    distance_km = FloatField('Estimated distance in kilometres', validators=[Optional(), NumberRange(min=0, max=5000)])
    submit = SubmitField('Continue')


class InventoryItemForm(FlaskForm):
    name = StringField('Item name', validators=[DataRequired(), Length(min=2, max=120)])
    category = SelectField('Category', choices=[
        ('Living Room', 'Living Room'), ('Bedroom', 'Bedroom'), ('Dining Room', 'Dining Room'),
        ('Kitchen', 'Kitchen'), ('Electronics', 'Electronics'), ('Office', 'Office'),
        ('Bathroom', 'Bathroom'), ('Outdoor / Balcony', 'Outdoor / Balcony'),
        ('Garage / Storage', 'Garage / Storage'), ("Children's Items", "Children's Items"),
        ('Fitness', 'Fitness'), ('Fragile / Valuable', 'Fragile / Valuable'),
        ('Miscellaneous', 'Miscellaneous'), ('Custom', 'Custom'),
    ], validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional(), Length(max=2000)])
    estimated_volume_m3 = FloatField('Estimated volume (m³)', validators=[NumberRange(min=0, max=100)])
    estimated_weight_kg = FloatField('Estimated weight (kg)', validators=[NumberRange(min=0, max=10000)])
    dimensions_length_cm = FloatField('Length (cm)', validators=[Optional(), NumberRange(min=0, max=10000)])
    dimensions_width_cm = FloatField('Width (cm)', validators=[Optional(), NumberRange(min=0, max=10000)])
    dimensions_height_cm = FloatField('Height (cm)', validators=[Optional(), NumberRange(min=0, max=10000)])
    aliases = StringField('Aliases', validators=[Optional(), Length(max=500)])
    fragile = BooleanField('Fragile')
    special_handling = BooleanField('Requires special handling')
    vehicle_compatibility = StringField('Vehicle compatibility', validators=[Optional(), Length(max=255)])
    active = BooleanField('Active in catalogue', default=True)
    sort_order = IntegerField('Sort order', validators=[Optional(), NumberRange(min=0, max=10000)])
    submit = SubmitField('Save catalogue item')


class PricingForm(FlaskForm):
    base_moving_fee = IntegerField('Base moving fee')
    price_per_km = IntegerField('Price per km')
    property_surcharge = IntegerField('Property surcharge')
    floor_surcharge = IntegerField('Floor surcharge')
    packing_partial = IntegerField('Partial packing')
    packing_full = IntegerField('Full packing')
    cleaning_move_out = IntegerField('Move-out cleaning')
    cleaning_move_in = IntegerField('Move-in cleaning')
    cleaning_both = IntegerField('Move-in and move-out cleaning')
    unpacking = IntegerField('Unpacking')
    service_assembly_yes = IntegerField('Furniture assembly')
    service_disassembly_yes = IntegerField('Furniture disassembly')
    storage_week = IntegerField('Storage week')
    special_handling = IntegerField('Special handling')
    weekend_surcharge = IntegerField('Weekend surcharge')
    holiday_surcharge = IntegerField('Holiday surcharge')
    additional_mover_fee = IntegerField('Additional mover fee')
    base_mover_hourly_fee = IntegerField('Mover hourly rate')
    access_difficulty_surcharge = IntegerField('Access difficulty surcharge')
    outside_service_area_surcharge = IntegerField('Outside service area surcharge')
    tax_rate = FloatField('Tax rate (%)', validators=[DataRequired(), NumberRange(min=0, max=100)])
    deposit_percentage = FloatField('Deposit (%)', validators=[DataRequired(), NumberRange(min=0, max=100)])
    volume_safety_factor = FloatField('Volume safety buffer multiplier', validators=[DataRequired(), NumberRange(min=1.0, max=2.0)])
    submit = SubmitField('Save pricing')
