# Fast teacher attendance + 75% shortage warning (pilot item 4): design and acceptance checks

Status: BUILT (branch `feat/fast-attendance`). Approved 2026-11-09 (defaults: on-duty counts as present, 3-day edit window, warn <80% / shortage <75%, all to be confirmed with faculty) plus changes A1-A5 below. Building. QR / geofence attendance is dropped from the pilot (Smirthi, 2026-11-09): a rotating
code can be screenshotted to a friend, needs a working phone for every student, and GPS can be faked. Teacher-marked
attendance meets the real goal: under 30 seconds per class on a cheap Android phone.

## What exists today (and what is wrong with it)
Five steps of JavaScript (pick subject, press fetch, type a date, tick boxes, save), then a full page reload. Nothing stops
the same class being saved twice for one date, two classes on one day cannot be told apart, a student is only present or
absent, the percentage is never shown or warned about, and every teacher edit after the fact is allowed forever.

## Design

**Taking attendance (teacher, phone first).** "Take attendance" shows the teacher's own classes as big buttons (today's
date is implicit; most recently used first). One tap opens the class list: students sorted by roll number, **everyone
pre-set to present**, one large tap target per student to mark absent (a second tap marks on-duty, a third returns to
present). A sticky "Submit (3 absent)" button. One POST. All-present class = 2 taps (open class, submit); each absentee adds
one tap. The page is server-rendered with a few lines of plain JavaScript (no jQuery AJAX chain) and also works as an
ordinary form if JavaScript fails.

**Data (migration, tenant schema).**
- `Attendance` (one class held): add `period` (1..8, default 1, for two classes of one offering in a day) and `taken_by`;
  unique (offering, date, period).
- `AttendanceReport.status`: boolean becomes `present` / `absent` / `on_duty`; unique (attendance, student). Legacy rows
  map True -> present, False -> absent; legacy duplicate classes get the next free period and duplicate reports keep the
  newest (counted and printed by the migration).
- `AttendanceRule` (configuration, one per Regulation, created with defaults when missing): `required_percent` (75),
  `warning_percent` (80, early warning band), `on_duty_counts_as_present` (yes), `edit_window_days` (3).
  Nothing about 75% is hard-coded in code.

**Who is on the list for a date.** Students whose enrolment covers that date (`enrolled_on` <= date, and not withdrawn on or
before it), in any section (so arrear students appear), so a student who joined later is not marked absent for earlier classes.

**Percentages.** Per student per offering: (present + on-duty if counted) / classes held. Overall per student across offerings.
Status: OK (>= warning), WARNING (< warning, >= required), SHORTAGE (< required). Also "can still miss N classes" =
floor(attended / required_fraction - held) (never negative). Limit: no timetable yet, so "held" means classes recorded so far.

**Where it shows.** Teacher: after submitting, the students now in WARNING/SHORTAGE for that class; a class summary page.
Student: percentage per subject and overall on the dashboard with a banner when WARNING/SHORTAGE. College admin: shortage
report by section/offering with an Excel download. A service function `shortage_students()` is the single source for the
WhatsApp digest (pilot item 7, not built here).

**Editing.** A teacher can correct their own class for `edit_window_days` after the date (default 3); after that only the
college admin can. Future dates are refused; dates outside the offering's term are refused. Every change is audited.

## Acceptance checks

Speed (measurable proxies for "under 30 seconds on a cheap phone")
1. All-present class: 2 interactions (open the class, submit). Each absentee adds exactly 1. Tested on the rendered page
   (the list is pre-set to present; the form needs no typed input).
2. The class page is server-rendered with a fixed number of database queries (<= 14 real queries, not growing with class size;
   tested with 2 and 150 students). Submitting runs no per-student reads (the writes are per changed student because each is audited):
   150 students submit in about 0.06 s locally (644 queries, 13 reads), asserted under 5 s on CI.
3. The class page is under 100 KB for 150 students and loads no jQuery/AJAX chain; the form works with JavaScript disabled.

Correctness
4. A class (offering, date, period) can be saved once; saving again updates it (no duplicates) and a double tap is harmless
   (database unique constraints, not only code).
5. Two periods of one offering on the same day are two separate classes.
6. The list is exactly the students enrolled on that date (arrear students included; withdrawn and not-yet-enrolled excluded);
   submitting a student who is not on that list is refused.
7. Statuses are present / absent / on-duty; on-duty counts as present by default and by the Regulation's rule.
8. Future dates and dates outside the term are refused; the teacher edit window is enforced and then only the college admin
   may edit.
