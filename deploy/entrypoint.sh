#!/bin/sh
# Runs on every container start: bring every schema up to date, then start the server.
set -e
python manage.py migrate_schemas --shared --noinput   # public schema (college registry)
python manage.py migrate_schemas --noinput            # every college's own schema
exec "$@"
