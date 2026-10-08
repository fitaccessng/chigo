from click.testing import CliRunner

from moving_company import create_app
from moving_company.extensions import db
from moving_company.models import Mover, Team, Truck, TruckPartner
from moving_company.services.demo_seed import clear_demo_data, seed_demo_data


def test_seed_demo_data_is_idempotent_and_preserves_real_records():
    app = create_app(testing=True)
    with app.app_context():
        real_partner = TruckPartner(
            name='Real Partner', company='Real Company', phone='+2348000000000',
            vehicle_type='Canter', capacity='Medium', status='Available', rate=50000,
        )
        real_vehicle = Truck(
            partner_id=real_partner.id, registration_number='REAL-VEHICLE',
            vehicle_type='Canter', capacity='Medium', status='Available',
            current_location='Gwarinpa', rate=60000,
        )
        real_mover = Mover(name='Real Mover', phone='+2348000000001', status='Available')
        db.session.add_all([real_partner, real_vehicle, real_mover])
        db.session.commit()

        first = seed_demo_data()
        second = seed_demo_data()

        assert first == {'partners': 7, 'vehicles': 10, 'movers': 14, 'teams': 4}
        assert second == first
        assert TruckPartner.query.filter_by(is_demo=True).count() == 7
        assert Truck.query.filter_by(is_demo=True).count() == 10
        assert Mover.query.filter_by(is_demo=True).count() == 14
        assert Team.query.filter_by(is_demo=True).count() == 4
        assert TruckPartner.query.filter_by(company='Real Company').count() == 1
        assert Truck.query.filter_by(registration_number='REAL-VEHICLE').count() == 1
        assert Mover.query.filter_by(name='Real Mover').count() == 1
        assert Truck.query.filter_by(is_demo=True).filter(Truck.image.isnot(None)).count() == 10
        assert Mover.query.filter_by(is_demo=True).filter(Mover.team_id.isnot(None)).count() == 14


def test_clear_demo_data_only_removes_demo_rows():
    app = create_app(testing=True)
    with app.app_context():
        partner = TruckPartner(name='Demo Only', company='Demo Company', is_demo=True, seed_source='demo')
        vehicle = Truck(partner=partner, registration_number='DEMO-VEHICLE', is_demo=True, seed_source='demo')
        mover = Mover(name='Demo Mover', is_demo=True, seed_source='demo')
        team = Team(name='Demo Team', is_demo=True, seed_source='demo')
        mover.team = team
        real_partner = TruckPartner(name='Real Only', company='Real Company', is_demo=False, seed_source='production')
        db.session.add_all([partner, vehicle, mover, team, real_partner])
        db.session.commit()

        report = clear_demo_data()

        assert report == {'partners': 1, 'vehicles': 1, 'movers': 1, 'teams': 1}
        assert TruckPartner.query.filter_by(name='Demo Only').count() == 0
        assert Truck.query.filter_by(registration_number='DEMO-VEHICLE').count() == 0
        assert Mover.query.filter_by(name='Demo Mover').count() == 0
        assert Team.query.filter_by(name='Demo Team').count() == 0
        assert TruckPartner.query.filter_by(name='Real Only').count() == 1


def test_demo_cli_commands_seed_and_clear():
    app = create_app(testing=True)
    runner = CliRunner()
    with app.app_context():
        db.session.query(Truck).delete()
        db.session.query(Mover).delete()
        db.session.query(Team).delete()
        db.session.query(TruckPartner).delete()
        db.session.commit()

    with app.app_context():
        seed_result = runner.invoke(app.cli, ['seed-demo'])
        assert seed_result.exit_code == 0, seed_result.output
        assert 'Seeded 7 truck partners' in seed_result.output

        clear_result = runner.invoke(app.cli, ['clear-demo'])
        assert clear_result.exit_code == 0, clear_result.output
        assert 'Cleared 7 truck partners' in clear_result.output

        assert TruckPartner.query.filter_by(is_demo=True).count() == 0
        assert Truck.query.filter_by(is_demo=True).count() == 0
        assert Mover.query.filter_by(is_demo=True).count() == 0
        assert Team.query.filter_by(is_demo=True).count() == 0
