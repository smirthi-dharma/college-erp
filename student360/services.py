"""Student 360: who may see which student, and the data for the page (a fixed number of queries)."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from academics.models import Enrolment
from attendance.services import CONDONED, OK, SHORTAGE, WARNING, summarize
from audit_trail.models import AuditLog
from main_app.models import CustomUser, Guardian, Staff, Student, StudentGuardian, StudentResult

from .models import StudentAccessLog

ADMIN, STAFF_ROLE, STUDENT_ROLE = CustomUser.COLLEGE_ADMIN, CustomUser.STAFF, CustomUser.STUDENT


def visible_students(user):
    """Students this user may open. A teacher's access follows ACTIVE enrolment in their own offerings."""
    if user.user_type == ADMIN:
        return Student.objects.all()
    if user.user_type == STAFF_ROLE:
        staff = Staff.objects.filter(admin=user).first()
        if staff is None:
            return Student.objects.none()
        ids = Enrolment.objects.filter(offering__faculty=staff, status=Enrolment.ACTIVE).values('student_id')
        return Student.objects.filter(pk__in=ids)
    return Student.objects.none()


def require_access(user, student):
    """Raise PermissionDenied (after recording the refused attempt) unless the user may open this student."""
    if not visible_students(user).filter(pk=student.pk).exists():
        log_access(user, student, StudentAccessLog.REFUSED)
        raise PermissionDenied('You may not open this student.')


def log_access(user, student, outcome=StudentAccessLog.ALLOWED):
    StudentAccessLog.objects.create(viewer_id=user.pk, viewer_email=user.email, viewer_role=user.user_type,
                                    student_id=student.pk, student_repr=str(student)[:200], outcome=outcome)


def log_refused_unknown(user, student_id):
    """A refused attempt on an id that does not exist (someone probing ids)."""
    StudentAccessLog.objects.create(viewer_id=user.pk, viewer_email=user.email, viewer_role=user.user_type,
                                    student_id=student_id, student_repr='(no such student)',
                                    outcome=StudentAccessLog.REFUSED)


def _alerts(viewer_role, student, summary, guardians):
    """(level, text, kind) tuples. Consent and structure alerts are for staff who can act on them."""
    alerts = []
    for e in summary.entries:
        subject = e.offering.subject.name if e.offering else 'all subjects'
        if e.status == SHORTAGE:
            alerts.append(('danger', f'Attendance shortage in {subject}: {e.percent}%', 'shortage'))
        elif e.status == WARNING:
            alerts.append(('warning', f'Attendance warning in {subject}: {e.percent}%', 'warning'))
        if e.pending_on_duty:
            alerts.append(('info', f'{e.pending_on_duty} on-duty mark(s) in {subject} awaiting college-admin review',
                           'on_duty'))
    if viewer_role in (ADMIN, STAFF_ROLE) and student.section_id is None:
        alerts.append(('warning', 'No section assigned', 'no_section'))
    if viewer_role == ADMIN:
        if not guardians:
            alerts.append(('warning', 'No guardian on file', 'no_guardian'))
        elif any(g.guardian.whatsapp_consent == Guardian.CONSENT_UNKNOWN for g in guardians):
            alerts.append(('warning', 'WhatsApp consent not recorded for a guardian', 'no_consent'))
    return alerts


def build(viewer, student):
    """Everything the page shows, already filtered for the viewer's role."""
    role = viewer.user_type
    student = Student.objects.select_related(
        'admin', 'section__batch__programme__department', 'section__batch__regulation').get(pk=student.pk)
    enrolments = list(Enrolment.objects.filter(student=student).select_related(
        'offering__subject', 'offering__faculty__admin', 'offering__term', 'offering__section'))
    active = [e for e in enrolments if e.status == Enrolment.ACTIVE]
    history = [e for e in enrolments if e.status != Enrolment.ACTIVE]

    summary = summarize([student])[student.pk]
    attended = sum(e.attended for e in summary.entries)
    held = sum(e.held for e in summary.entries)
    overall = (Decimal(attended) * 100 / held).quantize(Decimal('0.01')) if held else None

    results = StudentResult.objects.filter(student=student).select_related('offering__subject')
    if role == STAFF_ROLE:
        results = results.filter(offering__faculty__admin=viewer)  # a teacher sees marks only for their own subjects
    results = list(results)

    guardians = list(StudentGuardian.objects.filter(student=student).select_related('guardian')
                     .order_by('-is_primary', 'guardian__full_name'))

    section = student.section
    ctx = {
        'student': student, 'user_row': student.admin, 'role': role,
        'section': section, 'batch': section.batch if section else None,
        'programme': section.batch.programme if section else None,
        'regulation': section.batch.regulation if section else None,
        'active': active, 'history': history,
        'summary': summary, 'overall': overall, 'held': held, 'attended': attended,
        'results': results, 'guardians': guardians,
        'alerts': _alerts(role, student, summary, guardians),
        'show_private': role in (ADMIN, STUDENT_ROLE),  # date of birth, address, guardian phones
        'show_changes': role == ADMIN,
        'changes': [],
        'initials': ((student.admin.first_name[:1] + student.admin.last_name[:1]) or '?').upper(),
    }
    if role == ADMIN:
        ctx['changes'] = list(AuditLog.objects.filter(
            Q(model_label='main_app.Student', object_pk=str(student.pk))
            | Q(model_label='main_app.CustomUser', object_pk=str(student.admin_id)))[:20])
    return ctx


def list_alerts(students):
    """{student pk: attendance status or None} for a page of students (constant queries)."""
    summaries = summarize(students)
    return {pk: s.status for pk, s in summaries.items()}


STATUS_BADGE = {SHORTAGE: 'danger', WARNING: 'warning', CONDONED: 'info', OK: 'success'}
