# Staging setup: Supabase (Mumbai) + Hostinger VPS + Coolify

Status (2026-11-10): **prepared in the repo, not yet done.** Nothing below has been created or run. The account steps (A to D)
can only be done by you: they need your payment details, passwords and DNS. Steps E to G are commands. Wording of
dashboards changes over time; where a button name differs, follow the intent and the official docs.

What the repo now provides: a `Dockerfile` and a `docker-compose.yml` (web, Celery worker, Celery scheduler, Redis; neither has been built or
run yet, Docker is not installed on the development machine, so the first Coolify build is their first test; static tests guard their structure), `deploy/entrypoint.sh` (runs `migrate_schemas --shared` then `migrate_schemas` at every start),
`manage.py create_college`, and `scripts/test_on_supabase.sh`.

## A. Supabase project (database)
1. supabase.com -> New project. **Region: South Asia (Mumbai) / `ap-south-1`.** Make **two** projects:
   `college-erp-test` (throwaway, for the test suite) and `college-erp-staging`. Free plan is fine for both for now; the free
   plan pauses a project after a week of no use, so use Pro for staging when it matters.
2. Set a strong database password, store it in your password manager (never in chat, git or a screenshot).
3. Click **Connect** at the top of the project and copy the **Session pooler** string (host `aws-0-ap-south-1.pooler.supabase.com`,
   **port 5432**, user `postgres.<project-ref>`). Add `?sslmode=require` at the end.
   - Use session mode or the direct connection. **Never the transaction pooler (port 6543)**: django-tenants sets `search_path` per
     connection and a transaction pooler forgets it between statements, which would mix colleges' data.
   - The direct connection (`db.<ref>.supabase.co:5432`) is IPv6-only unless you buy the IPv4 add-on; the session pooler works
     over IPv4, so prefer it unless you know the VPS has IPv6.

## B. Prove the tests pass on Supabase (do this first, on your Mac)
```bash
cd my-erp
printf 'DATABASE_URL=postgres://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require\n' > .env.test-db
scripts/test_on_supabase.sh          # same suite incl. the 17 tenant-isolation tests; ~ a few minutes over the network
```
`.env.test-db` is git-ignored. Use the **test** project: Django creates and drops a database named `test_postgres` on it. The script
refuses port 6543. If it fails (for example the pooler user cannot create a database), copy the **last 40 lines** of the output to
me, without the password, and I will fix it. I will not have the password, so I cannot run this part myself.

## C. Hostinger VPS
1. hostinger.in -> VPS -> a KVM plan with at least 2 vCPU / 8 GB RAM (Coolify itself needs about 2 GB), **data-centre: India (Mumbai)**,
   OS: Ubuntu 24.04 (plain, not a Docker template). Set the root password / add your SSH key.
2. In hPanel's firewall allow TCP 22, 80, 443 and 8000 (8000 is Coolify's dashboard until you give it a domain).
3. SSH in as root and run Coolify's installer from coolify.io/docs (one command: `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash`).
4. Open `http://<vps-ip>:8000` **immediately** and register: the first account becomes the administrator.

## D. DNS (Cloudflare) and the repository
1. Cloudflare: add your domain, switch the registrar's name servers to Cloudflare's.
2. Add an **A record** per host, pointing to the VPS IP, **proxy OFF (grey cloud)** for now so Coolify can get Let's Encrypt
   certificates with the HTTP challenge: `staging.ourerp.in` and one per college, e.g. `demo.staging.ourerp.in`. (A wildcard
   certificate needs a DNS challenge with a Cloudflare API token; do that when you have more than a few colleges.)
3. Coolify -> Sources -> add a **GitHub App** (or a deploy key) so Coolify can read the private repo `smirthi-dharma/college-erp`.

## E. Create the stack in Coolify (web + worker + scheduler + Redis)
The repo's `docker-compose.yml` is the whole stack: `web` (gunicorn), `worker` (Celery, sends the WhatsApp digests and retries),
`beat` (the scheduler, **exactly one**) and `redis` (private: no published port, password required, data kept on a volume).
Postgres is not in it: that is Supabase.
1. Project -> New resource -> **Docker Compose** -> private repository `smirthi-dharma/college-erp`, branch `main`, compose file
   `docker-compose.yml`. (A single Dockerfile application also still works for the web part, but then you must add the worker,
   scheduler and Redis yourself, so use the compose file.)
