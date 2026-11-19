# Pilot item 8: principal dashboard. Acceptance checks

**Status: DRAFT 2, answers incorporated 2026-11-19. Nothing is built. Waiting for your "go" to start building.**
The five tiles are a draft until a real principal confirms them (see "Open points").

## What it is

One page that answers, in under a minute, "is the college running and where is it slipping?", from data the pilot already
has. It is read-only. Fees are not built, so that tile says "Not available yet" and never shows made-up numbers.

## Decisions already made (your answers)

1. A principal can open the **full Student 360** of any student, read-only, and every such view is written to the access log.
2. The attendance tile says plainly **"no attendance recorded today"** where that is the case.
3. **Five tiles**: attendance today, shortage count, sections not marked, guardian consent coverage, fees placeholder.
4. **Excel and a printable page**; no generated PDF file for the pilot.
5. **One role, "Management / Principal", with many users**; the college admin creates, edits and deactivates them.
6. The page shows the **current term by default**, with a **term picker** to look at an earlier or a later term.

## Who sees it

Management / Principal users and the college admin. Teachers and students get 403. The role is read-only: it can open the
dashboard, Student 360 and the existing report pages that only read, and nothing that adds, edits or deletes (HoD views by
department are a later item).

## The five tiles (draft)

| Tile | Shows | How it is worked out |
|---|---|---|
| Attendance today | classes recorded today, present % across them (on-duty counts as present); the words "no attendance recorded today" when there are none | `Attendance` and `AttendanceReport` for today, subjects of the chosen term |
| Shortage count | students OK / on warning / in shortage, and a list link to the existing shortage report | `attendance.services.summarize` with each regulation's `AttendanceRule`, subjects of the chosen term |
| Sections not marked | sections that have a subject this term but no class recorded today, with the teacher's name; a section with no subject is not listed | `SubjectOffering` against today's `Attendance` |
| Guardian consent coverage | share of guardians with consent yes / no / not asked, by programme | `Guardian.whatsapp_consent` |
| Fees | "Not available yet" | none |

Without a timetable the page cannot know about holidays: on a day with no classes every section shows as not marked. The tile
says so in one line.

## Acceptance checks

**Access and safety**
1. A Management user opens the dashboard and every link on it. A test posts to every add, edit and delete URL in the application as
   Management and expects 403 and no change in the database (a list built from the URL table, so a new write page cannot be missed).
2. Teacher and student get 403; a visitor who is not logged in goes to the login page; the URL-policy test lists the new view.
3. College A's principal never sees College B's numbers (two colleges with different data).
4. Management opens a student's full Student 360, read-only; every view is in the Student 360 access log with the viewer's role,
   and a refused attempt is logged as refused (as for the college admin).
5. The college admin can create several Management users, edit and deactivate them; each change is in the audit log; a
   deactivated user cannot log in; Management cannot create or change users.

**Correct numbers**
6. A golden example: a small college built in the test (known present, absent and on-duty counts; one student in shortage, one on
   warning, one condoned) gives exactly the expected figures in all five tiles.
7. The shortage numbers equal the counts on the shortage report page for the same term (reconciliation test).
8. Thresholds, per-subject or overall basis, condonation and on-duty come from `AttendanceRule`; a test changes the rule and the
   tile changes with it.
9. "Sections not marked" lists exactly the sections with a subject this term and no class recorded today, names the teacher, and
   ignores sections without subjects; marking a class removes the section from the list.
10. A student who withdrew from a subject or joined mid-term is counted as in the shortage report.
11. Consent coverage counts each guardian once, even when linked to several children, and matches the consent screen.
12. Each tile shows "as of <date and time>" and a one-line "how this is calculated".

**Term picker and filters**
13. The current term is selected by default (the term whose dates contain today; if none does, the latest term). Picking another
    term changes every tile; "today" tiles still mean today and say so. The choice is remembered per user. An invalid term id is refused.

**Empty cases**
14. A brand-new college (no students, no attendance, no terms) shows zeros, "no attendance recorded today", and a short get-started
    hint; no crash, no divide by zero.

**Speed and size**
15. The number of database queries is the same for a 10-student and a 3,000-student college, and the page renders in under one
    second on the 3,000-student test data (grouped queries, never a loop per student).
16. The page loads nothing from outside our own origin except what the Content-Security-Policy already allows.

**Phone, paper and Excel**
17. Readable at 360 px width with no sideways scrolling; tiles stack in this order: sections not marked, attendance today,
    shortage, consent, fees.
18. A print stylesheet gives a clean one-page A4 summary (checked by eye in a real browser).
19. An "Export to Excel" button downloads one workbook: the tiles, the sections-not-marked list, the shortage list and the consent
    coverage table, for the chosen term. Formula-looking cell text is neutralised (as in the import module), and every download
    is written to the audit log with who and which term.

**Done means**
20. Tests for 1 to 16 and 19 pass in CI; 17 and 18 checked by eye in a real browser with notes in the PR; `docs/DEMO_SCRIPT.md`
    gets a minute for the dashboard; CLAUDE.md status updated.

## Open points (small; answer when you are ready, or let the principal decide)

1. **Holidays:** keep the one-line note as is, or add a "no classes today" switch the college admin can set (more useful, but one
   more screen and a rule to test)? Default if you say nothing: the note only.
2. **Parents' phone numbers:** a principal opening Student 360 sees guardians' phones and consent, as the college admin does, and every
   view is logged (your answer 1). If a college prefers, a per-college setting could hide phone numbers from Management later.
3. **The five tiles:** the order and the wording follow what a real principal says. If they ask for different tiles, this table
   changes before anything is built.
