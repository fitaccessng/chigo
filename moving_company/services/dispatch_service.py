from __future__ import annotations

from datetime import datetime, time, timedelta
from hashlib import sha256
import logging
import re

from flask import current_app, has_request_context, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    Booking, BookingAssignment, DispatchEvent, DispatchOffer, DispatchRequest,
    Mover, Notification, PartnerVehicle, ResourceReservation, TruckPartner,
)
from .booking_engine_service import inventory_is_confirmed, inventory_requirements, record_event
from .transactional_email_service import send_branded_email

logger = logging.getLogger(__name__)
ACTIVE_RESERVATION_STATES = ('HELD', 'RESERVED')
SUPPORTED_DISPATCH_SERVICES = {'residential', 'commercial'}


def _now() -> datetime:
    return datetime.utcnow()


def _serializer() -> URLSafeSerializer:
    secret = current_app.config.get('SECRET_KEY')
    if not secret or (not current_app.testing and secret == 'dev-secret-key'):
        raise ValueError('Dispatch signing is not configured with a production SECRET_KEY.')
    return URLSafeSerializer(secret, salt='chigo-dispatch-offer-v1')


def _coverage_tokens(value: str | None) -> set[str]:
    return {token.strip().casefold() for token in re.split(r'[,;|]', value or '') if token.strip()}


def _location_tokens(booking: Booking, place: str) -> set[str]:
    fields = ('area_council', 'district', 'neighborhood', 'area', 'city', 'state')
    return {
        str(value).strip().casefold()
        for field in fields
        if (value := getattr(booking, f'{place}_{field}', None))
    }


def _held_resource_ids(reservation_date, *, offer_id=None, booking_id=None):
    query = ResourceReservation.query.filter(
        ResourceReservation.reservation_date == reservation_date,
        ResourceReservation.status.in_(ACTIVE_RESERVATION_STATES),
        or_(ResourceReservation.status == 'RESERVED', ResourceReservation.expires_at > _now()),
    )
    if offer_id is not None:
        query = query.filter(ResourceReservation.offer_id != offer_id)
    if booking_id is not None:
        query = query.filter(ResourceReservation.booking_id != booking_id)
    return query.all()


def _active_assignment_names(move_date, booking_id):
    assignments = BookingAssignment.query.join(Booking).filter(
        Booking.move_date == move_date,
        Booking.id != booking_id,
        Booking.status.notin_(('Cancelled', 'Completed')),
    ).all()
    return {
        str(name).strip().casefold()
        for assignment in assignments
        for name in (assignment.mover_1, assignment.mover_2, assignment.mover_3, assignment.mover_4)
        if name and str(name).strip()
    }