9. Percentages and the OK / WARNING / SHORTAGE bands follow the Regulation's `AttendanceRule`; changing a rule changes the
   result with no code change; exact boundaries (74.99%, 75%, 79.99%, 80%) are tested with a golden table.
10. "Can still miss N" is correct and never negative; zero classes held gives no percentage and no warning.

Permissions, audit, isolation
11. Only the teacher of the offering can take or edit it; other teachers get 403; students and the Head-of-department role get 403;
    the college admin can view and edit any class (audited). Every endpoint has an explicit role check (the URL walker test).
12. A student sees only their own percentages; one student cannot read another's (tested).
13. Every attendance create/update/delete is in the audit log with the actor; an edit that changes nothing writes nothing.
14. College A's attendance, rules and reports are invisible to College B (tenant tests).
15. POST + CSRF for every change; no state change over GET.

Migration and regression
16. The migration converts a legacy dataset (booleans, duplicate classes, duplicate reports) without losing a mark or changing
    any student's present/absent counts (tested like the academics migration).
17. The old multi-step pages and their JSON endpoints are removed (no dead routes); the student date-range view keeps working.
18. All 216 existing tests (updated where they touch attendance), ruff, bandit, pip-audit, `check --deploy` and the secret scan pass;
    new tests are mutation-checked (break the unique constraint, the enrolment-date filter, the edit window, the 75% rule).

Out of scope here: WhatsApp absence digest (item 7), timetable and holidays, offline marking, leave-request approval linking
to attendance, biometric or QR check-in, a mobile app (the page is mobile-first web).

## Changes approved 2026-11-09 (these add to or replace the checks above)

A1. **Per-subject vs overall.** `AttendanceRule.basis` = `per_subject` (default) or `overall`. Per-subject: the OK/WARNING/SHORTAGE
    status is computed for each offering separately, and a student is in shortage if any subject is. Overall: one percentage across
    all of the student's offerings. Reports, the student page and `shortage_students()` all follow the setting. (Check 9 is run for both.)
A2. **Condonable band and Condonation record.** `AttendanceRule.condonable_percent` (default 65; set equal to the required
    percentage to disable). A student below the required percentage but at or above the condonable percentage may be condoned.
    A `Condonation` (student, subject or overall, reason, percentage at approval, approved by, approved at; revoked by/at) can be
    created **only by the college admin**, only while the student is inside the band for that scope, once at a time per scope
    (database constraint), and is revocable. A condoned student shows as CONDONED (not SHORTAGE) with the record visible; nobody
    below the band can be condoned. Creating and revoking are audited. Teachers cannot see or create condonations.
A3. **On-duty needs review.** On-duty marked by a teacher is flagged `needs_review`; the college admin has a review queue
    (approve keeps it; reject turns it into absent). While pending it counts as present (provisional) and shows a flag; rejected
    ones count as absent. On-duty marked by the college admin needs no review. Every review decision is audited.
A4. **A failed submit loses nothing and a retry cannot duplicate.** The page sends a one-time submission key with the form. If the
    save fails (server error, validation error, lost connection) nothing is half-saved (one transaction) and the page re-renders
    with every tap preserved; in the browser the taps are also kept as a local draft until the save succeeds. Resubmitting with the
    same key, or double-tapping, saves exactly once (unique constraints plus the key; tested by retrying after a forced failure
    and by submitting twice). The browser draft is verified by hand, not by a unit test.
A5. **Substitute for one class on one date.** The college admin can assign a substitute teacher for one offering on one date
    (and period). For that class only the substitute may take or edit it: the regular teacher gets 403 for it, other teachers 403, the
    college admin can still edit (audited). The substitute sees that one class on that one date and nothing else of the offering
    (not other dates, not marks). The assignment is audited and removable by the college admin.

## Decisions (closed)
Defaults approved; the 75% / 80% / 65% figures and the 3-day window are settings to confirm with faculty later.

## Built (summary)
New app `attendance` (rules, condonations, substitutes) and changes to `main_app` attendance models (migrations 0010-0012, tested on
legacy-shaped data). Pages: `/attendance/` (take, mobile-first), `/attendance/class/<id>/`, summary, shortage report (+ Excel),
on-duty review, condonation, substitutes, rules, and `/attendance/mine/` for students plus a banner on the student home page.
The old multi-step teacher pages and JSON endpoints were removed. 56 new tests, mutation-checked (edit window, substitute block,
75% rule, condonation band, roster dates, on-duty review). The browser-side draft (taps kept in local storage after a failed save)
is verified by hand, not by a unit test. `Enrolment.enrolled_on` is now a settable date (automatic enrolments of a new offering start
at the term start) so a class list can be reconstructed for a past date.
