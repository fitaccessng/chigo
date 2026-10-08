from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete

from ..extensions import db
from ..models import Mover, Team, Truck, TruckPartner

DEMO_PARTNERS = (
    ('Capital Move Fleet', '0800 000 0001', 'partner01@example.test', '1.5 Ton Truck', '8–12 m³', 'Available', 78000, 'Gwarinpa, Wuse, Maitama, Asokoro, Jabi'),
    ('Abuja Haulage Partners', '0800 000 0002', 'partner02@example.test', '3 Ton Box Truck', '15–20 m³', 'Assigned', 92000, 'Wuse II, Utako, Kubwa, Lugbe, Lokogoma'),
    ('Metro Relocation Fleet', '0800 000 0003', 'partner03@example.test', '5 Ton Truck', '25–35 m³', 'Available', 118000, 'Maitama, Asokoro, Apo, Karsana, Life Camp'),
    ('FCT Moving Transport', '0800 000 0004', 'partner04@example.test', '7.5 Ton Truck', '40–50 m³', 'Unavailable', 145000, 'Gwarinpa, Jabi, Airport Road, Dei-Dei, Kubwa'),
    ('NorthPoint Truck Services', '0800 000 0005', 'partner05@example.test', 'Cargo Van', '6–8 m³', 'Available', 68000, 'Wuse, Utako, Jabi, Karsana, Galadimawa'),
    ('CityMove Vehicle Partners', '0800 000 0006', 'partner06@example.test', 'Pickup', '4–6 m³', 'Assigned', 61000, 'Lugbe, Lokogoma, Apo, Gwarinpa, Maitama'),
    ('Capital Logistics Fleet', '0800 000 0007', 'partner07@example.test', '1.5 Ton Truck', '8–12 m³', 'Available', 84000, 'Wuse, Kubwa, Dei-Dei, Airport Road, Life Camp'),
)

DEMO_VEHICLES = (
    ('CHIGO-Demo-001', '1.5 Ton Truck', '8–12 m³', 9500, '4.8 × 2.1 × 2.0 m', 'Gwarinpa', 'Available', 78000, 'pickup-demo.svg'),
    ('CHIGO-Demo-002', '3 Ton Box Truck', '15–20 m³', 18000, '5.2 × 2.2 × 2.3 m', 'Wuse II', 'Assigned', 92000, 'cargo-van-demo.svg'),
    ('CHIGO-Demo-003', '5 Ton Truck', '25–35 m³', 30000, '6.5 × 2.4 × 2.5 m', 'Maitama', 'In Use', 118000, 'truck-demo.svg'),
    ('CHIGO-Demo-004', '7.5 Ton Truck', '40–50 m³', 45000, '7.4 × 2.5 × 2.8 m', 'Jabi', 'Available', 145000, 'truck-demo.svg'),
    ('CHIGO-Demo-005', 'Cargo Van', '6–8 m³', 7500, '4.2 × 2.0 × 2.0 m', 'Utako', 'Maintenance', 68000, 'cargo-van-demo.svg'),
    ('CHIGO-Demo-006', 'Pickup', '4–6 m³', 3200, '3.8 × 1.8 × 1.5 m', 'Kubwa', 'Unavailable', 61000, 'pickup-demo.svg'),
    ('CHIGO-Demo-007', '1.5 Ton Truck', '8–12 m³', 10500, '4.8 × 2.1 × 2.0 m', 'Lugbe', 'Available', 84000, 'truck-demo.svg'),
    ('CHIGO-Demo-008', '3 Ton Box Truck', '15–20 m³', 19000, '5.2 × 2.2 × 2.3 m', 'Lokogoma', 'Assigned', 92000, 'cargo-van-demo.svg'),
    ('CHIGO-Demo-009', '5 Ton Truck', '25–35 m³', 32000, '6.5 × 2.4 × 2.5 m', 'Apo', 'In Use', 118000, 'truck-demo.svg'),
    ('CHIGO-Demo-010', 'Cargo Van', '6–8 m³', 8000, '4.2 × 2.0 × 2.0 m', 'Karsana', 'Available', 68000, 'cargo-van-demo.svg'),
)

DEMO_TEAMS = (
    ('Team Alpha', 'Demo Team Leader 01', 'Available', 'Gwarinpa', 24),
    ('Team Bravo', 'Demo Team Leader 02', 'Assigned', 'Wuse II', 31),
    ('Team Charlie', 'Demo Team Leader 03', 'On Job', 'Maitama', 19),
    ('Team Delta', 'Demo Team Leader 04', 'Available', 'Jabi', 27),
)

