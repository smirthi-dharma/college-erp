# Bulk Excel import (pilot item 2): design and acceptance checks

Status: BUILT (branch `feat/bulk-import`). Approved 2026-11-09 with no first password, free-text roll numbers and changes C1-C7; Option A for the role (rename to College admin in PR #5, new Head-of-department role "4" with no pages). Measured: 2,000 students validate in 0.2 s and save in about 10 s locally. Not built (out of scope as listed): background processing for >2,000 rows (follow-up), set-password e-mails, parent logins, consent recording, fees blocker for rollback (the registry `ROLLBACK_BLOCKERS` is in place; fee models must register).

## What exists today
Students and staff are added one at a time. There is no roll number, no phone number, no employee ID and no parent
record anywhere, and passwords are set by hand by the HoD. Parents are needed for WhatsApp alerts (pilot item 7).

## Design

Three separate imports, each with its own downloadable template, under `/import/` (HoD only):
**Students**, **Parents**, **Staff**. Flow for each: upload `.xlsx` -> **validate everything without saving** ->
HoD sees a preview (counts, first 100 problems, downloadable error sheet) -> **Confirm** -> all rows are saved in one
transaction, or none. The uploaded file is never kept; only an import record is (who, when, type, file name, counts).

New data (migration, tenant schema):
| Where | Field | Rule |
|---|---|---|
| Student | `roll_number` | unique within the college, text, optional for old rows |
| CustomUser | `phone` | stored as +91XXXXXXXXXX when given |
| Staff | `employee_id` | unique, optional for old rows |
| Guardian (new model) | student, name, relation (father/mother/guardian/other), phone, email, is_primary | at most one primary per student; at least a phone |
| ImportBatch (new model, audited) | kind, file_name, rows, created, updated, status, by, at | one row per confirmed import |

Columns (first row = headers, order free, extra columns ignored with a warning, `*` = required):
- **Students**: `roll_number*, first_name*, last_name*, email*, gender (M/F), phone, programme_code*, batch_start_year*, section*, address`
- **Parents**: `student_roll_number*, name*, relation*, phone*, email, is_primary (yes/no)`
- **Staff**: `employee_id*, first_name*, last_name*, email*, gender (M/F), phone, department_code`

Mode (a checkbox, off by default): **create only**. Rows that match an existing record (same roll number / employee ID /
email) are errors in this mode. With **update existing** ticked they update name, gender, phone, address and section;
the identity (email, roll number, employee ID) is never changed by an import, and passwords are never touched.

New students are placed in the section named by `programme_code` + `batch_start_year` + `section`, which fires the
existing enrolment rules (core offerings auto-enrol); a section change on update follows the existing transfer rules.

## Acceptance checks

Validation (nothing is saved at this stage)
1. Only `.xlsx` is accepted (not `.xls`, `.xlsm`, `.csv`, renamed files); max 5 MB and 5,000 rows; a zip-bomb style
   workbook (huge uncompressed size) is refused before parsing.
2. Missing required column, unknown programme/department/section, duplicate roll number or email inside the file,
   duplicate with an existing record (create mode), bad email, bad gender, bad phone, over-long text: each is reported
   with sheet row number, column and a plain-English reason.
3. Phones: 10-digit Indian numbers and `+91`/`0`-prefixed forms are normalised to +91XXXXXXXXXX; anything else is an error.
   Emails are lower-cased and trimmed. Cell text is trimmed; numbers in text columns (roll number 2101234 stored as a
   number by Excel) are read as text without a trailing `.0`.
4. Cells starting with `=`, `+`, `-` or `@` are kept as plain text and never evaluated; formulas are read as their
   cached value; hyperlinks and macros are ignored.
5. The preview shows how many rows will be created / updated / skipped, and a downloadable `.xlsx` of all errors
   (row, column, message). A file with any error cannot be confirmed.

Saving
6. Confirm saves all rows or none (one transaction): a failure on row N leaves the database unchanged.
7. Confirm only works for the file that was validated (a token ties the preview to the session; changing or replacing
   the file requires a new validation); double-clicking Confirm creates nothing twice.
8. Imported users have no usable password (nobody can log in as them until a password is set, see Decision 1) and get
   the right role profile (student or staff) through the existing rules.
9. Students land in the right section and are auto-enrolled in that section's core offerings (existing enrolment tests
   pass unchanged); parents are linked to the right student; at most one primary guardian per student.
10. Update mode changes only the listed fields, never email, roll number, employee ID or password; an unchanged row is
    reported as "no change" and writes nothing.
11. Re-uploading the same file in create mode creates nothing (every row is a duplicate error); in update mode it
    reports all rows as "no change".

Security, audit, tenant isolation
12. Every import page is HoD-only (`@role_required`), POST + CSRF for upload/confirm; staff, students and anonymous
    users are refused; a template download is HoD-only too.