def _candidate_blockers(booking: Booking, partner: TruckPartner, vehicle: PartnerVehicle,
                        *, offer_id=None, fixed_mover_ids=None) -> tuple[list[str], list[Mover], dict]:
    blockers = []
    if booking.primary_service not in SUPPORTED_DISPATCH_SERVICES:
        blockers.append('This dispatch workflow currently supports only residential and commercial moving.')
    if booking.quote_status != 'Accepted' or booking.payment_status != 'Paid' or booking.workflow_state != 'CONFIRMED':
        blockers.append('Verified deposit payment and an accepted quote are required before dispatch.')
    if not inventory_is_confirmed(booking):
        blockers.append('Customer-confirmed inventory is required before dispatch.')
    if not booking.move_date or not booking.preferred_time:
        blockers.append('The requested move date and preferred time are incomplete.')
    if not booking.estimated_duration_minutes or booking.estimated_duration_minutes <= 0:
        blockers.append('A validated move-duration estimate is unavailable.')
    if booking.route_status != 'CALCULATED' or not booking.distance_km or booking.distance_km <= 0:
        blockers.append('A calculated road route is required; route availability is not assumed.')
    if booking.service_area_status != 'SERVICEABLE':
        blockers.append('The booking service-area result is not confirmed as serviceable.')

    property_answers = ((booking.workflow_data or {}).get('property') or {}).get('service_answers') or {}
    location_access = (booking.workflow_data or {}).get('location_access') or {}
    if any(property_answers.get(key) == 'yes' for key in ('restricted_vehicle_access', 'long_carry_distance')):
        blockers.append('Access restrictions require manual vehicle/access suitability review.')
    if any(getattr(booking, f'{place}_narrow_road', False) or getattr(booking, f'{place}_inside_estate', False)
           for place in ('pickup', 'destination')):
        blockers.append('Narrow-road or estate access capability is not recorded for partner vehicles.')
    if any(getattr(booking, f'{place}_floor', None) not in (None, '', 'Ground')
           and location_access.get(f'{place}_lift_available') is None
           for place in ('pickup', 'destination')):
        blockers.append('Lift availability is unresolved for an above-ground property.')

    if partner.status != 'Available':
        blockers.append('Partner is not currently marked available.')
    if not partner.email:
        blockers.append('Partner email is not configured for a dispatch offer.')
    if booking.primary_service not in (partner.service_types or []):
        blockers.append('Partner has not explicitly enabled this service type.')
    if not vehicle.is_active or vehicle.status != 'Available':
        blockers.append('Partner vehicle is not active and available.')
    if booking.recommended_vehicle_type_id != vehicle.vehicle_type_id:
        blockers.append('Partner vehicle does not match the vehicle type used in the accepted quote.')

    partner_area_tokens = _coverage_tokens(partner.operating_areas)
    vehicle_area_tokens = _coverage_tokens(vehicle.operating_areas)
    for place in ('pickup', 'destination'):
        location_tokens = _location_tokens(booking, place)
        if not location_tokens:
            blockers.append(f'{place.title()} coverage details are missing.')
        elif not partner_area_tokens.intersection(location_tokens):
            blockers.append(f'Partner coverage is not verified for the {place} location.')
        if vehicle_area_tokens and location_tokens and not vehicle_area_tokens.intersection(location_tokens):
            blockers.append(f'Partner vehicle coverage is not verified for the {place} location.')

    if not vehicle.actual_capacity_m3 or not vehicle.max_payload_kg:
        blockers.append('Measured partner vehicle volume and payload capacity are required.')

    selected_services = (booking.workflow_data or {}).get('services') or {}
    if any(selected_services.get(key) not in (None, '', 'none', 'no')
           for key in ('packing', 'assembly', 'disassembly', 'cleaning', 'home_decor', 'storage')):
        blockers.append('Selected add-on services require separately qualified partner/crew data.')

    requirements = inventory_requirements(booking)
    if requirements.get('review_reasons'):
        blockers.extend(requirements['review_reasons'])
    if requirements.get('special_items'):
        blockers.append('Specialist crew qualifications are not represented in structured skills data.')
    if vehicle.actual_capacity_m3 and requirements['volume_m3'] > vehicle.actual_capacity_m3:
        blockers.append('A single partner vehicle cannot carry the estimated inventory volume.')
    if vehicle.max_payload_kg and requirements['weight_kg'] > vehicle.max_payload_kg:
        blockers.append('A single partner vehicle cannot carry the estimated inventory payload.')

    selected_vehicle_count = ((booking.calculation_data or {}).get('vehicle') or {}).get('quantity')
    if selected_vehicle_count and int(selected_vehicle_count) > 1:
        blockers.append('Multi-vehicle dispatch is not supported by the current partner reservation records.')

    if booking.move_date:
        held = _held_resource_ids(booking.move_date, offer_id=offer_id, booking_id=booking.id)
        held_vehicle_ids = {row.partner_vehicle_id for row in held if row.partner_vehicle_id}
        held_mover_ids = {row.mover_id for row in held if row.mover_id}
        if vehicle.id in held_vehicle_ids:
            blockers.append('Partner vehicle already has an active hold or reservation that date.')
        assignments = BookingAssignment.query.join(Booking).filter(
            Booking.move_date == booking.move_date,
            Booking.id != booking.id,
            Booking.status.notin_(('Cancelled', 'Completed')),
        ).all()
        if any(assignment.partner_vehicle_id == vehicle.id for assignment in assignments):
            blockers.append('Partner vehicle is already assigned to another booking that date.')
        assigned_names = _active_assignment_names(booking.move_date, booking.id)
    else:
        held_mover_ids = set()
        assigned_names = set()

    roster = Mover.query.filter_by(status='Available').order_by(Mover.id).all()
    required_movers = int(((booking.calculation_data or {}).get('workforce') or {}).get('movers') or 0)
    if required_movers < 1:
        blockers.append('A validated crew-size calculation is unavailable.')
    eligible_movers = []
    if not blockers:
        fixed_ids = set(fixed_mover_ids) if fixed_mover_ids is not None else None
        for mover in roster:
            if fixed_ids is not None and mover.id not in fixed_ids:
                continue
            if mover.id in held_mover_ids or mover.name.casefold() in assigned_names:
                continue
            if mover.team and mover.team.availability != 'Available':
                continue
            eligible_movers.append(mover)
            if len(eligible_movers) == required_movers:
                break
        if len(eligible_movers) < required_movers:
            blockers.append('The recorded mover pool cannot cover the calculated crew size for that date.')

    snapshot = {
        'partner_id': partner.id,
        'partner_vehicle_id': vehicle.id,
        'vehicle_type_id': vehicle.vehicle_type_id,
        'capacity_m3': vehicle.actual_capacity_m3,
        'payload_kg': vehicle.max_payload_kg,
        'estimated_volume_m3': requirements.get('volume_m3'),
        'estimated_weight_kg': requirements.get('weight_kg'),
        'move_date': booking.move_date.isoformat() if booking.move_date else None,
        'preferred_time': booking.preferred_time,
        'duration_minutes': booking.estimated_duration_minutes,
        'partner_rate': float(vehicle.partner_rate or partner.rate or 0),
        'rate_configured': bool(vehicle.partner_rate or partner.rate),
        'mover_ids': [mover.id for mover in eligible_movers] if not blockers else [],
    }
    return blockers, eligible_movers, snapshot


