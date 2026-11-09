# Academics models (pilot item 1): design and acceptance checks

Status: APPROVED by Smirthi on 2026-11-09 with the additions below. Items 1-18 are BUILT and tested (`academics/tests/`). Items 19-21 (admin lockdown, 2FA, admin audit) are a separate feature branch and PR (`feat/admin-2fa`), NOT built yet.

## Design

New tenant app `academics` (in TENANT_APPS and AUDIT_APPS; one schema per college).

| Model | Fields | Rules |
|---|---|---|
| Department | name, code | code unique |
| Programme | department, name, code, duration_semesters | code unique; semesters 1..12 |
| Regulation | programme, name (R2022), effective_from | unique(programme, name); grading/internal-mark rules attach later as configuration |
| Batch | programme, regulation, start_year | unique(programme, start_year); regulation must belong to the same programme |
| Section | batch, name | unique(batch, name) |
| Subject | programme, regulation, semester, code, name, credits, kind (theory/lab) | unique(regulation, code); semester within programme duration; credits >= 0 (decimal, halves allowed) |
| AcademicTerm | name (2026-27 ODD), start_date, end_date | minimal; needed by offerings |
| SubjectOffering | subject, section, term, faculty (Staff), kind (core/elective) | unique(subject, section, term); section's batch must share the subject's regulation |
| Enrolment | offering, student, status (active/withdrawn), enrolled_on, withdrawn_on, source (auto/manual/import/transfer) | unique(offering, student); a student from another section is allowed (arrear/repeat), flagged `outside_section`; withdrawal never deletes the row |

Replacing the old models:
- `Course` -> `Programme`; `Session` -> `Batch` (+ `AcademicTerm` where it meant a term).
- `Student.course/session` -> `Student.section` (batch and programme follow from it).
- `Staff.course` -> `Staff.department`.
- `Subject` (one fixed teacher) -> `Subject` (catalogue entry) + `SubjectOffering` (who teaches it to whom, when).
- `Attendance.subject` -> `Attendance.offering`; `StudentResult.subject` -> `StudentResult.offering`.
- Data migration maps every existing Course/Session/Subject row to the new rows, so no college data is lost. Old tables are dropped in a second migration after the copy is verified.
- Existing HoD/staff/student pages and the permission helpers (`subject_for_staff`, `_students_of`) are rewritten on the new models: staff act on offerings they teach, only for students enrolled in that offering.

## Acceptance checks

Data integrity
1. Each unique rule in the table above is enforced by the database (duplicate -> IntegrityError), not only by forms.
2. Cross-links are validated: a Batch with a regulation of another programme, a Subject whose semester exceeds the programme duration, an Offering whose section's regulation differs from the subject's, and an Enrolment of a student from another section are all refused (ValidationError on `full_clean`, and the HoD forms show the message).
3. Deleting a Department, Programme, Regulation, Batch, Section, Subject or Offering that is still referenced is refused (PROTECT), so attendance, marks and enrolments can never vanish by accident. Deleting a student (erasure request) removes their enrolments, attendance and marks with them; the audit log keeps the record.
4. Credits and semester reject negative or out-of-range values.

Migration
5. On a database with old Course/Session/Subject/Attendance/Result rows, the migration produces the new rows with every attendance and result still attached to the right student and subject (counts and per-student totals equal before and after).
6. `makemigrations --check` is clean and the migration runs on an empty database and on a populated one, in every college schema.

Tenant isolation (extend the existing 17)
7. College A's departments, programmes, regulations, batches, sections, subjects, offerings and enrolments are invisible from College B (ORM and HTTP), and the same codes can exist in both colleges.

Permissions (extend the existing access-control tests)
8. Every new view has `@role_required`; the policy test lists them. Only the HoD can open the structure pages; staff see only their own offerings (in their attendance and marks pages); students see only their own enrolments; anonymous gets redirected.
9. A staff member cannot take attendance, read results or write results for an offering they do not teach, or for a student not enrolled in it. A student cannot read another student's enrolments or marks.
10. All new state changes are POST with CSRF; no state change over GET.

Audit log
11. Create, update and delete of every new model writes an audit row in that college's schema (extend `AUDIT_APPS`; the existing "no update()/bulk_create" guard test covers `academics`).

Regression
12. All 84 existing tests still pass (those that touch Course/Subject are updated to the new models, never deleted), plus ruff, bandit, pip-audit, `check --deploy`, and secret scan in CI.

Out of scope here: CO/PO models, timetable, fee structures, bulk Excel import (next features), and the finer "HoD sees only their department" rule (follow-up, noted in SECURITY_AUDIT item 8).

## Approved additions (2026-11-09)

Enrolment rules
13. Creating a CORE offering enrols every active student of its section (source=auto). Creating an ELECTIVE offering enrols nobody.
14. Electives: HoD assigns students one by one, or bulk from an Excel list (.xlsx; column `email`; roll numbers arrive with the bulk-import feature). The import is all-or-nothing per file, reports each bad row, ignores nothing silently, and is idempotent (re-upload creates no duplicates).
15. A student joining a section later (lateral entry) is auto-enrolled in that section's core offerings for the current term.
16. A section transfer withdraws the student from the old section's offerings (status=withdrawn, withdrawn_on set) and enrols them in the new section's core offerings. Their attendance and marks rows for the old offerings are untouched and still readable (counts identical before and after).
17. A student outside the section can be enrolled by the HoD (arrear/repeat). The model and form allow it; the pilot does not use it.
18. Every enrolment change (create, withdraw, re-activate, bulk import, transfer) writes an audit row.

Django admin
19. The admin site is reachable only by super-admin (our staff) accounts on the public schema, never by college users: a HoD, staff or student gets 403/redirect on every `/admin/` URL of a college host.
20. Admin login requires two-factor (TOTP via django-otp): password alone cannot open any admin page; a wrong or reused code is refused; failed codes are rate-limited by axes.
21. An edit made through the admin writes an audit row with the admin user as actor (test: change a Programme name in the admin, assert the audit row and its actor).

## Remaining questions
None. (Auto-enrol and admin decisions answered above.)
