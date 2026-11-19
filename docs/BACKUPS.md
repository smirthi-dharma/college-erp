# Backups, restore test, error tracking and the health check

Status (2026-11-19): **prepared, not live.** The scripts, tests and this guide are in the repository; nothing runs until staging
exists (`docs/STAGING_SETUP.md`). The scripts were exercised end to end against a local Postgres with a local folder as the
destination (backup, encrypt, checksum, restore into a scratch database, wrong passphrase, tampered file, name guards). They have
**not** been run against Cloudflare R2 or Supabase.

## 1. What is protected

One `pg_dump` of the whole database every night covers every college's schema, the shared tables and the audit logs. The dump is
encrypted on the server before it leaves, so Cloudflare only ever holds an unreadable file. **Uploaded files** (profile pictures
and consent-form scans, one folder per college under `MEDIA_ROOT`) are archived and encrypted the same way, in a second file with
the same time stamp. The database is dumped first and the files second, so every file the dump refers to is in the archive.

| | |
|---|---|
| Frequency | nightly at 02:00 India time (`30 20 * * *` UTC) |
| Format | `pg_dump --format=custom --compress=9`, then `gpg --symmetric --cipher-algo AES256` |
| Where | R2 bucket, `daily/erp-<UTC time>.dump.gpg` (database) and `erp-<same time>.media.tar.gpg` (uploaded files), each with a `.sha256`; on the 1st of the month also `monthly/` |
| Kept | `daily/` 35 days, `monthly/` 400 days (R2 lifecycle rules, below). The scripts never delete anything |
| Loss window (RPO) | up to 24 hours. Supabase Pro's own daily backups are a second layer, not a replacement |
| Time to recover (RTO) | not measured yet. The first restore drill fills this in `docs/RESTORE_LOG.md` |

## 2. One-time setup (you do this; about 30 minutes)

1. **R2 bucket.** Cloudflare dashboard, R2, create a bucket named like `erp-backups-prod`. Region: Asia-Pacific hint.
2. **Lifecycle rules** on the bucket: delete objects under `daily/` after 35 days; under `monthly/` after 400 days.
3. **API token.** R2, Manage API tokens, create a token with *Object Read & Write* limited to this one bucket. Keep the access key
   id and secret in Coolify's secret store, never in the repository or in chat.
4. **Encryption passphrase.** Generate one: `openssl rand -base64 36`. Put it in a file on the server (mode 600) and in your
   password manager, and keep a **second copy somewhere that is not the server and not Cloudflare** (printed in a safe, or a
   second password manager). **If the passphrase is lost, every backup is unreadable. If it leaks, whoever also gets the bucket can
   read the data.** Rotating it is a new passphrase for new backups; old files still need the old one, so keep old ones too.
5. **Dead-man's switch** (recommended): create a free check at healthchecks.io (or similar) expecting a ping every 24 h; put its
   URL in `BACKUP_PING_URL`. A silent failure then becomes an e-mail the next morning. The script calls `<url>/fail` on errors.
