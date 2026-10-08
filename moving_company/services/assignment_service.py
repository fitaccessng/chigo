from datetime import date

from ..models import Booking, BookingAssignment


def check_assignment_conflict(booking_id, truck_id=None, move_date=None, driver_name=None, mover_names=None, cleaner_name=None):
    booking = Booking.query.get(booking_id)
    target_date = move_date or (booking.move_date if booking else None)
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    if target_date is None:
        return True

    assignments = BookingAssignment.query.join(Booking).filter(
        Booking.move_date == target_date,
        Booking.id != booking_id,
    ).all()
    requested_movers = {name.strip().casefold() for name in (mover_names or []) if name and name.strip()}
    requested_driver = driver_name.strip().casefold() if driver_name and driver_name.strip() else None
    requested_cleaner = cleaner_name.strip().casefold() if cleaner_name and cleaner_name.strip() else None

    for assignment in assignments:
        if truck_id is not None and assignment.truck_id == truck_id:
            return True
        if requested_driver and assignment.driver_name and assignment.driver_name.strip().casefold() == requested_driver:
            return True
        if requested_cleaner and assignment.cleaner and assignment.cleaner.strip().casefold() == requested_cleaner:
            return True
        assigned_movers = {
            name.strip().casefold()
            for name in (assignment.mover_1, assignment.mover_2, assignment.mover_3, assignment.mover_4)
            if name and name.strip()
        }
        if requested_movers & assigned_movers:
            return True
    return False


def save_assignment(booking, truck_id=None, driver_name=None, mover_names=None, cleaner=None):
    mover_names = mover_names or []
    if check_assignment_conflict(booking.id, truck_id, booking.move_date, driver_name, mover_names, cleaner):
        raise ValueError('The selected truck or crew is already assigned on this move date.')

    assignment = BookingAssignment.query.filter_by(booking_id=booking.id).first()
    if assignment is None:
        assignment = BookingAssignment(booking_id=booking.id)
    assignment.truck_id = truck_id or None
    assignment.driver_name = driver_name or None
    assignment.mover_1 = mover_names[0] if len(mover_names) > 0 else None
    assignment.mover_2 = mover_names[1] if len(mover_names) > 1 else None
    assignment.mover_3 = mover_names[2] if len(mover_names) > 2 else None
    assignment.mover_4 = mover_names[3] if len(mover_names) > 3 else None
    assignment.cleaner = cleaner or None
    assignment.conflict_message = None
    booking.status = 'Assigned'
    return assignment
