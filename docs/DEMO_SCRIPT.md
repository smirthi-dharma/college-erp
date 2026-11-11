# Demo script: 10 minutes for a principal

Every click below was run end to end on a fresh `seed_demo` college on 2026-11-11 (menus, shortage figures, the dry-run message
text); the screens were exercised through Django's test client, not by eye in a browser, so give it one real run-through first. Demo data only: all names, numbers and phone numbers are made up.

## Before you start (5 minutes, once)
```bash
python manage.py migrate_schemas --shared && python manage.py migrate_schemas   # database up to date
python manage.py create_demo_college          # skip if it exists; users admin@demo.in, staff@demo.in, student@demo.in
python manage.py seed_demo                    # programme, section, 2 subjects, 10 students, 9 guardians, 10 days of attendance
python manage.py runserver                    # then open http://demo.localhost:8000/
```
Password for every demo login: `demo-pass-123` (printed by `create_demo_college`; staging uses the one `create_college` printed).
Use two browser windows, one normal and one private, so the teacher and the admin can be open at once.
Logins: **admin@demo.in** (college admin), **staff@demo.in** (the teacher of both subjects), **student04@demo.in** (Karthik).

## The ten minutes
| Min | Do | Say |
|---|---|---|
| 0:00 | Log in as **admin**. Show the left menu. | "One platform; every college's data sits in its own separate database section. Roles see only their own menu." |
| 1:00 | **Institution structure** (`/structure/`): Departments, Programmes, Batches, Sections, Subject offerings. Then **Bulk import**: show the Excel templates, do not upload. | "Set up once, or import your students, parents and staff from Excel. You see a preview and nothing is saved until you confirm." |
| 3:00 | **Student 360** (`/students/`): type `Karthik`. Point at the red SHORTAGE badge, open his page. Show attendance by subject (both near 50%), the alert strip, the guardians with their consent. | "One page per student. Karthik is at about 50%, below the 75% rule, and the rule is a setting per university regulation, not fixed in code. Every time someone opens a student, it is logged." |
| 5:00 | Switch to the private window, log in as **staff**. **Attendance** (`/attendance/`): pick *Programming in Python*, today. Tap **Aarthi Kumar**, **Arjun Kumar** (brother and sister) and **Karthik Subramanian** as absent, submit. | "Everyone starts as present; the teacher taps only the absentees and submits once. That is the 30-second class." |
| 6:30 | Back as **admin**: **Attendance shortage** (`/attendance/shortage/`). Show Karthik: Mathematics 10 held, 5 attended (50%); Programming 11 held, 6 attended (54.55%); can still miss 0. Click the Excel download. | "The early warning, before the exam eligibility problem, not after." |
| 7:30 | **WhatsApp digest** (`/messaging/`): off by default and in dry run. Tick **Enabled**, keep **dry run**, Save. In a terminal: `python manage.py run_digest --schema demo --ignore-time`. Open **Daily report**. | "Parents get one message a day listing every absent child, so the two siblings share one message. Nothing is sent without the parent's yes, and today this is a rehearsal: it shows exactly what would go out." |
| 8:30 | In the report: **Mr. Kumar**'s row is one message naming both children ("Aarthi Kumar: P1 Programming in Python; Arjun Kumar: P1 ..."); **Mr. Subramanian** (Karthik's father) shows as skipped, no consent. Then open **Guardian consent**: his answer is Unknown. | "Two absent siblings, one message. Karthik's father never gave consent, so he is not messaged. Consent is recorded with who, when and how." |
| 9:00 | Private window: log in as **student04**. **Attendance summary**; then change the number in `/students/<id>/` to another student. | "A student sees only their own data. Asking for someone else's page is refused, and the attempt is logged." |
| 9:30 | Close. | "Your data stays in India, one college can never see another, every change is in an audit log, and each college's secrets are encrypted." |

## Do not claim (not built or not proven)
- **Fees and online payment**, **CO-PO attainment**, a real **principal dashboard** (the admin home page shows a few inherited
  counts; do not call it that), exams/results, timetable, admissions.
- **Real WhatsApp messages**: not yet tried on Meta's test number, and Tamil wording is unreviewed. Say "rehearsal / dry run".
- **Speed on a phone**: the 30-second figure is the design target; it has not been timed on a real Android phone.
- Staging: if you demo from staging rather than your laptop, it exists only after the steps in `docs/STAGING_SETUP.md`.

## Run it again
The attendance and digest steps work once per day (a day's digest is never sent twice, even as a dry run). To start over, rebuild
the demo college (this deletes ONLY the demo college's data; never run it against a real college):
```bash
python manage.py shell -c "from colleges.models import College; College.objects.get(schema_name='demo').delete(force_drop=True)"
python manage.py create_demo_college && python manage.py seed_demo
```
