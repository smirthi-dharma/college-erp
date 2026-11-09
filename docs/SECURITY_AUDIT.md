# Security audit of the inherited code

Date: 2026-11-09. Scope: every view, form, template and setting inherited from Ansarimajid/College-ERP
(64 application routes at audit time; 60 after the library pages were removed), checked against
the non-negotiables in CLAUDE.md. Method: read all view code and templates, wrote failing tests first
(`main_app/tests/`), fixed until green, then broke each protection on purpose to confirm the tests catch it.

Result: 84 automated tests pass: 17 tenant isolation (`colleges`), 49 in `main_app` (access control, CSRF, GET-writes,
injection, login, lockout, `user_type`, passwords, library removal) and 18 audit log (`audit_trail`). CI runs them together with ruff, bandit,
pip-audit and `manage.py check --deploy`; all of these also pass locally. No inherited secret strings remain in the working tree.

## Findings and fixes

| # | Severity | Finding (as inherited) | Fix | Proved by |
|---|---|---|---|---|
| 1 | High | Roles were enforced by comparing the view's Python module name. Views outside the three guarded modules had no check at all. `/staff/result/edit/` let **any logged-in user, including a student, change any student's marks** (observed: 11 -> 100). | Every endpoint now declares `@role_required(...)` or `@public_view` (`main_app/access.py`). The middleware refuses any view without a policy (deny by default). | `PolicyTests` (every URL has a policy), `RoleSeparationTests` |
| 2 | High | `/get_attendance` was open to every role, so a student could list attendance records for any subject. | HoD and staff only; staff only for their own subjects. | `RoleSeparationTests`, `StaffObjectAccessTests` |
| 3 | High | No object-level checks: a teacher could read, take, update attendance and read or write results for **any** subject and student in the college, including other teachers' and other courses' (7 endpoints). | Teachers act only on subjects they teach, and only on students enrolled in that subject's course (`subject_for_staff`, `_require_student_in_subject`). The edit-result form only offers the teacher's own subjects and students. | `StaffObjectAccessTests` (10 tests) |
| 4 | High | `@csrf_exempt` on 20 state-changing endpoints (feedback replies, leave approval, attendance, notifications, token saves...). | All exemptions removed. Pages send the token with every AJAX call (`$.ajaxSetup` in the base templates). | `CsrfTests`: POST without a token is refused for every route and every role |
| 5 | High | State changes over GET: five `delete_*` links and logout. Any page that embedded `<img src="/student/delete/3">` could delete records. | POST only (405 otherwise); links became small forms with CSRF tokens. | `NoStateChangeOverGetTests` |
| 6 | High | Stored XSS: dashboards printed Python lists into `<script>` blocks with `|safe`. A student could set their first name to a script payload, which then ran in the principal's browser. | Values go through `js_json` (JSON with `<`, `>`, `&` escaped). | `InjectionTests` (fails when reverted) |
| 7 | High | Credentials committed in the source: Gmail address and app password, Django `SECRET_KEY`, reCAPTCHA secret, **Firebase server key**. | All read from environment variables; nothing is sent when unset. The app refuses to start in production without `DJANGO_SECRET_KEY`. | `check --deploy` in CI; grep clean |
| 8 | Medium | `user_type` defaulted to 1 (HoD): any code path that forgot the role created an administrator. The role was a string but the signals compared integers, so users created with `'3'` got no profile. | Default is student. The value is always stored as a string; signals use constants; migration backfills missing profiles. | `UserTypeTests` |
| 9 | Medium | Password validators were switched off when `DEBUG` was off (i.e. in production), and the HoD forms never ran them. | Always on, minimum length 10, enforced in the user forms. | `PasswordPolicyTests` |
| 10 | Medium | The email login backend ignored `is_active`: a deactivated user could still log in. Failed logins were also not recorded. | Backend honours `is_active`; login goes through Django's `authenticate()`; failures are audit-logged. | `LoginTests`, audit login tests |
| 11 | Medium | Uploads used a global `FileSystemStorage()`, bypassing the per-college folders and mixing colleges' files. | Uses the tenant-aware default storage. | `test_uploads_use_the_per_college_storage...` (static check only; see open items) |
| 12 | Medium | Views returned exception objects or `None` (HTTP 500) and showed `str(e)` to users, leaking internals. | Fixed in all touched views: proper 400/403/404 responses, generic user messages, details in server logs. | access-control suites |
| 13 | Medium | `on_delete=DO_NOTHING` on attendance and student links: deleting a student or subject with attendance raised a database error and could leave dangling rows. | `CASCADE` for attendance rows, `PROTECT` for courses and sessions still in use (migration 0003). | `NoStateChangeOverGetTests.test_delete_endpoints_work_with_post` |
| 14 | Low | Production defaults: `DEBUG` on by default, no secure cookies, no HSTS, no SSL redirect. | `DEBUG` off unless `DJANGO_DEBUG=1`; secure cookies, HSTS, SSL redirect, proxy header, CSRF trusted origins from env. | `check --deploy --fail-level WARNING` in CI |
| 15 | Low | `/accounts/login/` crashed (missing template). | Redirects to the real login page. | policy test |

