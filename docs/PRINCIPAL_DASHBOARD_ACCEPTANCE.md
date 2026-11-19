# Pilot item 8: principal dashboard. Acceptance checks

**Status: DRAFT, waiting for approval. Nothing is built.** Written 2026-11-19. Build starts only after you approve these checks
and answer the open questions at the end (same rule as every feature).

## What it is

One page that answers, in under a minute, "is my college running and where is it slipping?", using only data the pilot already
has: students, staff, attendance, the 75% rule, WhatsApp alerts and the import and consent state. It is read-only. It does **not**
show fees, marks or CO-PO, because those are not built; those cards say "Not available yet" and never show made-up numbers.

## Who sees it

A new role, **Management / Principal** (read-only), created by the college admin. The college admin sees the same page. Teachers
and students do not (403). A head of department's own-department view is a later item, not this one.

## The page

| Block | Shows | Source (one place, so it matches the reports) |
|---|---|---|
| Headline | students, staff, programmes, sections | academics and main_app |
| Attendance today | classes recorded today, overall present %, sections with no class recorded today | `main_app.Attendance`, `AttendanceReport` |
| Shortage and warning | number of students OK / on warning / in shortage, by programme and by section, each number linking to the existing shortage report | `attendance.services.summarize`, `AttendanceRule` of the regulation |
| 30-day trend | attendance % per day per programme (weekdays with at least one class) | same |
| Parent alerts | yesterday's WhatsApp messages sent / delivered / failed / skipped for no consent; share of guardians with consent yes / no / unknown | `messaging` report services |
| Data health | students without a section, without a guardian, without a consent answer; on-duty entries waiting for review | existing alert queries from Student 360 |
| Fees, marks, CO-PO | "Not available yet" | none |

## Acceptance checks

**Access and safety**
1. A Management user can open the dashboard and every link on it; every page they can reach is read-only (a test posts to every
   add/edit/delete URL as Management and expects 403 and no change in the database).
2. Teacher and student get 403; a visitor who is not logged in goes to the login page; the URL-policy test lists the new view.
3. College A's principal never sees College B's numbers (tenant-isolation test with two colleges and different data).
4. Opening a student from the dashboard is recorded in the Student 360 access log, exactly as for the college admin.
5. The Management role can be created, changed and deactivated by the college admin only, and each change is in the audit log.

**Correct numbers**
6. A golden example: a small college built in the test (known present/absent/on-duty counts, one student in shortage, one on
   warning, one condoned) gives exactly the expected figures in every block.
7. Every shortage number on the dashboard equals the count on the shortage report page for the same filters (reconciliation test).
8. Thresholds, the per-subject or overall basis, condonation and on-duty come from `AttendanceRule`; nothing is hard-coded, and a
   test changes the rule and sees the dashboard change.
9. "Sections with no class recorded today" ignores sections with no offerings, and the card says plainly that without a timetable
   it cannot know about holidays.
10. A student who withdrew from a subject, or joined mid-term, is counted the same way as in the shortage report.
11. Each block shows "as of <date and time>" and a "how this is calculated" line a principal can read.

**Empty and edge cases**
12. A brand-new college (no students, no attendance, no WhatsApp setup) shows zeros and a short "get started" hint; no crash, no
    divide by zero, no empty chart errors.
13. WhatsApp off, or in dry-run mode, shows "off" or "dry run" instead of zeros that look like failures.
14. Filters for date range, programme and section work and are remembered per user; an invalid or inverted range is refused with a message.

**Speed and size**
15. The number of database queries is the same for a 10-student college and a 3,000-student college (query-count test), and the page
    renders in under one second on the 3,000-student test data. Numbers come from grouped queries, never a loop per student.
16. No page element makes a call outside our own origin except the chart library already allowed by the Content-Security-Policy.

**Use on a phone and on paper**
17. Readable at 360 px width with no sideways scrolling; the headline blocks stack in this order: shortage, attendance today,
    parent alerts.
18. A print stylesheet gives a clean one-page A4 summary (checked in a real browser), and a button downloads the shortage table
    and the 30-day trend as Excel; each download is written to the audit log.

**Done means**
19. Tests for 1 to 16 pass in CI; 17 and 18 checked by eye in a real browser with notes in the PR; `docs/DEMO_SCRIPT.md` gets a
    minute for the dashboard; CLAUDE.md status updated.

## Open questions (your answers decide the build)

1. **Role name and creation:** "Management / Principal" as one role, created by the college admin: OK? Or should a principal be
   able to invite a vice-principal?
2. **Student detail:** may Management open a student page (names, guardians' phone numbers, consent), or only see numbers and lists
   of names? Phone numbers of parents are the most sensitive field we hold.
3. **"Attendance today" without a timetable:** is "sections where no class was recorded today" useful enough, or would the principal
   rather see "classes recorded today vs yesterday"? (A proper "classes missed" needs the timetable, which is not in the pilot.)
4. **Which five numbers would your first principal want on top?** Ask one real principal before we build; the order of blocks
   follows their answer.
5. **Excel or PDF:** is Excel plus a printable page enough for the pilot, or do you need a PDF file generated?
6. **Academic year or term switch:** default to the current term only, with a dropdown for earlier terms later?