6. **Tools on the machine that runs the job:** `postgresql-client-17`, `gnupg`, `awscli`, `curl`. The current Dockerfile (PR #13)
   does not install them yet; add them there or run the job from a small separate container. Do this when you set up staging.
7. **Schedule** it as a Coolify scheduled task with the environment below.

```bash
# environment for the scheduled task (values come from Coolify secrets)
DATABASE_URL=postgres://...            # Supabase session pooler (port 5432) or direct connection. NEVER port 6543
MEDIA_ROOT=/app/media                  # the folder of uploaded files (the backup refuses to run if it is missing)
BACKUP_PASSPHRASE_FILE=/run/secrets/backup_passphrase
BACKUP_DEST=s3://erp-backups-prod
R2_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...
BACKUP_PING_URL=https://hc-ping.com/<your check id>
```

Command: `deploy/backup/backup.sh`. First run it by hand with `DRY_RUN=1` to see the plan, then once for real, then confirm the file
is in the bucket and run the restore test below straight away: **a backup that has never been restored is not a backup.**

## 3. The monthly restore test (30 minutes, first working day of the month)

Restore into a **scratch** Postgres that is not production and not Supabase (your own machine's Postgres, or a throw-away
`postgres:17` container). The script creates a new database whose name must start with `restore_`, never overwrites or drops
anything, and refuses production and Supabase addresses.

```bash
export RESTORE_ADMIN_URL=postgres://postgres@localhost:5432/postgres
export RESTORE_DATABASE=restore_$(date +%Y%m)
export RESTORE_MEDIA_DIR=/tmp/restore_media_$(date +%Y%m)   # a NEW folder; its name must start with restore_
export BACKUP_PASSPHRASE_FILE=...  BACKUP_DEST=s3://erp-backups-prod  R2_ENDPOINT=...  AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...
export PRODUCTION_DATABASE_URL=...       # optional: makes it refuse the production host
deploy/backup/restore.sh latest          # or an exact file name; BACKUP_PREFIX=monthly for a monthly copy
```

It downloads both files, checks both SHA-256 sums, decrypts, inspects the file archive (it refuses one with absolute or `..`
paths, before creating anything), restores the database, unpacks the files into the new folder, and prints per college the number
of users and audit entries and the newest audit entry. Then it **checks every profile picture and consent-form scan the database
names against the restored files**: any that is missing is listed as `MISSING FILE` and the script ends with `RESTORE INCOMPLETE`
(exit 3), which is an incident, not a pass. For a backup made before files were included, use `SKIP_MEDIA=1`. Check:

- [ ] it ended with `RESTORE OK`
- [ ] the picture/scan line per college says `0 missing`
- [ ] the number of restored files is plausible (it is printed)
- [ ] the colleges listed are the ones you expect
- [ ] the newest audit entry is from the last day or so (that is your real loss window)
- [ ] user counts match the live system within a day's changes
- [ ] optional but best: point a local copy of the app at the restored database and log in as a college admin

Then write one line in `docs/RESTORE_LOG.md` (date, file, how long, result), and drop the scratch database with the command the
script prints (and remove the restored files folder). If any step fails, that is a production incident: fix it the same day and repeat the test.

## 4. Real disaster (the live database is lost)

1. Do not panic and do not delete anything. Find the newest file in `daily/` and run the restore into a **new** database on the
   new server. Note the time it took.
2. Point `DATABASE_URL` of the app at it, run `python manage.py migrate_schemas` (safe: it only adds what is missing), check
   `/healthz`, log in as a college admin.
3. Tell the colleges how much was lost (the newest audit entry time) and write it down.

## 5. Health check: `GET /healthz`

Answers `200 {"status":"ok"}` or `503 {"status":"unavailable","checks":{"database":"failed"}}`. It works over plain HTTP on any
host name or IP (so Coolify and the container can call it as `http://localhost:8000/healthz`), needs no login, and shows nothing
about colleges, users or versions. Use it as the Coolify health check path and in an uptime monitor (UptimeRobot, Better Stack)
on the public name, alerting you by e-mail or WhatsApp.

## 6. Error tracking (Sentry)

Off unless `SENTRY_DSN` is set. The rule: send only what we control and nothing that can carry a person's data. An error report
holds the exception type, the stack of code locations, the request method and path (no query string), the release, the
environment and the college's schema name as a tag. It never holds request bodies, cookies, headers, user, local variables,
breadcrumbs, extra data, or the **text** of exception and log messages (a database error or an f-string can contain a name, an
e-mail address, a phone number or a roll number, and a name or a roll number cannot be recognised by pattern). The text is
replaced by a fixed notice; to debug, use the type, the stack and the path, and reproduce with demo data. No performance tracing.

**Proved by test:** `student360/tests/test_error_reporting.py` starts a real Sentry client with the project's settings and
raises real errors through the real WSGI application with Sentry's WSGI layer (the same path as gunicorn): from a student's page,
from a search for a name, from an admin form post that is logged with its data, from a log line and outside a request. Each
carries names, phone numbers, e-mail addresses, a roll number, an address, a date of birth, a guardian's name and phone, an
uploaded file name and a password. Every event that would be sent is searched for every one of those values; the test also
fails if nothing was captured, so it cannot pass by doing nothing. It was mutation-checked (leaving the exception text, the
request data or the cookies in each makes it fail).

Set `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (staging or production) and `GIT_COMMIT` in Coolify. **Region: US or EU is accepted
(decided 2026-11-19)**; with the above, no personal data leaves India. After the first deploy, raise a test error on staging and
open it in Sentry to confirm it shows no name, e-mail, phone or request body. Create an alert rule: e-mail on any new issue.

## 7. Not covered yet (be honest with colleges about these)

- Uploaded files are backed up as an archive once a night, with the database, from `MEDIA_ROOT`. When they move to Supabase
  Storage (planned), that storage needs its own copy into the same bucket; until then the server's disk is the only live copy.
- No point-in-time recovery: you can go back to a night, not to a minute. Supabase Pro adds that on its side.
- The monthly drill is a person's job; a calendar reminder is the control.
