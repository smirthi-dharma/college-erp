# College ERP — Project Context for Claude Code

## What we're building
A multi-tenant cloud ERP for Indian colleges and universities, sold as a per-student annual subscription.
One platform serves every college; each college's data lives in its own PostgreSQL schema.
Positioning: "the ERP that gets your college accreditation-ready while running daily operations."

Full proposal (modules, roadmap, pricing, risks): https://claude.ai/artifact/8L4ZTMPkkc9FyCLpWZFJA5

## Starting point
- Base: fork of Ansarimajid/College-ERP (Django, MIT licence), copied into this private repo.
- Keep `THIRD_PARTY_LICENSES.md` (the original MIT notice). Never copy GPL code from other projects.
- Treat the base as a scaffold: add tests and refactor each module as we touch it.

## Tech stack
- Backend: Django + Django REST Framework, Celery + Redis for background jobs
- Multi-tenancy: django-tenants, schema per college, subdomain per college (college.ourerp.in)
- Database: Supabase Pro (Mumbai region), used as plain PostgreSQL
- File storage: Supabase Storage; nightly DB dump copied to Cloudflare R2
- Frontend: React + Tailwind (web app), same codebase wrapped with Capacitor for the Android app
- Hosting: Hostinger VPS (Mumbai) running the app via Coolify; Cloudflare free plan for DNS/HTTPS
- Integrations: Razorpay (fees settle to the college's own account), WhatsApp Cloud API (Meta, direct),
  Firebase Cloud Messaging for push notifications
- Developer machine: Windows + PowerShell

## Pilot MVP scope (Phase 1, target Jan–Apr 2027)
1. Institution setup: departments, programmes, batches/sections, subjects, regulations
2. Bulk Excel import of students, parents, staff
3. Student 360 profile
4. QR attendance (rotating code + geofence) with 75% shortage early warning
5. Internal marks with auto CO-PO attainment (questions tagged with CO + Bloom's level)
6. Fees: structures, concessions, Razorpay/UPI payments, auto receipts, reminders
7. WhatsApp parent alerts (daily absence digest, fee due, marks)
8. Principal dashboard

NOT in the pilot: exams/results, timetable, admissions, library, hostel, HR/payroll, LMS.

## Roles
Super admin, Management/Principal, IQAC coordinator, HoD, Faculty, Office staff
(admissions, exam cell, accounts), Student, Parent, Placement officer.

## Non-negotiables
- Tenant isolation: College A must never see College B's data. Write tests for this first.
- Role-based access on every endpoint; audit log of every data change.
- Automated daily off-server backups; test a restore monthly.
- DPDP Act readiness: consent records, data export/deletion, India data residency.
- University rules (grading, CBCS, internal marks) stored as configuration, never hard-coded.
- Attendance must take under 30 seconds per class on a cheap Android phone.

## How we work
- One feature at a time, with acceptance checks agreed before building.
- Build in a branch with tests -> Smirthi tests locally as each role -> merge -> deploy to staging.
- Update the "Current status" section below at the end of each session.

## Current status
Last updated: 2026-11-09 (session 3)

- [x] Base repo running locally (Django 5.2.19 LTS, Python 3.13 in `.venv`, PostgreSQL 17 via Postgres.app, dev DB `college_erp_dev`). Config only from env / git-ignored `.env` (template `.env.example`). History reset to a clean orphan `main`; the old upstream history lives only in the local branch `legacy-upstream-history-LOCAL-ONLY` (contains upstream's secrets, NEVER push). Private repo `github.com/smirthi-dharma/college-erp`; CI (tests, ruff+bandit, pip-audit, check --deploy, secret scan) is green. **Workflow now: one branch + PR per feature, never commit straight to main; merge when CI is green.** Merged: PR #1 (CLAUDE.md, docs/OBE_REFERENCE.md, docs/obe_seed.json), PR #2 (library models dropped, migration 0004).
- [x] Gap list: base vs pilot scope -> `docs/GAP_LIST.md`. The `user_type` string-vs-int bug is FIXED (with tests); remaining known crashes are only the out-of-scope library pages.
- [x] CI added in `.github/workflows/ci.yml` (tests on Postgres 17, ruff, bandit, pip-audit, `check --deploy` with production-like settings). All steps pass locally; first GitHub run passed (2026-11-09).
- [x] Security audit of every inherited view -> `docs/SECURITY_AUDIT.md` (15 findings fixed, 9 open items). Explicit `@role_required` / `@public_view` on every endpoint (deny by default), teacher object-level checks, CSRF everywhere, POST-only deletes/logout, stored XSS fixed, password validators on, inactive users blocked, secrets moved to env. Biggest open item: no login rate limit (add django-axes before pilot).
- [x] Audit log: new tenant app `audit_trail` (append-only, DB trigger, per college, PII masked). 18 tests.
- [x] Login lockout (django-axes 9): 5 failures per email+address = 30 min lock (429), per-college, audited. Library pages removed from URLs/menus/views (models remain). Secret scan: detect-secrets baseline is empty and runs in CI; Firebase config now from `FIREBASE_WEB_CONFIG`; upstream README/CONTRIBUTING/SECURITY/CODE_OF_CONDUCT (another person's email, demo credentials) replaced by our own README.
- [x] Pilot item 1, institution structure (branch `feat/academics-models`): new tenant app `academics` (Department, Programme, Regulation, Batch, Section, Subject, AcademicTerm, SubjectOffering, Enrolment) replaces Course/Session/Subject everywhere; legacy data migrated (main_app 0005-0007, tested). Enrolment rules: core offerings auto-enrol the section; electives by HoD or Excel (.xlsx, column `email`); joining/moving section auto-enrols into core offerings and withdraws from the old ones without deleting marks/attendance; outside-section (arrear) enrolment allowed; every change audited. HoD pages under `/structure/`. Acceptance checks: `docs/ACADEMICS_ACCEPTANCE.md` (items 1-18 built; 19-21 = admin lockdown + 2FA + admin audit test = next branch `feat/admin-2fa`, NOT built). 118 tests. Not built yet: roll numbers, HoD-only-own-department rule, Excel bulk import of students/staff (feature 2). Each new view needs a role decorator; new apps with models must be added to `AUDIT_APPS`.
- [x] Multi-tenant setup with django-tenants 4.0.1: `colleges` app (College + Domain), schema per college, subdomain routing, per-college media paths. 17 isolation tests in `colleges/tests/` pass (run `manage.py test colleges`); mutation-checked (they fail if sessions go to the public schema or media storage is shared). Setup steps in `docs/DEV_SETUP.md`; demo college via `manage.py create_demo_college` (http://demo.localhost:8000). Follow-ups in `docs/GAP_LIST.md` ("Tenancy follow-ups"): super-admin/public site, serving uploaded media per college, tests for Celery/cache/backups once those exist.
- [~] OBE / CO-PO data model: official rules now in `docs/OBE_REFERENCE.md` + `docs/obe_seed.json` (versioned PO sets, rubric support, configurable targets); design drafted in `docs/CO_PO_DATA_MODEL.md` (models, calculation with a golden example, rules-as-config, roles, 10 acceptance checks, 5 open questions). Nothing built. Waiting on Smirthi to approve the acceptance checks and answer the open questions (accreditation target, default thresholds, internal-only attainment, question-wise marks, audit-log timing). Needs `academics` minimum models (pilot item 1) before `obe` can be built; the audit log now exists. NOT started: Smirthi is confirming the open questions with faculty.

Housekeeping done: merge conflict fixed, secrets/DEBUG/ALLOWED_HOSTS/reCAPTCHA/FCM keys moved to env vars, initial migrations added, `db.sqlite3` untracked, `THIRD_PARTY_LICENSES.md` added. Old secrets still exist in git history (they belong to the upstream author). Env vars: see `docs/DEV_SETUP.md`.
