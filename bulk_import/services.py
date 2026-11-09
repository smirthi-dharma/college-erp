"""Confirming an import, rolling it back, and the spreadsheets we hand out (templates, error sheets)."""
import io

from django.db import transaction
from django.utils import timezone
from openpyxl import Workbook

from academics.models import Department, Enrolment, Section
from main_app.models import (
    AttendanceReport,
    CustomUser,
    FeedbackStaff,
    FeedbackStudent,
    Guardian,
    LeaveReportStaff,
    LeaveReportStudent,
    NotificationStaff,
    NotificationStudent,
    StudentGuardian,
    StudentResult,
)

from .importers import IMPORTERS, apply_plan
from .models import ImportBatch
from .parsing import FORMULA_STARTS

# --- confirming -------------------------------------------------------------------------------------------------------


@transaction.atomic
def confirm(kind, update_existing, plan, file_name, file_sha256, user):
    importer = IMPORTERS[kind](update_existing)
    refs, created, updated, unchanged = apply_plan(importer, plan)
    return ImportBatch.objects.create(
        kind=kind, file_name=file_name[:255], file_sha256=file_sha256, update_existing=update_existing,
        rows=len(plan.actions), created=created, updated=updated, unchanged=unchanged,
        created_refs=refs, imported_by=user)


# --- rollback ---------------------------------------------------------------------------------------------------------

# Other apps register a function here (user_ids -> list of reasons) when they start referring to students or staff,
# e.g. the fees module. Rollback is refused while any registered blocker returns a reason.
ROLLBACK_BLOCKERS = []


def _users_blockers(users, student_side):
    reasons = []
    if users.filter(last_login__isnull=False).exists():
        reasons.append('someone imported by this file has already logged in')
    ids = list(users.values_list('pk', flat=True))
    if student_side:
        if AttendanceReport.objects.filter(student__admin_id__in=ids).exists():
            reasons.append('attendance has been recorded for these students')
        if StudentResult.objects.filter(student__admin_id__in=ids).exists():
            reasons.append('marks have been entered for these students')
        if Enrolment.objects.filter(student__admin_id__in=ids).exclude(source__in=[Enrolment.AUTO, Enrolment.TRANSFER]).exists():
            reasons.append('students were enrolled by hand after the import')
        if StudentGuardian.objects.filter(student__admin_id__in=ids).exists():
            reasons.append('parents are linked to these students (roll back the parents import first)')
        if (LeaveReportStudent.objects.filter(student__admin_id__in=ids).exists()
                or FeedbackStudent.objects.filter(student__admin_id__in=ids).exists()
                or NotificationStudent.objects.filter(student__admin_id__in=ids).exists()):
            reasons.append('these students have leave requests, feedback or notifications')
    else:
        from academics.models import SubjectOffering
        if SubjectOffering.objects.filter(faculty__admin_id__in=ids).exists():
            reasons.append('these staff teach subject offerings')
        if (LeaveReportStaff.objects.filter(staff__admin_id__in=ids).exists()
                or FeedbackStaff.objects.filter(staff__admin_id__in=ids).exists()
                or NotificationStaff.objects.filter(staff__admin_id__in=ids).exists()):
            reasons.append('these staff have leave requests, feedback or notifications')
    for blocker in ROLLBACK_BLOCKERS:
        reasons.extend(blocker(ids))
    return reasons


def rollback_blockers(batch):
    """Reasons this batch cannot be rolled back (empty list = it can)."""
    if batch.status != ImportBatch.APPLIED:
        return ['it was already rolled back']
    if batch.updated:
        return ['it updated existing records and their old values are not stored (updates cannot be undone)']
    refs = batch.created_refs
    if batch.kind in (ImportBatch.STUDENTS, ImportBatch.STAFF):
        users = CustomUser.objects.filter(pk__in=refs.get('users', []))
        return _users_blockers(users, student_side=batch.kind == ImportBatch.STUDENTS)
    reasons = []
    if Guardian.objects.filter(pk__in=refs.get('guardians', [])).exclude(whatsapp_consent=Guardian.CONSENT_UNKNOWN).exists():
        reasons.append('a consent answer has been recorded for these guardians')
    return reasons