def _eligible_candidates(booking: Booking, *, excluded_candidate_ids=None):
    excluded_candidate_ids = excluded_candidate_ids or set()
    candidates = []
    diagnostics = []
    partners = TruckPartner.query.filter_by(status='Available').order_by(TruckPartner.id).all()
    for partner in partners:
        if booking.primary_service not in (partner.service_types or []):
            diagnostics.append(f'Partner {partner.id} has not explicitly enabled {booking.primary_service} service.')
            continue
        for vehicle in sorted(partner.vehicles, key=lambda item: (item.partner_rate or partner.rate or 0, item.id)):
            identity = (partner.id, vehicle.id)
            if identity in excluded_candidate_ids:
                continue
            blockers, movers, snapshot = _candidate_blockers(booking, partner, vehicle)
            if blockers:
                diagnostics.extend(blockers)
                continue
            candidates.append((float(snapshot['partner_rate']), partner.id, vehicle.id, partner, vehicle, movers, snapshot))
    candidates.sort(key=lambda row: (not row[6]['rate_configured'], row[0], row[1], row[2]))
    return candidates, sorted(set(diagnostics))


def _event(dispatch_request, event_type, details=None, actor_user_id=None, offer=None):
    db.session.add(DispatchEvent(
        dispatch_request_id=dispatch_request.id,
        offer_id=offer.id if offer else None,
        actor_user_id=actor_user_id,
        event_type=event_type,
        details=details or {},
    ))


def _release_offer_holds(offer: DispatchOffer, reason: str):
    now = _now()
    for reservation in ResourceReservation.query.filter_by(offer_id=offer.id, status='HELD').all():
        reservation.status = 'RELEASED'
        reservation.released_at = now
        reservation.release_reason = reason[:250]


