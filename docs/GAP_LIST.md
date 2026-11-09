# Gap list: base repo vs pilot scope

Last updated 2026-11-09. Base: fork of Ansarimajid/College-ERP (MIT). Stack now: Django 5.2.19, Python 3.13, PostgreSQL 17.

## Pilot MVP scope

| # | Pilot item | Status | Gap |
|---|---|---|---|
| 1 | Institution setup | Partial | Only flat Course, Session, Subject. Missing departments, programmes, batches/sections, regulations, subject links to programme/semester/regulation. |
| 2 | Bulk Excel import | None | One-by-one forms only. No parent entity, no import. |
| 3 | Student 360 profile | Minimal | Profile has name, gender, address, photo only. Missing guardian, enrolment number, history, attendance/marks/fees views. |
| 4 | QR attendance + 75% warning | Partial | Manual per-subject checkbox attendance. Missing rotating QR, geofence, 75% shortage calculation, 30-second target. |
| 5 | Internal marks + CO-PO | Minimal | Only `test`/`exam` floats. Missing COs, POs, mapping, question tagging with Bloom's level, attainment. |
| 6 | Fees + Razorpay | None | No fee models, payments, receipts, reminders. |
| 7 | WhatsApp parent alerts | None | Only FCM token fields. No parent contacts, WhatsApp, Celery/Redis. |
| 8 | Principal dashboard | Minimal | Basic HoD home. No Principal/Management role, no analytics. |

Roles: 3 of 9 exist (HoD, Staff, Student). Missing Super admin, Principal/Management, IQAC coordinator, Office staff, Parent, Placement officer. Role checks are one middleware keyed on module name, not per-endpoint.

Out of pilot scope but present in the base (remove or hide): leave, feedback, `erpnext_*` templates. The library pages are removed (URLs, menus, views); the `Book`, `IssuedBook`, `Library` models remain until a migration drops them.

## Non-negotiables

| Requirement | Status |
|---|---|
| Tenant isolation (django-tenants, schema per college) | Built and tested: 17 tests in `colleges/tests/` pass (schema layout, ORM, HTTP host routing, sessions, credentials, record ids, media paths). Gaps: see "Tenancy follow-ups". |
| Per-endpoint RBAC + audit log of every data change | **Built**: explicit policy on every endpoint (deny by default), object-level checks for teachers, append-only audit log per college. See `docs/SECURITY_AUDIT.md` (login lockout via django-axes is in; open items listed there). |
| Daily off-server backups, monthly restore test | Absent. |
| DPDP: consent records, export/deletion, India residency | Absent. |
| University rules as configuration | Not met (fixed `test`/`exam` fields). |
| DRF, Celery, Redis | Absent. |
| React + Tailwind frontend, Capacitor Android app | Absent (server-rendered templates). |

## Known bugs (fix when touching the area)

- ~~`user_type` string-vs-integer bug~~ **Fixed 2026-11-09** (role always stored as a string, constants in code, default is
  student, profiles backfilled; `UserTypeTests`). Bulk import and Student 360 can rely on it.
- Library pages (missing templates) removed, so every remaining page renders; a test crawls all GET routes for each role.
- All other earlier 500s (POST-style endpoints hit with GET, `/accounts/login/`) are fixed; see `docs/SECURITY_AUDIT.md`.

## Tenancy follow-ups

- Public schema has no host or pages yet: unknown/bare hosts return 404. A super-admin site (create colleges, subscriptions) is still to build; today colleges are created with `manage.py create_demo_college` or the shell.
- Uploaded files are now stored under `MEDIA_ROOT/<schema>/`, but serving them to the right college and profile-picture URLs are not tested or fixed yet (existing `media/` files from the upstream repo are not under any college folder). Do this before photo upload goes live.
- The tenant test base creates colleges outside Django's test transaction; each test run creates and drops two schemas (about 5 seconds).
- Not covered by tests yet: cross-college access via Celery jobs, cache keys, backups/restore per college (add when those exist).
- Local dev: a user's role pages need the right data (e.g. a student with no course crashes `/student/view/attendance/`, same family as the other 500s).

## Housekeeping done

- Middleware merge conflict resolved; secrets, DEBUG, ALLOWED_HOSTS and reCAPTCHA keys moved to environment variables.
- Initial migrations added; `db.sqlite3` untracked; `THIRD_PARTY_LICENSES.md` added.
- Django 3.1 -> 4.2 -> 5.2 LTS; `STORAGES` replaces `STATICFILES_STORAGE`.
- django-tenants 4.0.1: `colleges` app (College, Domain), SHARED/TENANT app split, tenant middleware, per-college media storage.

## Still open

- `origin` removed from the upstream remote (renamed `upstream`, push disabled); the private repo URL is still to be added as `origin`.
- Old secrets (upstream author's Gmail app password, SECRET_KEY, reCAPTCHA keys) remain in git history.
