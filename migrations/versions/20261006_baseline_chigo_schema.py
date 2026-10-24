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
    # Alembic creates this metadata table with VARCHAR(32) by default, while
    # several Chigo revision identifiers are longer.  Widen it before Alembic
    # records this baseline revision so the following migration can proceed.
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)')


def downgrade():
    raise RuntimeError(
        'The Chigo baseline migration is intentionally irreversible to protect existing application data.'
    )
