from __future__ import annotations

from ..extensions import db
from ..models import VehicleType

DEFAULT_VEHICLE_TYPES = (
    ('Small Van', 'small-van', 'Compact van for single-room moves and a few large items.', 'Panel van', 5, 700, 2.5, 1.6, 1.3, 'van', 'Single rooms, boxes, and a few pieces of furniture', 18000, 500, 25000, 1),
    ('Pickup', 'pickup', 'Pickup for small furniture-heavy moves.', 'Pickup', 8, 1000, 3.0, 1.8, 1.4, 'pickup', 'Small moves and limited household items', 22000, 600, 30000, 2),
    ('Hiace / Large Van', 'hiace-large-van', 'Enclosed van for studios, rooms, and small offices.', 'Large van', 12, 1500, 4.0, 1.8, 1.8, 'large-van', 'Studio apartments and small office moves', 28000, 700, 38000, 3),
    ('Canter', 'canter', 'Medium-duty vehicle for household and office relocations.', 'Canter', 22, 3000, 4.3, 2.0, 2.1, 'canter', '1-3 bedroom homes and medium office moves', 40000, 800, 55000, 4),
    ('Large Canter / Box Truck', 'large-canter-box-truck', 'Enclosed high-capacity vehicle for larger household moves.', 'Box truck', 30, 4500, 5.2, 2.2, 2.3, 'box-truck', '3-4 bedroom homes and larger moves', 55000, 950, 70000, 5),
    ('Large Truck', 'large-truck', 'High-capacity vehicle for large homes and commercial moves.', 'Heavy truck', 45, 7000, 6.5, 2.4, 2.5, 'large-truck', 'Large houses, offices, and commercial moves', 80000, 1200, 100000, 6),
)


def ensure_active_vehicle_catalogue() -> None:
    if VehicleType.query.filter_by(is_active=True).first():
        return

    existing_by_slug = {vehicle.slug: vehicle for vehicle in VehicleType.query.all()}
    for spec in DEFAULT_VEHICLE_TYPES:
        (name, slug, description, body_type, capacity, payload, length, width, height,
         icon, best_for, base_price, per_km, minimum, order) = spec
        vehicle = existing_by_slug.get(slug)
        if vehicle is None:
            vehicle = VehicleType(slug=slug)
            db.session.add(vehicle)
        vehicle.name = name
        vehicle.description = description
        vehicle.body_type = body_type
        vehicle.capacity_m3 = capacity
        vehicle.max_payload_kg = payload
        vehicle.length_m = length
        vehicle.width_m = width
        vehicle.height_m = height
        vehicle.icon = icon
        vehicle.best_for = best_for
        vehicle.base_price = base_price
        vehicle.price_per_km = per_km
        vehicle.minimum_price = minimum
        vehicle.sort_order = order
        vehicle.is_active = True
    db.session.commit()
