# Local development setup

Needs Python 3.13 and PostgreSQL 17 (Postgres.app on macOS).

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Postgres (Postgres.app): start the server, then create the dev database
createdb -U postgres -h localhost college_erp_dev      # default DATABASE_URL

cp .env.example .env                                   # then edit .env: DJANGO_DEBUG=1 and your own DJANGO_SECRET_KEY
                                                       # (python -c "import secrets; print(secrets.token_urlsafe(64))")
.venv/bin/python manage.py migrate_schemas --shared    # public schema (college registry)
.venv/bin/python manage.py create_demo_college         # schema "demo", host demo.localhost, 3 users
.venv/bin/python manage.py runserver                   # open http://demo.localhost:8000/
```

Demo logins (dev only): `admin@demo.in`, `staff@demo.in`, `student@demo.in`, password `demo-pass-123`.
`*.localhost` resolves to 127.0.0.1 in Chrome and Firefox. The bare `localhost:8000` returns 404 by design (no college).

Checks that CI runs (all must pass before merging):

```bash
.venv/bin/python manage.py test                 # 84 tests: isolation, access control, lockout, audit log
.venv/bin/ruff check .
.venv/bin/bandit -q -c pyproject.toml -r main_app colleges college_management_system audit_trail --severity-level medium
.venv/bin/pip-audit -r requirements.txt --strict
```

Secret scan (also in CI): `git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline`

Unlock a locked-out login: `python manage.py tenant_command axes_reset --schema=demo`

Production-like check: `DJANGO_DEBUG=0 DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=.example.com RECAPTCHA_SITE_KEY=x RECAPTCHA_SECRET_KEY=y python manage.py check --deploy --fail-level WARNING`

Environment variables: `DATABASE_URL`, `DJANGO_SECRET_KEY` (always required, no default), `DJANGO_DEBUG` (default 0; set 1 locally),
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `RECAPTCHA_SITE_KEY`, `RECAPTCHA_SECRET_KEY`, `FCM_SERVER_KEY`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `AUDIT_CLIENT_IP_HEADER` (only behind a proxy you control).
With `DJANGO_DEBUG=0` run `collectstatic` first, set the reCAPTCHA keys, and list real hosts in `DJANGO_ALLOWED_HOSTS`.


## WhatsApp digest (background jobs)
Tests need nothing (tasks run inline, no network). To try it locally: `manage.py run_digest --ignore-time` runs a college's digest in the
foreground with no Redis (it is in dry run until a college admin turns it on in the app). The full variable list and the Celery command are
in `docs/WHATSAPP_DIGEST_ACCEPTANCE.md`. Generate `FIELD_ENCRYPTION_KEY` once and keep it: losing it makes stored college secrets unreadable.

## Django admin (platform staff only)

Set `PLATFORM_ADMIN_EMAILS=you@yourcompany.in` in `.env`, then for each college:

```bash
python manage.py migrate_schemas
python manage.py create_platform_admin demo you@yourcompany.in
```

Add the printed `otpauth://` link to an authenticator app, then sign in at `http://demo.localhost:8000/platform-admin/`
with your password and the 6-digit code.