2. Give the **`web`** service its domains (Coolify: the service's "Domains" field), port `8000`:
   `https://demo.staging.ourerp.in` (comma-separate more; each college host needs its own DNS record). Do not give any domain
   to `worker`, `beat` or `redis`.
3. Environment variables (Coolify encrypts them; compose refuses to start if a required one is missing):

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `DJANGO_DEBUG` | `0` |
| `DATABASE_URL` | the **staging** project's session-pooler string, with `?sslmode=require` |
| `DJANGO_ALLOWED_HOSTS` | `.staging.ourerp.in` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://*.staging.ourerp.in` |
| `RECAPTCHA_SITE_KEY` / `RECAPTCHA_SECRET_KEY` | your Google reCAPTCHA v2 keys for that domain (required when DEBUG is 0) |
| `PLATFORM_ADMIN_EMAILS` | your work e-mail |
| `AUDIT_CLIENT_IP_HEADER` | `HTTP_X_FORWARDED_FOR` (Coolify's proxy sets it; switch to `HTTP_CF_CONNECTING_IP` once Cloudflare proxying is on) |
| `REDIS_PASSWORD` | a long random string: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Used by Redis and by the app; no other setup |
| `FIELD_ENCRYPTION_KEYS` | one key from `python manage.py new_encryption_key`. **Back it up first** (`docs/KEY_MANAGEMENT.md`) |
| `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | from your Meta app (`docs/WHATSAPP_DIGEST_ACCEPTANCE.md`); may stay empty until you test WhatsApp: the digest then fails safely |

4. Storage: the compose file already creates two named volumes: `media` (mounted on `web` at `/app/media`; uploads such as consent
   scans live in one folder per college) and `redis-data`. **Back both up** with the nightly job once it exists; today they are
   not backed up (see Known gaps).
5. Turn on auto-deploy for `main`, then Deploy. Only `web` runs the migrations, at every start. `worker` and `beat` start without
   migrating; on the very first deploy they may log a few database errors for a minute until `web` has finished: that is expected.
6. Size: web (3 gunicorn workers) + worker (concurrency 2) + beat + Redis fit in 8 GB next to Coolify itself, but not comfortably
   in 4 GB. Watch memory after the first week.

## F. First college (Coolify -> the `web` service -> Terminal)
```bash
python manage.py create_college demo "Demo College" demo.staging.ourerp.in you@yourcompany.in   # prints a one-time password
python manage.py create_platform_admin demo you@yourcompany.in                                  # prints the 2FA link
python manage.py seed_demo --schema demo                                                        # optional: sample data for a demo
```
Open `https://demo.staging.ourerp.in/`, log in, change the password. The bare `staging.ourerp.in` returns 404 by design (no college).

## G. Smoke test, and then tell me
1. Log in as the college admin, open Student 360 and Attendance. 2. Open `https://staging.ourerp.in/` (404 expected).
3. Create a second college and confirm neither can open the other's `/students/<id>/`.
4. **Background jobs** (Coolify terminal of `worker`): `celery -A college_management_system inspect ping` must answer `pong`;
   the `beat` logs must show `dispatch-whatsapp-digests` being sent every minute; the `redis` container must be "healthy".
5. In the `web` terminal: `python manage.py rotate_encryption_keys --check` must say `0 unreadable`.
6. As college admin: sidebar "WhatsApp digest" -> turn it on **in dry run**, then (after attendance was marked and the send time
   has passed) the daily report shows "would have been sent" rows and nothing reaches any phone.
7. Meta's webhook URL for the later WhatsApp test: `https://demo.staging.ourerp.in/webhooks/whatsapp/` with your verify token.
When B and G are done, send me the test output tail and I will update `CLAUDE.md`.

## Known gaps (not staging blockers, but real)
No health-check endpoint for `web`; no automated backups yet (Supabase Pro has daily backups; the Cloudflare R2 copy, the backup of the
`media` and `redis-data` volumes and the monthly restore test are still to build); uploaded media is on a VPS volume, not Supabase
Storage; Redis holds only queued/retrying jobs (losing it loses no data: a queued message that no worker picked up is handed out again by the
per-minute sweep after 30 minutes, see `messaging/services.py`), so it is a low-risk component but is not backed up; the compose file and Dockerfile have never been
built or run, and Coolify's compose handling of `x-` anchors and `${VAR:?}` has not been tried.