def _send_offer_email(offer: DispatchOffer, token: str):
    partner = offer.partner
    try:
        relative_link = f'/offers/{token}'
        origin = current_app.config.get('CANONICAL_ORIGIN')
        if origin:
            link = f'{origin.rstrip("/")}{relative_link}'
        elif has_request_context():
            link = f'{request.url_root.rstrip("/")}{relative_link}'
        else:
            raise RuntimeError('Set CANONICAL_ORIGIN before dispatch emails are generated outside a request.')
        booking = offer.dispatch_request.booking
        send_branded_email(
            partner.email,
            f'Chigo move request · {booking.booking_number}',
            'A moving request is available for your review.',
            'New moving request',
            [
                f"Service: {booking.primary_service.title()}.",
                f"Route: {booking.pickup_city or 'Location pending'} to {booking.destination_city or 'Location pending'}.",
                f"Requested date and time: {booking.move_date} · {booking.preferred_time}.",
                'This is an offer, not a confirmed assignment. Review the request and accept only if you can fulfill it.',
            ],
            'Review offer', link,
            metadata={'dispatch_offer_id': offer.id},
        )
        offer.email_status = 'SENT'
        offer.last_email_error = None
        _event(offer.dispatch_request, 'OFFER_EMAIL_SENT', {'partner_id': partner.id}, offer=offer)
    except Exception as error:
        offer.email_status = 'FAILED'
        offer.last_email_error = error.__class__.__name__
        logger.warning('Dispatch offer email failed offer_id=%s error=%s', offer.id, error.__class__.__name__)
        _event(offer.dispatch_request, 'OFFER_EMAIL_FAILED', {'error': error.__class__.__name__}, offer=offer)
    offer.email_attempts += 1
    db.session.commit()


def _serializer_token(offer_id: int) -> str:
    return _serializer().dumps({'offer_id': offer_id})


def token_offer(token: str) -> DispatchOffer | None:
    try:
        payload = _serializer().loads(token)
        offer_id = int(payload['offer_id'])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None
    digest = sha256(token.encode()).hexdigest()
    offer = db.session.get(DispatchOffer, offer_id)
    return offer if offer and offer.token_hash == digest else None