@transaction.atomic
def rollback(batch, user):
    """Delete exactly what the import created (each delete goes through the audit log). Returns the blockers if refused."""
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    reasons = rollback_blockers(batch)
    if reasons:
        return reasons
    refs = batch.created_refs
    if batch.kind == ImportBatch.PARENTS:
        for link in StudentGuardian.objects.filter(pk__in=refs.get('links', [])):
            link.delete()
        for guardian in Guardian.objects.filter(pk__in=refs.get('guardians', [])):
            if not StudentGuardian.objects.filter(guardian=guardian).exists():  # still shared with other children: keep
                guardian.delete()
    else:
        for person in CustomUser.objects.filter(pk__in=refs.get('users', [])):
            person.delete()  # cascades the role profile and automatic enrolments
    batch.status = ImportBatch.ROLLED_BACK
    batch.rolled_back_by = user
    batch.rolled_back_at = timezone.now()
    batch.save()
    return []


# --- spreadsheets we produce ------------------------------------------------------------------------------------------

def safe_cell(value):
    """Text that starts like a formula would be run by Excel; prefix it so it is shown as text."""
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def _to_bytes(workbook):
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def error_sheet(problems):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Problems'
    ws.append(['Row', 'Column', 'Problem'])
    for row, column, message in problems:
        ws.append([row, safe_cell(column), safe_cell(message)])
    return _to_bytes(wb)


TEMPLATES = {
    'students': (['roll_number', 'first_name', 'last_name', 'email', 'gender', 'phone', 'programme_code',
                  'batch_start_year', 'section', 'date_of_birth', 'address'],
                 ['21CS001', 'Anita', 'Kumar', 'anita.kumar@example.com', 'F', '9876543210', 'CSE', 2025, 'A', '2006-04-23', '1 Main Road']),
    'parents': (['student_roll_number', 'name', 'relation', 'phone', 'email', 'is_primary'],
                ['21CS001', 'Ravi Kumar', 'father', '9876543211', 'ravi@example.com', 'yes']),
    'staff': (['employee_id', 'first_name', 'last_name', 'email', 'gender', 'phone', 'department_code', 'address'],
              ['E1001', 'Meena', 'Iyer', 'meena.iyer@example.com', 'F', '9876543212', 'CSE', '2 Lake View']),
}


def template_workbook(kind):
    headers, example = TEMPLATES[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Template'
    ws.append(headers)
    ws.append(example)
    notes = wb.create_sheet('Notes')
    notes.append(['Notes'])
    notes.append(['Keep the header row. Delete the example row. Format roll numbers / IDs / phone columns as Text so Excel keeps leading zeros.'])
    notes.append(['Phones: 10-digit Indian mobile numbers (98765 43210, +91 98765 43210). Dates: 2006-04-23 or 23/04/2006.'])
    notes.append(['An import never sets a password and never sets WhatsApp consent.'])
    notes.append([])
    if kind == 'students':
        notes.append(['Sections that exist in this college (programme_code, batch_start_year, section):'])
        for s in Section.objects.select_related('batch__programme'):
            notes.append([safe_cell(s.batch.programme.code), s.batch.start_year, safe_cell(s.name)])
        notes.append(['gender: M or F (optional)'])
    elif kind == 'staff':
        notes.append(['Department codes in this college:'])
        for d in Department.objects.all():
            notes.append([safe_cell(d.code), safe_cell(d.name)])
    else:
        notes.append(['relation: father, mother, guardian or other. is_primary: yes or no. One primary guardian per student.'])
        notes.append(['A guardian with several children: one row per child, same phone number.'])
    return _to_bytes(wb)