13. Every created or updated row is in the audit log with the HoD as actor (existing signals), plus one `ImportBatch` row;
    personal fields stay masked in the log.
14. College A's import cannot see or touch College B's data: the same roll numbers can exist in both colleges; a
    preview token from A is useless in B.
15. The uploaded file is held only in memory/temp for validation and deleted after confirm or on timeout (30 minutes);
    nothing from it is written to the media folder or the logs.
16. Roll number, employee ID and phone uniqueness are enforced by the database, not only by the import.

Performance and usability
17. A file of 1,000 students validates in under 15 s and saves in under 60 s on the CI runner (test with a generated file).
18. The templates download as `.xlsx` with the headers, one example row, and a Notes sheet listing allowed values
    (programme/section codes of that college come from its own data).
19. The Manage Students/Staff pages show roll number / employee ID and phone; the single-add forms gain the same fields
    (so one-by-one and bulk stay consistent), and the unique rules apply to both.

Regression
20. All 139 existing tests, ruff, bandit, pip-audit, `check --deploy` and the secret scan still pass; new tests are
    mutation-checked (break the transaction, the HoD check, the duplicate check, the formula guard, and watch them fail).

Out of scope here: parent login accounts and the parent app, sending the welcome/reset e-mails (e-mail is not
configured yet), photos, fees data, CSV, importing marks or attendance, consent records (DPDP consent comes with
the WhatsApp feature, but parent phones are stored only for those who are imported).

## Changes approved 2026-11-09 (these replace or add to the checks above)

C1. **Who may import: the college admin role only** (HoD scoping is not settled). HoD and staff get 403. See the open decision below:
    today the code has no separate "college admin" and "HoD"; the one admin role ("1") is called HoD.
C2. **Rollback of an import** (college admin only, audited). Allowed only when nothing refers to the imported records:
    no attendance, marks or fees (fees: the rule is registered now and enforced once fee models exist), no manual enrolments
    added after the import, and the person has never logged in. Rollback deletes exactly the records the import created
    (tracked per row in `ImportBatchItem`), nothing else. **Update-mode changes cannot be rolled back** (the audit log masks
    personal values, so the old values are not stored): a batch that updated any row is not rollback-able, and the preview
    warns about this before Confirm. A batch can be rolled back once; the batch is marked `rolled_back` and every deletion is
    in the audit log.
C3. **New fields**: `Guardian.whatsapp_consent` (unknown / yes / no, default unknown) and `Student.date_of_birth` (optional;
    Students template gets a `date_of_birth` column: ISO `2006-04-23`, `23-04-2006`, `23/04/2006` or an Excel date; not in the
    future, not under 3 years ago). **Consent guard**: one single sending function, `send_whatsapp(guardian, ...)`, is the only
    way to message a guardian; it raises `ConsentRequired` and sends nothing unless consent is exactly `yes`. The import can
    never set consent (a `whatsapp_consent` column is ignored with a warning); it stays `unknown` until the consent feature
    records it. Test: unknown, no, and missing all refuse (transport never called); yes passes; a test fails if any other
    module calls the transport directly.
C4. **Limit 2,000 rows per file** (Cloudflare request timeout). Background processing (Celery) is a noted follow-up for
    bigger colleges. Check 1 and 17 are updated accordingly: a 2,000-row students file validates and saves, each step within
    60 s on the CI runner (measured and written here once built).
C5. **Siblings**: one guardian can be linked to several students. Model: `Guardian` (the person: name, phone, email,
    whatsapp_consent; phone unique per college) and `StudentGuardian` (student, guardian, relation, is_primary; unique per
    pair; at most one primary per student). The Parents sheet keeps one row per student-guardian pair; if the phone already
    belongs to a guardian, that guardian is linked (a different name is a warning and is not overwritten unless update mode).
    Consent belongs to the person, so it is shared by all their children. Test with two siblings and one guardian.
C6. `ImportBatch.file_sha256` stores the SHA-256 of the uploaded file. The preview warns when the exact same file was already
    imported (showing when and by whom).
C7. **Roll numbers** are trimmed and unique case-insensitively (`2101A` = `2101a`), enforced by a database constraint on
    `lower(roll_number)`, not only by the import; the same rule applies to employee IDs and to the single-add forms. The value
    is stored as typed, trimmed.

Checks changed by the above: 1 (2,000 rows), 8 (as written), 9 (guardian links), 12 (college admin only, HoD and staff 403),
13 (also rollback), 16 (case-insensitive), 17 (2,000 rows), 19 (single-add forms get roll number, phone, DOB), plus new C2-C7.

## Decision (closed)
Option A was approved: role "1" is College admin (PR #5), role "4" Head of department exists with no pages and gets 403 everywhere (it cannot log in yet and sees a clear message).