def _create_next_offer(dispatch_request: DispatchRequest, actor_user_id=None, excluded_candidate_ids=None):
    booking = db.session.execute(
        select(Booking).where(Booking.id == dispatch_request.booking_id).with_for_update()
    ).scalar_one()
    previous_offers = DispatchOffer.query.filter_by(dispatch_request_id=dispatch_request.id).all()
    excluded = excluded_candidate_ids if excluded_candidate_ids is not None else {
        (offer.partner_id, offer.partner_vehicle_id) for offer in previous_offers
    }
    candidates, diagnostics = _eligible_candidates(booking, excluded_candidate_ids=excluded)
    if not candidates:
        dispatch_request.status = 'MANUAL_REVIEW'
        dispatch_request.last_error = '; '.join(diagnostics[:5])[:500] or 'No configured, eligible partner and vehicle combination is available.'
        dispatch_request.updated_at = _now()
        _event(dispatch_request, 'MANUAL_REVIEW_REQUIRED', {'reasons': diagnostics[:20]}, actor_user_id)
        db.session.commit()
        return None

    _, _, _, partner, vehicle, movers, snapshot = candidates[0]
    expires_at = _now() + timedelta(minutes=int(current_app.config.get('DISPATCH_OFFER_TTL_MINUTES', 20)))
    try:
        with db.session.begin_nested():
            offer = DispatchOffer(
                dispatch_request_id=dispatch_request.id, partner_id=partner.id,
                partner_vehicle_id=vehicle.id, token_hash='pending',
                status='OFFERED', candidate_snapshot=snapshot, expires_at=expires_at,
            )
            db.session.add(offer)
            db.session.flush()
            token = _serializer_token(offer.id)
            offer.token_hash = sha256(token.encode()).hexdigest()
            db.session.add(ResourceReservation(
                dispatch_request_id=dispatch_request.id, offer_id=offer.id, booking_id=booking.id,
                partner_vehicle_id=vehicle.id, reservation_date=booking.move_date,
                status='HELD', expires_at=expires_at,
            ))
            for mover in movers:
                db.session.add(ResourceReservation(
                    dispatch_request_id=dispatch_request.id, offer_id=offer.id, booking_id=booking.id,
                    mover_id=mover.id, reservation_date=booking.move_date,
                    status='HELD', expires_at=expires_at,
                ))
            dispatch_request.attempts += 1
            dispatch_request.status = 'OFFERED'
            dispatch_request.last_error = None
            dispatch_request.updated_at = _now()
            _event(dispatch_request, 'OFFER_CREATED', {
                'partner_id': partner.id, 'partner_vehicle_id': vehicle.id,
                'reserved_mover_count': len(movers), 'expires_at': expires_at.isoformat(),
            }, actor_user_id, offer)
    except IntegrityError:
        db.session.rollback()
        dispatch_request = db.session.get(DispatchRequest, dispatch_request.id)
        dispatch_request.status = 'REASSIGNMENT_REQUIRED'
        dispatch_request.last_error = 'A resource was reserved concurrently; rerun dispatch after availability is refreshed.'
        _event(dispatch_request, 'RESERVATION_CONFLICT', {'reason': 'unique active resource hold conflict'}, actor_user_id)
        db.session.commit()
        return None

    db.session.commit()
    offer = db.session.get(DispatchOffer, offer.id)
    _send_offer_email(offer, token)
    return offer


def start_dispatch(booking_id: int, actor_user_id=None, *, retry_manual=False):
    booking = db.session.execute(select(Booking).where(Booking.id == booking_id).with_for_update()).scalar_one_or_none()
    if booking is None:
        raise LookupError('Booking was not found.')
    if booking.primary_service not in SUPPORTED_DISPATCH_SERVICES:
        raise ValueError('Partner fleet dispatch is configured only for residential and commercial moves.')
    if booking.quote_status != 'Accepted' or booking.payment_status != 'Paid' or booking.workflow_state != 'CONFIRMED':
        raise ValueError('Verified payment, an accepted quote, and confirmed booking status are required before dispatch.')
    if not inventory_is_confirmed(booking):
        raise ValueError('Customer-confirmed inventory is required before dispatch.')
    dispatch_request = DispatchRequest.query.filter_by(booking_id=booking.id).with_for_update().first()
    if dispatch_request and dispatch_request.status == 'RESERVED':
        return dispatch_request
    if dispatch_request:
        current_offer = DispatchOffer.query.filter_by(
            dispatch_request_id=dispatch_request.id, status='OFFERED',
        ).filter(DispatchOffer.expires_at > _now()).first()
        if current_offer:
            if current_offer.email_status != 'SENT':
                _send_offer_email(current_offer, _serializer_token(current_offer.id))
            return dispatch_request
    else:
        dispatch_request = DispatchRequest(booking_id=booking.id, status='PENDING_DISPATCH')
        db.session.add(dispatch_request)
        db.session.flush()

    _event(dispatch_request, 'DISPATCH_STARTED', {'booking_id': booking.id, 'retry_manual': retry_manual}, actor_user_id)
    db.session.commit()
    _create_next_offer(
        dispatch_request, actor_user_id,
        excluded_candidate_ids=set() if retry_manual else None,
    )
    return db.session.get(DispatchRequest, dispatch_request.id)


