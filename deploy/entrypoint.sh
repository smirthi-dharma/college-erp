#!/bin/sh
# Runs on every container start. Only the web container migrates the databases (two containers migrating at once would race);
# the Celery worker and scheduler just start.
set -e
if [ "$1" = "gunicorn" ]; then
  python manage.py migrate_schemas --shared --noinput   # public schema (college registry)
  python manage.py migrate_schemas --noinput            # every college's own schema
fi
exec "$@"