## Audit log (non-negotiable: "audit log of every data change")

New tenant-schema app `audit_trail`. Every create, update and delete of every `main_app` model, plus login,
logout and failed login, writes one row: when, who (id, email, role), IP, user agent, request, model, object,
and the changed fields. Rows are written in the same transaction as the change (a rolled-back change leaves
no entry; a failure to log stops the change). Design choices:

- Append-only twice over: the model and queryset refuse update/delete, and a database trigger refuses
  UPDATE, DELETE and TRUNCATE on the table. Retention clean-ups must be a deliberate maintenance step.
- Passwords, device tokens are never stored. Personal fields (names, email, address, gender, photo) are recorded
  as "changed" without their values, and object names are `Model #id`, so the log never blocks an erasure
  request (DPDP Act) or leaks personal data to people who can read the log.
- Each college has its own log (separate schema); tested.
- The client IP is the connection address. `X-Forwarded-For` is ignored unless `AUDIT_CLIENT_IP_HEADER`
  is set to a header your proxy controls (spoof attempt tested).
- Limit: `QuerySet.update()`, `bulk_create()` and raw SQL do not send signals. A test fails if the application
  code uses them; new modules must be added to `AUDIT_APPS` in settings.

## Open items (not fixed here)

1. ~~No login rate limit or lockout~~ **Done** (django-axes): 5 failed logins for the same email from the same address lock that pair for 30 minutes (HTTP 429, also for unknown emails), a success resets the count, lock data lives in each college's schema and lockouts are written to the audit log. Residual risk: a distributed guessing attack on one account from many addresses is slowed only by reCAPTCHA and the password rules. Unlock a pair with `python manage.py tenant_command axes_reset --schema=<college>`.
2. **Old secrets** (Gmail app password, `SECRET_KEY`, reCAPTCHA and Firebase keys) were in the upstream history. The private
   repository starts from a fresh single commit, so they are not in it. They belong to the upstream author; treat them as public.
3. ~~Library module~~ **Removed**: URLs, menus, views, forms, templates and (migration 0004) the Book, IssuedBook and Library tables.
4. **Uploaded-file serving** is untested: files are stored per college, but how a college's photos are served
   (and kept private) is not built. The development `static()` route serves `MEDIA_ROOT` without authentication.
5. **Firebase web config** now comes from `FIREBASE_WEB_CONFIG` (your own project); the upstream project's config was removed
   from the service worker and three templates, and the service worker returns 404 until configured.
   The legacy FCM endpoint used by `notifications.py` has been retired by Google; move to the HTTP v1 API.
6. **Password-reset flow** (`/accounts/password_reset/`) was not tested for per-college isolation, and the
   email settings still point at SMTP via environment variables that are not set.
7. **No Content-Security-Policy header**; the templates use inline scripts. Plan a CSP when the React front end replaces them.
8. HoD can read every record in their own college by design. Finer rules (HoD of one department only)
   arrive with the departments model.
9. Not covered by tests yet: Celery jobs, caches and backups (they do not exist yet).

## Django admin: platform staff only, with 2FA (2026-11-09)

Before: `/admin/` was open to any account flagged `is_staff` in a college, with password-only login, and the
`CustomUser` admin page crashed (it still listed a removed `username` field; fixed).
Now (`college_management_system/admin_site.py`) an admin page needs ALL of: active staff superuser, e-mail listed in
`PLATFORM_ADMIN_EMAILS` (environment; empty = admin locked for everyone), and a TOTP code at login (django-otp: codes are
single-use and failed codes are throttled). College users, even if flagged superuser, never qualify. The admin moved from
`/admin/` to `/platform-admin/` (`DJANGO_ADMIN_URL` to change it). Platform admins live inside each college's schema
(a login in one college is useless in another); create one with
`python manage.py create_platform_admin <schema> <email>` (`--reset-2fa` after a lost phone). The secret is shown once.
Admin edits go through the ORM, so the audit log records them with the admin as the actor (tested for edit and delete).
Open: no recovery codes yet (reset by command); admin pages for the `academics` models are registered after that app merges.

## Rules for new code

- Every view gets `@role_required(...)` or `@public_view`. Without one the middleware returns 403 and the policy test fails.
- Look up objects through the user's scope (their subjects, their students, their college), never by raw id.
- State changes are POST (or PUT/DELETE) with the CSRF token; never `@csrf_exempt`.
- Use `save()` / `delete()`; not `update()`, `bulk_create()` or raw SQL, so the audit log sees the change.
- Never print Python values into scripts with `|safe`; use `js_json` or `json_script`.
- Secrets come from the environment.