def expire_dispatch_offers(now=None):
    now = now or _now()
    expired = DispatchOffer.query.filter_by(status='OFFERED').filter(DispatchOffer.expires_at <= now).all()
    affected_ids = set()
    for offer in expired:
        offer.status = 'EXPIRED'
        offer.responded_at = now
        _release_offer_holds(offer, 'Offer expired.')
        dispatch_request = offer.dispatch_request
        dispatch_request.status = 'REASSIGNMENT_REQUIRED'
        dispatch_request.updated_at = now
        affected_ids.add(dispatch_request.id)
        _event(dispatch_request, 'OFFER_EXPIRED', {'partner_id': offer.partner_id}, offer=offer)
    db.session.commit()
    for dispatch_id in affected_ids:
        dispatch_request = db.session.get(DispatchRequest, dispatch_id)
        if dispatch_request:
            excluded = {
                (row.partner_id, row.partner_vehicle_id)
                for row in DispatchOffer.query.filter_by(dispatch_request_id=dispatch_id).all()
            }
            _create_next_offer(dispatch_request, excluded_candidate_ids=excluded)
    return len(expired)


def respond_to_offer(token: str, decision: str, reason: str = '') -> DispatchOffer:
    offer = token_offer(token)
    if offer is None:
        raise LookupError('This dispatch offer is not valid.')
    offer = db.session.execute(select(DispatchOffer).where(DispatchOffer.id == offer.id).with_for_update()).scalar_one()
    dispatch_request = db.session.execute(
        select(DispatchRequest).where(DispatchRequest.id == offer.dispatch_request_id).with_for_update()
    ).scalar_one()
    now = _now()
    if offer.status == 'ACCEPTED' and decision == 'accept':
        return offer
    if offer.status != 'OFFERED':
        raise ValueError('This dispatch offer is no longer available.')
    if offer.expires_at <= now:
        offer.status = 'EXPIRED'
        offer.responded_at = now
        _release_offer_holds(offer, 'Offer expired before partner response.')
        dispatch_request.status = 'REASSIGNMENT_REQUIRED'
        _event(dispatch_request, 'OFFER_EXPIRED', {'partner_id': offer.partner_id}, offer=offer)
        db.session.commit()
        excluded = {(row.partner_id, row.partner_vehicle_id) for row in DispatchOffer.query.filter_by(dispatch_request_id=dispatch_request.id).all()}
        _create_next_offer(db.session.get(DispatchRequest, dispatch_request.id), excluded_candidate_ids=excluded)
        raise ValueError('This dispatch offer has expired.')

    if decision == 'reject':
        offer.status = 'REJECTED'
        offer.rejection_reason = reason.strip()[:500] or None
        offer.responded_at = now
        _release_offer_holds(offer, 'Partner declined the offer.')
        dispatch_request.status = 'REASSIGNMENT_REQUIRED'
        dispatch_request.updated_at = now
        _event(dispatch_request, 'OFFER_REJECTED', {'reason': offer.rejection_reason}, offer=offer)
        db.session.commit()
        excluded = {(row.partner_id, row.partner_vehicle_id) for row in DispatchOffer.query.filter_by(dispatch_request_id=dispatch_request.id).all()}
        _create_next_offer(db.session.get(DispatchRequest, dispatch_request.id), excluded_candidate_ids=excluded)
        return offer
    if decision != 'accept':
        raise ValueError('Choose accept or reject.')

    booking = db.session.execute(select(Booking).where(Booking.id == dispatch_request.booking_id).with_for_update()).scalar_one()
    partner = db.session.get(TruckPartner, offer.partner_id)
    vehicle = db.session.get(PartnerVehicle, offer.partner_vehicle_id)
    blockers, movers, snapshot = _candidate_blockers(
        booking, partner, vehicle, offer_id=offer.id,
        fixed_mover_ids=offer.candidate_snapshot.get('mover_ids', []),
    )
    held = ResourceReservation.query.filter_by(offer_id=offer.id, status='HELD').with_for_update().all()
    held_mover_ids = {row.mover_id for row in held if row.mover_id}
    required_mover_ids = set(offer.candidate_snapshot.get('mover_ids', []))
    if not held or held_mover_ids != required_mover_ids or not any(row.partner_vehicle_id == vehicle.id for row in held):
        blockers.append('One or more temporary resource holds are missing or no longer valid.')
    if blockers:
        offer.status = 'RESERVATION_FAILED'
        offer.responded_at = now
        _release_offer_holds(offer, 'Eligibility changed before acceptance.')
        dispatch_request.status = 'REASSIGNMENT_REQUIRED'
        dispatch_request.last_error = '; '.join(blockers[:5])[:500]
        _event(dispatch_request, 'ACCEPTANCE_REVALIDATION_FAILED', {'reasons': blockers[:20]}, offer=offer)
        db.session.commit()
        excluded = {(row.partner_id, row.partner_vehicle_id) for row in DispatchOffer.query.filter_by(dispatch_request_id=dispatch_request.id).all()}
        _create_next_offer(db.session.get(DispatchRequest, dispatch_request.id), excluded_candidate_ids=excluded)
        raise ValueError('Eligibility or reservation changed. Operations will review or reassign this booking.')

    reservation_end = datetime.combine(booking.move_date + timedelta(days=1), time.min)
    for reservation in held:
        reservation.status = 'RESERVED'
        reservation.expires_at = reservation_end
    offer.status = 'ACCEPTED'
    offer.responded_at = now
    dispatch_request.status = 'RESERVED'
    dispatch_request.last_error = None
    dispatch_request.updated_at = now

    assignment = BookingAssignment.query.filter_by(booking_id=booking.id).first()
    if assignment is None:
        assignment = BookingAssignment(booking_id=booking.id)
        db.session.add(assignment)
    assignment.partner_id = partner.id
    assignment.partner_vehicle_id = vehicle.id
    assignment.truck_id = None
    for index in range(1, 5):
        mover_id = offer.candidate_snapshot['mover_ids'][index - 1] if index <= len(offer.candidate_snapshot['mover_ids']) else None
        mover = db.session.get(Mover, mover_id) if mover_id else None
        setattr(assignment, f'mover_{index}', mover.name if mover else None)
    booking.assigned_partner_vehicle_id = vehicle.id
    booking.assigned_vehicle_type_id = vehicle.vehicle_type_id
    booking.status = 'Assigned'
    record_event(booking, 'dispatch.reserved', new={
        'partner_id': partner.id, 'partner_vehicle_id': vehicle.id,
        'mover_ids': offer.candidate_snapshot['mover_ids'],
    })
    if booking.customer_id:
        db.session.add(Notification(
            user_id=booking.customer_id,
            booking_id=booking.id,
            message='Your paid booking now has a partner vehicle and crew reservation.',
        ))
    _event(dispatch_request, 'RESERVATION_CONFIRMED', {
        'partner_id': partner.id, 'partner_vehicle_id': vehicle.id,
        'mover_ids': offer.candidate_snapshot['mover_ids'],
    }, offer=offer)
    db.session.commit()
    return offer


def release_booking_reservations(booking_id: int, reason: str, *, commit=True):
    now = _now()
    reservations = ResourceReservation.query.filter(
        ResourceReservation.booking_id == booking_id,
        ResourceReservation.status.in_(ACTIVE_RESERVATION_STATES),
    ).all()
    for reservation in reservations:
        reservation.status = 'RELEASED'
        reservation.released_at = now
        reservation.release_reason = reason[:250]
    dispatch_request = DispatchRequest.query.filter_by(booking_id=booking_id).first()
    if dispatch_request and dispatch_request.status != 'CANCELLED':
        dispatch_request.status = 'CANCELLED'
        dispatch_request.updated_at = now
        _event(dispatch_request, 'RESERVATIONS_RELEASED', {'reason': reason[:250]})
    if commit:
        db.session.commit()


def retry_failed_dispatch_notifications():
    failed = DispatchOffer.query.filter_by(status='OFFERED', email_status='FAILED').all()
    for offer in failed:
        _send_offer_email(offer, _serializer_token(offer.id))
    return len(failed)