DEMO_MOVERS = (
    ('Daniel Okafor', '0800 000 0101', 'Packing, Furniture Handling', '2 years', 'Available', 4.8, 44, 'Team Alpha'),
    ('Samuel Ade', '0800 000 0102', 'Loading, Unloading, Heavy Furniture', '3 years', 'Available', 4.6, 38, 'Team Alpha'),
    ('Ibrahim Bello', '0800 000 0103', 'Office Relocation, Appliance Handling', '4 years', 'Assigned', 4.9, 51, 'Team Alpha'),
    ('Chinedu Peter', '0800 000 0104', 'Packing, Furniture Assembly', '2 years', 'Available', 4.7, 31, 'Team Alpha'),
    ('Emeka James', '0800 000 0105', 'Unloading, Fragile Items', '5 years', 'Available', 4.8, 46, 'Team Bravo'),
    ('Yusuf Musa', '0800 000 0106', 'Heavy Furniture, Loading', '7 years', 'Assigned', 4.9, 58, 'Team Bravo'),
    ('David John', '0800 000 0107', 'Packing, Office Relocation', '3 years', 'On Job', 4.5, 43, 'Team Bravo'),
    ('Michael Paul', '0800 000 0108', 'Furniture Handling, Assembly', '4 years', 'Available', 4.7, 35, 'Team Charlie'),
    ('Victor Obi', '0800 000 0109', 'Loading, Unloading, Appliance Handling', '6 years', 'On Job', 4.9, 62, 'Team Charlie'),
    ('Joseph Samuel', '0800 000 0110', 'Packing, Fragile Items', '2 years', 'Unavailable', 4.3, 28, 'Team Charlie'),
    ('Aisha Rahman', '0800 000 0111', 'Furniture Assembly, Unloading', '5 years', 'Available', 4.8, 40, 'Team Delta'),
    ('Tunde Balogun', '0800 000 0112', 'Heavy Furniture, Loading', '8 years', 'Available', 4.9, 67, 'Team Delta'),
    ('Oluwaseun Adebayo', '0800 000 0113', 'Office Relocation, Packing', '3 years', 'On Job', 4.6, 36, 'Team Delta'),
    ('Nneka Okafor', '0800 000 0114', 'Fragile Items, Furniture Handling', '4 years', 'Unavailable', 4.5, 34, 'Team Delta'),
)


@dataclass(frozen=True)
class DemoSeedReport:
    partners: int
    vehicles: int
    movers: int
    teams: int


def _demo_partner(name, phone, email, vehicle_type, capacity, status, rate, areas):
    return TruckPartner(
        name=name, company=name, phone=phone, email=email,
        vehicle_type=vehicle_type, capacity=capacity, status=status, rate=rate,
        operating_areas=areas, is_demo=True, seed_source='demo', notes='Development/demo record; not a real partner.',
    )


def seed_demo_data():
    with db.session.begin_nested():
        existing_partner = TruckPartner.query.filter_by(seed_source='demo').first()
        if existing_partner:
            return _seed_report()

        partner_by_name = {}
        for partner_data in DEMO_PARTNERS:
            partner = _demo_partner(*partner_data)
            db.session.add(partner)
            partner_by_name[partner.name] = partner

        teams = {}
        for team_name, leader, availability, assignment, jobs in DEMO_TEAMS:
            team = Team(
                name=team_name, team_leader=leader, availability=availability,
                current_assignment=assignment, jobs_completed=jobs,
                is_demo=True, seed_source='demo',
            )
            db.session.add(team)
            teams[team_name] = team

        mover_rows = []
        for name, phone, skills, experience, status, rating, jobs, team_name in DEMO_MOVERS:
            mover_rows.append(Mover(
                name=name, phone=phone, skills=skills, experience=experience,
                status=status, rating=rating, jobs_completed=jobs,
                current_assignment=f'[{teams[team_name].name}] {teams[team_name].current_assignment}',
                team=teams[team_name], is_demo=True, seed_source='demo',
            ))
        db.session.add_all(mover_rows)

        for index, vehicle_data in enumerate(DEMO_VEHICLES, start=1):
            registration, vehicle_type, capacity, payload, dimensions, location, status, rate, image = vehicle_data
            partner_name = DEMO_PARTNERS[(index - 1) % len(DEMO_PARTNERS)][0]
            db.session.add(Truck(
                partner=partner_by_name[partner_name], registration_number=registration,
                vehicle_type=vehicle_type, capacity=capacity, status=status,
                current_location=location, rate=rate, payload_capacity_kg=payload,
                dimensions_m=dimensions, driver_required=True, fuel_assumption='Standard diesel',
                image=image, is_demo=True, seed_source='demo',
            ))

    return _seed_report()


def _seed_report():
    return {
        'partners': TruckPartner.query.filter_by(is_demo=True).count(),
        'vehicles': Truck.query.filter_by(is_demo=True).count(),
        'movers': Mover.query.filter_by(is_demo=True).count(),
        'teams': Team.query.filter_by(is_demo=True).count(),
    }


def clear_demo_data():
    report = _seed_report()
    with db.session.begin_nested():
        db.session.execute(delete(Truck).where(Truck.is_demo.is_(True)))
        db.session.execute(delete(Mover).where(Mover.is_demo.is_(True)))
        db.session.execute(delete(Team).where(Team.is_demo.is_(True)))
        db.session.execute(delete(TruckPartner).where(TruckPartner.is_demo.is_(True)))
    return report
