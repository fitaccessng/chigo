#!/bin/sh
set -eu

# Production schema changes are versioned through Alembic.  Do this before
# serving traffic so a newly provisioned database cannot accept requests with
# an incomplete schema.
if [ "${CHIGO_SCHEMA_BOOTSTRAP:-false}" = "true" ]; then
  flask --app run.py bootstrap-empty-production-schema
  flask --app run.py db stamp head
else
  flask --app run.py prepare-migration-version-table
  flask --app run.py db upgrade
fi

if [ "${CHIGO_INITIAL_SUPER_ADMIN_BOOTSTRAP:-false}" = "true" ]; then
  flask --app run.py bootstrap-initial-super-admin
fi

exec gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  run:app
