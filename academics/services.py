"""Enrolment rules. Every change goes through save() so the audit log records it."""
import datetime

from django.db import transaction

from .models import Enrolment, SubjectOffering


def active_students_of(section):
    from main_app.models import Student
    return Student.objects.filter(section=section, admin__is_active=True)


@transaction.atomic
def enrol(student, offering, source=Enrolment.MANUAL, since=None):
    """Enrol (or re-activate) a student. Students outside the offering's section are allowed.

    Returns (enrolment, changed): changed is False when nothing needed doing.
    """
    enrolment = Enrolment.objects.filter(offering=offering, student=student).first()
    if enrolment is None:
        enrolment = Enrolment(offering=offering, student=student, source=source, enrolled_on=since or datetime.date.today())
        enrolment.save()
        return enrolment, True
    if enrolment.status == Enrolment.WITHDRAWN:
        enrolment.status = Enrolment.ACTIVE
        enrolment.withdrawn_on = None
        enrolment.source = source
        enrolment.save()
        return enrolment, True
    return enrolment, False


@transaction.atomic
def withdraw(enrolment):
    if enrolment.status == Enrolment.ACTIVE:
        enrolment.status = Enrolment.WITHDRAWN
        enrolment.withdrawn_on = datetime.date.today()
        enrolment.save()
        return True
    return False


@transaction.atomic
def enrol_section_in_core(offering):
    """A new CORE offering enrols every active student of its section. Electives enrol nobody."""
    if offering.kind != SubjectOffering.CORE:
        return 0
    # the section's students were in this class from the start of the term, so attendance can be marked from then
    since = min(datetime.date.today(), offering.term.start_date)
    return sum(1 for s in active_students_of(offering.section) if enrol(s, offering, Enrolment.AUTO, since=since)[1])


def current_core_offerings(section):
    today = datetime.date.today()
    return SubjectOffering.objects.filter(section=section, kind=SubjectOffering.CORE, term__end_date__gte=today)


@transaction.atomic
def sync_student_section(student, old_section_id):
    """Joining or moving to a section: leave the old section's offerings, join the new section's core ones.

    Withdrawn enrolments keep their rows, so attendance and marks stay attached.
    """
    if old_section_id and old_section_id != student.section_id:
        stale = Enrolment.objects.filter(
            student=student, status=Enrolment.ACTIVE, offering__section_id=old_section_id)
        for enrolment in stale:
            withdraw(enrolment)
    if student.section_id and student.admin.is_active:
        source = Enrolment.TRANSFER if old_section_id else Enrolment.AUTO
        for offering in current_core_offerings(student.section):
            enrol(student, offering, source)


class ImportError_(Exception):
    """Raised with a list of row problems; nothing is saved."""

    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = problems


@transaction.atomic
def import_electives(offering, file):
    """Bulk-assign students to an elective from an .xlsx list (column 'email'). All or nothing."""
    from openpyxl import load_workbook

    from main_app.models import Student

    try:
        sheet = load_workbook(file, read_only=True, data_only=True).active
    except Exception as exc:  # unreadable or not an xlsx
        raise ImportError_(['The file is not a valid .xlsx workbook.']) from exc
    rows = list(sheet.iter_rows(values_only=True))
    if not rows or not rows[0]:
        raise ImportError_(['The sheet is empty.'])
    header = [str(c).strip().lower() if c is not None else '' for c in rows[0]]
    if 'email' not in header:
        raise ImportError_(["The first row must contain a column named 'email'."])
    col = header.index('email')
    problems, students = [], []
    for number, row in enumerate(rows[1:], start=2):
        value = row[col] if col < len(row) else None
        if value is None or str(value).strip() == '':
            continue
        email = str(value).strip().lower()
        student = Student.objects.filter(admin__email__iexact=email).first()
        if student is None:
            problems.append(f'Row {number}: no student with email {email}.')
        else:
            students.append(student)
    if problems:
        raise ImportError_(problems)
    if not students:
        raise ImportError_(['No student emails found.'])
    return sum(1 for s in students if enrol(s, offering, Enrolment.IMPORT)[1])
