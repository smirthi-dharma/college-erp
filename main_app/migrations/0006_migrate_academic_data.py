"""Step 2 of 3: convert Course / Session / Subject rows into the academics structure.

Mapping (nothing is deleted here):
  Course   -> Department + Programme + Regulation 'Legacy'
  Session  -> Batch (by start year) per programme, one Section 'A' per batch
  Subject  -> Subject (semester 1, 0 credits, code LEG<id>) + one core SubjectOffering per section of its course
  Student  -> section; every student is enrolled in the core offerings of the section
  Attendance / StudentResult -> the offering of the matching section
Colleges review and correct the placeholders (semester, credits, codes) afterwards.
"""
import datetime

from django.db import migrations


def forwards(apps, schema_editor):
    Course = apps.get_model('main_app', 'Course')
    Session = apps.get_model('main_app', 'Session')
    Subject = apps.get_model('main_app', 'Subject')
    Student = apps.get_model('main_app', 'Student')
    Staff = apps.get_model('main_app', 'Staff')
    Attendance = apps.get_model('main_app', 'Attendance')
    StudentResult = apps.get_model('main_app', 'StudentResult')
    Department = apps.get_model('academics', 'Department')
    Programme = apps.get_model('academics', 'Programme')
    Regulation = apps.get_model('academics', 'Regulation')
    Batch = apps.get_model('academics', 'Batch')
    Section = apps.get_model('academics', 'Section')
    NewSubject = apps.get_model('academics', 'Subject')
    Term = apps.get_model('academics', 'AcademicTerm')
    Offering = apps.get_model('academics', 'SubjectOffering')
    Enrolment = apps.get_model('academics', 'Enrolment')

    if not (Course.objects.exists() or Session.objects.exists() or Subject.objects.exists()):
        return

    today = datetime.date.today()
    sessions = list(Session.objects.all())
    term = Term.objects.create(
        name='Legacy (migrated)',
        start_date=min([s.start_year for s in sessions] or [today]),
        end_date=max([s.end_year for s in sessions] + [today]),
    )

    programme_of, regulation_of, department_of = {}, {}, {}
    for course in Course.objects.all():
        dept = Department.objects.create(name=course.name, code=f'D{course.pk}')
        prog = Programme.objects.create(department=dept, name=course.name, code=f'P{course.pk}', duration_semesters=8)
        reg = Regulation.objects.create(programme=prog, name='Legacy', effective_from=today)
        department_of[course.pk], programme_of[course.pk], regulation_of[course.pk] = dept, prog, reg

    section_cache = {}

    def section_for(course_id, session):
        key = (course_id, session.pk if session else None)
        if key not in section_cache:
            year = session.start_year.year if session else today.year
            batch, _ = Batch.objects.get_or_create(
                programme=programme_of[course_id], start_year=year,
                defaults={'regulation': regulation_of[course_id]})
            section, _ = Section.objects.get_or_create(batch=batch, name='A')
            section_cache[key] = section
        return section_cache[key]

    # Students: section from (course, session)
    for student in Student.objects.exclude(course__isnull=True):
        student.section = section_for(student.course_id, student.session)
        student.save(update_fields=['section'])
    for staff in Staff.objects.exclude(course__isnull=True):
        staff.department = department_of[staff.course_id]
        staff.save(update_fields=['department'])

    # Subjects and offerings (one per section that exists for the course, created on demand)
    new_subject_of = {}
    for subject in Subject.objects.all():
        new_subject_of[subject.pk] = NewSubject.objects.create(
            programme=programme_of[subject.course_id], regulation=regulation_of[subject.course_id],
            semester=1, code=f'LEG{subject.pk}', name=subject.name, credits=0, kind='theory')

    offering_cache = {}

    def offering_for(subject, section):
        key = (subject.pk, section.pk)
        if key not in offering_cache:
            offering, _ = Offering.objects.get_or_create(
                subject=new_subject_of[subject.pk], section=section, term=term,
                defaults={'faculty_id': subject.staff_id, 'kind': 'core'})
            offering_cache[key] = offering
        return offering_cache[key]

    # Offerings for every section that has students of the course
    for subject in Subject.objects.all():
        for section_id in Student.objects.filter(course_id=subject.course_id).exclude(section__isnull=True) \
                .values_list('section_id', flat=True).distinct():
            offering_for(subject, Section.objects.get(pk=section_id))

    # Attendance: offering of (subject, the section of that session)
    for attendance in Attendance.objects.all():
        subject = Subject.objects.get(pk=attendance.subject_id)
        section = section_for(subject.course_id, Session.objects.get(pk=attendance.session_id))
        attendance.offering = offering_for(subject, section)
        attendance.save(update_fields=['offering'])

    # Results: offering of (subject, the student's section)
    for result in StudentResult.objects.all():
        subject = Subject.objects.get(pk=result.subject_id)
        student = Student.objects.get(pk=result.student_id)
        section = Section.objects.get(pk=student.section_id) if student.section_id else section_for(subject.course_id, None)
        result.offering = offering_for(subject, section)
        result.save(update_fields=['offering'])

    # Enrolments: all students of a section in its core offerings, plus anyone with attendance or results there
    for offering in Offering.objects.all():
        for student in Student.objects.filter(section_id=offering.section_id):
            Enrolment.objects.get_or_create(offering=offering, student=student, defaults={'source': 'auto'})
    for report in apps.get_model('main_app', 'AttendanceReport').objects.select_related('attendance'):
        Enrolment.objects.get_or_create(
            offering_id=report.attendance.offering_id, student_id=report.student_id, defaults={'source': 'auto'})
    for result in StudentResult.objects.all():
        Enrolment.objects.get_or_create(
            offering_id=result.offering_id, student_id=result.student_id, defaults={'source': 'auto'})


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0001_initial'),
        ('main_app', '0005_academic_links'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
