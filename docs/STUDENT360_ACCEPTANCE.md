# Student 360: design and acceptance checks (pilot item 3)

Status: PROPOSED. Nothing is built. Approve or change the checks below, then I build.

## What it is
One read-only page per student that gathers what the college already knows about them, so a college admin or
teacher answers "who is this student and how are they doing?" without opening five screens. It adds no new data
entry: every number comes from modules that exist (structure, import, attendance, marks).

URL: `/students/` (search list) and `/students/<id>/` (the 360 page).

## Sections on the page
1. **Identity**: name, roll number, email, phone, date of birth, gender, address, photo.
2. **Academics**: programme, batch, section, regulation, department; join date.
3. **Subjects this term**: each active enrolment (subject, faculty, core/elective, source), plus withdrawn ones in a
   collapsed "history" list.
4. **Attendance**: per-subject and overall percentage, status (OK / warning / shortage / condoned), "can still miss",
   on-duty awaiting review, active condonations. Uses the same `summarize()` as the student's own page, so the numbers
   can never disagree.
5. **Marks**: internal test and exam marks per subject (current `StudentResult`). CO-PO attainment is NOT shown
   (not built; waiting on the faculty answers).
6. **Guardians**: name, relation, primary flag, phone, and WhatsApp consent (unknown / yes / no).
7. **Alerts strip at the top**: attendance shortage or warning, on-duty awaiting review, guardian with no consent
   recorded, no guardian on file, no section assigned. Each alert links to where it is fixed.
8. **Recent changes** (college admin only): last 20 audit-log entries about this student, as the log stores them
   (personal fields stay masked).
9. **Fees**: a placeholder card "Fees module not built yet", so the page layout does not change when fees arrive.

## Who can see what
| Role | List | 360 page | Hidden from them |
|---|---|---|---|
| College admin | all students of the college | any student | nothing |
| Faculty (staff) | only students enrolled in their own subject offerings | those students only | date of birth, address, guardian phone numbers, recent changes |
| Student | none | only their own, at `/students/me/` | recent changes, alerts about guardian consent |
| Head of department | none (no pages yet) | 403 | everything |

A teacher sees the student's overall attendance (needed to judge a shortage) but marks only for their own
subjects. This is a deliberate choice, see Q2.

## Acceptance checks
Security and isolation (written first, as always)
1. A user from college B cannot open a college A student's page or find them in the list (tenant isolation test).
2. Every new URL has an explicit role decorator; the URL-walker test passes and pins the new pages.
3. Head of department and anonymous users get 403 / login redirect.
4. A teacher opening a student who is not in any of their offerings gets 403, not an empty page.
5. A teacher never receives date of birth, address, guardian phones, or recent changes: asserted on the rendered
   HTML, not only on the template.
6. A student can open only their own page; `/students/<other id>/` returns 403 for a student.
7. A teacher sees marks only for their own subjects; other subjects' marks are absent from the HTML.

Content
8. Identity, academics and guardians match the database for a fully populated student (golden test).
9. Subjects list shows active enrolments, and withdrawn ones only under history; a student who moved sections shows
   the old subjects as withdrawn with their attendance and marks still visible.
10. Attendance numbers on the 360 page equal the student's own attendance page and the shortage report for the same
    student (one test compares all three).
11. Each alert appears exactly when its condition holds and disappears when fixed: shortage, warning, pending
    on-duty review, no consent, no guardian, no section.
12. A student with no enrolments, no attendance, no marks and no guardians renders a clear empty state, no error.
13. Fees card is present and says the module is not built.

List and search (college admin, teacher)
14. Search by name, roll number or email, case-insensitive, with filters for programme, batch, section; 50 per page.
15. List shows an alert badge (shortage / warning) so the admin can scan for students at risk.
16. A search term is never interpolated into SQL or HTML unescaped (a test with quotes and `<script>`).

Quality
17. Opening a page for a student with 8 subjects and 150 classes uses a fixed number of queries, asserted (not per
    subject, not per class).
18. Page is read-only: no form, every non-GET request returns 405; nothing in the audit log changes by viewing.
19. Works on a phone-width screen (single column) and without JavaScript.
20. Existing student "My profile" and "My attendance" pages keep working; the 360 `/students/me/` links to them.

## Not in this feature
Editing from the 360 page (editing stays on the existing admin pages), photo upload, documents, fees, CO-PO
attainment, a timeline of events, parent login, print / PDF.

## Open questions (my recommendation first)
- Q1. **Log who looked at a student?** Recommend yes for college admin and teachers viewing a student (an
  access log, separate from the change log) because DPDP expects you to know who saw personal data. It adds one
  small model and a few tests. Say "no" if you want it later.
- Q2. **Teachers see overall attendance but only their own subjects' marks.** OK, or should teachers see only their
  own subjects for both?
- Q3. **Can a teacher open a student they once taught but whose enrolment is now withdrawn?** Recommend no (access
  follows active enrolment).
- Q4. **Alert wording for consent**: "WhatsApp consent not recorded" for unknown. Fine?
- Q5. **Student photo**: the field exists but the import never fills it. Show an initials avatar for now?
