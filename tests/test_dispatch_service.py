from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import (
    Booking, DispatchOffer, DispatchRequest, Mover, PartnerVehicle,
    ResourceReservation, TruckPartner, User, VehicleType,
)
from moving_company.services.booking_engine_service import create_booking_request, replace_inventory, save_workflow_section
from moving_company.services.dispatch_service import _serializer_token, respond_to_offer, start_dispatch


@pytest.fixture
def app():
    app = create_app(testing=True)
    yield app
    with app.app_context():
        db.session.remove()


def confirmed_paid_booking():
    booking = create_booking_request(
        customer_id=None,
        location={
            'pickup_address': '1 Test Road', 'pickup_city': 'Abuja', 'pickup_state': 'FCT',
            'pickup_area': 'Gwarinpa', 'pickup_area_council': 'Gwarinpa',
            'destination_address': '2 Test Road', 'destination_city': 'Abuja', 'destination_state': 'FCT',
            'destination_area': 'Lokogoma', 'destination_area_council': 'Lokogoma',
            'distance_km': 20, 'route_status': 'CALCULATED', 'distance_precision': 'exact',
            'service_area_status': 'SERVICEABLE', 'pickup_latitude': 9.1, 'pickup_longitude': 7.4,
            'destination_latitude': 9.0, 'destination_longitude': 7.5,
        },
    )
    save_workflow_section(booking, 'property', {
        'property_type': 'Apartment', 'bedrooms': 2, 'bathrooms': 1, 'floors': 1,
        'service_answers': {
            'destination_property_type': 'Flat / Apartment',
            'destination_bedrooms': 2, 'destination_floors': 1,
            'restricted_vehicle_access': 'no', 'long_carry_distance': 'no',
        },
    })
    replace_inventory(booking, [{'name': 'Sofa', 'quantity': 1, 'category': 'Living Room'}])
    workflow_data = dict(booking.workflow_data or {})
    workflow_data.update({
        'inventory_confirmed': True,
        'inventory_answers': {'heavy_or_oversized': 'no'},
    })
    booking.workflow_data = workflow_data
    booking.workflow_state = 'CONFIRMED'
    booking.status = 'Confirmed'
    booking.quote_status = 'Accepted'
    booking.payment_status = 'Paid'
    booking.move_date = date.today() + timedelta(days=10)
    booking.preferred_time = 'Morning'
    booking.estimated_duration_minutes = 180
    booking.recommended_vehicle_type_id = VehicleType.query.filter_by(slug='canter').one().id
    booking.assigned_vehicle_type_id = booking.recommended_vehicle_type_id
    booking.calculation_data = {'vehicle': {'quantity': 1}, 'workforce': {'movers': 2}}
    db.session.commit()
    return booking


def add_partner_and_resources(booking, *, service_types=None, email='partner@example.test'):
    vehicle_type = db.session.get(VehicleType, booking.recommended_vehicle_type_id)
    partner = TruckPartner(
        name='Configured Partner', company='Configured Partner Ltd', email=email,
        vehicle_type=vehicle_type.name, status='Available', rate=50000,
        operating_areas='Gwarinpa, Lokogoma',
        service_types=['residential'] if service_types is None else service_types,
    )
    db.session.add(partner)
    db.session.flush()
    vehicle = PartnerVehicle(
        partner_id=partner.id, vehicle_type_id=vehicle_type.id,
        registration_number='TEST-DISPATCH-01', actual_capacity_m3=20,
        max_payload_kg=3000, length_m=5, width_m=2, height_m=2,
        partner_rate=50000, operating_areas='Gwarinpa, Lokogoma',
        status='Available', is_active=True,
    )
    movers = [Mover(name=f'Dispatch Mover {index}', status='Available') for index in (1, 2)]
    db.session.add_all([vehicle, *movers])
    db.session.commit()
    return partner, vehicle, movers


def test_dispatch_requires_verified_payment_before_creating_request(app):
    with app.app_context():
        booking = confirmed_paid_booking()
        booking.payment_status = 'Pending'
        db.session.commit()

        with pytest.raises(ValueError, match='Verified payment'):
            start_dispatch(booking.id)
        assert DispatchRequest.query.filter_by(booking_id=booking.id).count() == 0


def test_dispatch_offer_holds_real_resources_and_partner_acceptance_assigns(app):
    app.config['CANONICAL_ORIGIN'] = 'https://example.test'
    with app.app_context():
        booking = confirmed_paid_booking()
        partner, vehicle, movers = add_partner_and_resources(booking)

        dispatch = start_dispatch(booking.id)

        offer = DispatchOffer.query.filter_by(dispatch_request_id=dispatch.id).one()
        assert dispatch.status == 'OFFERED'
        assert offer.partner_id == partner.id
        assert offer.partner_vehicle_id == vehicle.id
        assert offer.email_status == 'SENT'
        assert len(offer.reservations) == 1 + len(movers)
        assert all(reservation.status == 'HELD' for reservation in offer.reservations)

        response = respond_to_offer(_serializer_token(offer.id), 'accept')

        db.session.refresh(dispatch)
        db.session.refresh(booking)
        assignment = booking.assignments[0]
        assert response.status == 'ACCEPTED'
        assert dispatch.status == 'RESERVED'
        assert booking.status == 'Assigned'
        assert assignment.partner_id == partner.id
        assert assignment.partner_vehicle_id == vehicle.id
        assert {assignment.mover_1, assignment.mover_2} == {mover.name for mover in movers}
        assert all(row.status == 'RESERVED' for row in offer.reservations)


def test_missing_partner_service_configuration_routes_to_manual_review(app):
    with app.app_context():
        booking = confirmed_paid_booking()
        add_partner_and_resources(booking, service_types=[])

        dispatch = start_dispatch(booking.id)

        assert dispatch.status == 'MANUAL_REVIEW'
        assert 'not explicitly enabled residential service' in dispatch.last_error
        assert DispatchOffer.query.filter_by(dispatch_request_id=dispatch.id).count() == 0
        assert ResourceReservation.query.filter_by(dispatch_request_id=dispatch.id).count() == 0


def test_active_reservations_cannot_double_book_vehicle_or_mover(app):
    with app.app_context():
        booking = confirmed_paid_booking()
        partner, vehicle, movers = add_partner_and_resources(booking)
        dispatch = start_dispatch(booking.id)
        offer = DispatchOffer.query.filter_by(dispatch_request_id=dispatch.id).one()
        mover = movers[0]

        db.session.add(ResourceReservation(
            dispatch_request_id=dispatch.id, offer_id=offer.id, booking_id=booking.id,
            partner_vehicle_id=vehicle.id, reservation_date=booking.move_date,
            status='HELD', expires_at=offer.expires_at,
        ))
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
