"""Baseline the existing Chigo SQLAlchemy schema.

Revision ID: 20261006_baseline_chigo_schema
Revises:
Create Date: 2026-10-06

This initial revision creates the application's existing relational tables for
new databases. Existing deployments with these tables must be reviewed and
stamped at this revision before upgrading; this migration is not a data reset.
"""

from alembic import op

revision = '20261006_baseline_chigo_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from moving_company.models import db

    db.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError(
        'The Chigo baseline migration is intentionally irreversible to protect existing application data.'
    )
