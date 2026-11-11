#!/bin/sh
# Run the whole test suite against a Supabase (or any remote) Postgres, to prove tenant isolation there too.
# Put the connection string in .env.test-db (git-ignored), one line:
#   DATABASE_URL=postgres://postgres.<project-ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require
# Use SESSION mode (port 5432), never the transaction pooler (port 6543): django-tenants relies on SET search_path,
# which a transaction pooler forgets between statements. Use a THROWAWAY project: Django creates and drops a
# database called test_postgres on it.
set -e
cd "$(dirname "$0")/.."
[ -f .env.test-db ] || { echo "Create .env.test-db first (see the comments in this script)."; exit 1; }
set -a; . ./.env.test-db; set +a
case "$DATABASE_URL" in
  *:6543/*) echo "Refusing: port 6543 is the transaction pooler. Use the session-mode string (port 5432)."; exit 1;;
esac
exec .venv/bin/python manage.py test --noinput "$@"
