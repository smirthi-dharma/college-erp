"""Taking attendance, percentages, shortage status. All rules come from AttendanceRule (see models.py)."""
import datetime
import math
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from academics.models import Enrolment
from main_app.models import Attendance, AttendanceReport, CustomUser, Staff, Student

from .models import AttendanceRule, Condonation, SubstituteAssignment, default_rule, rule_for

PRESENT, ABSENT, ON_DUTY = AttendanceReport.PRESENT, AttendanceReport.ABSENT, AttendanceReport.ON_DUTY
OK, WARNING, SHORTAGE, CONDONED = 'ok', 'warning', 'shortage', 'condoned'


def regulation_of(offering):
    return offering.section.batch.regulation


# --- who may take a class, and when -----------------------------------------------------------------------------------

def _staff_of(user):
    return Staff.objects.filter(admin=user).first()


def can_take(user, offering, day, period):
    """College admin: always. Otherwise the substitute if one is assigned for that class, else the offering's teacher."""
    if user.user_type == CustomUser.COLLEGE_ADMIN:
        return True
    staff = _staff_of(user)
    if staff is None:
        return False
    substitute = SubstituteAssignment.objects.filter(offering=offering, date=day, period=period).first()
    if substitute is not None:
        return substitute.substitute_id == staff.pk
    return offering.faculty_id == staff.pk


def check_date(offering, day, user, today=None):
    today = today or datetime.date.today()
    if day > today:
        raise ValidationError('Attendance cannot be marked for a future date.')
    term = offering.term
    if not (term.start_date <= day <= term.end_date):
        raise ValidationError('That date is outside the term of this class.')
    if user.user_type != CustomUser.COLLEGE_ADMIN:
        window = rule_for(regulation_of(offering)).edit_window_days
        if (today - day).days > window:
            raise PermissionDenied(f'Only the college admin can mark or change attendance older than {window} days.')


# --- the class list ---------------------------------------------------------------------------------------------------

def roster(offering, day):
    """Students on the class list for that date: enrolled on or before it, not withdrawn on or before it."""
    enrolments = (Enrolment.objects.filter(offering=offering, enrolled_on__lte=day)
                  .filter(Q(withdrawn_on__isnull=True) | Q(withdrawn_on__gt=day)))
    return (Student.objects.filter(pk__in=enrolments.values('student_id')).select_related('admin')
            .order_by('roll_number', 'admin__last_name', 'admin__first_name'))


# --- taking attendance ------------------------------------------------------------------------------------------------

@dataclass
class Result:
    attendance: Attendance
    created: bool
    changed: int = 0
    absent: int = 0
    on_duty: int = 0


def submit(user, offering, day, period, statuses, key='', today=None):
    """Save one class. `statuses` maps student id -> present/absent/on_duty; students not mentioned are present.

    One transaction: any failure leaves nothing behind. A retry (same key, or a double tap) saves exactly once.
    """
    if not can_take(user, offering, day, period):
        raise PermissionDenied('You may not take this class.')
    check_date(offering, day, user, today)
    if not 1 <= period <= 8:
        raise ValidationError('Period must be between 1 and 8.')
    listed = {s.pk for s in roster(offering, day)}
    unknown = {int(k) for k in statuses} - listed
    if unknown:
        raise PermissionDenied('A student who is not on this class list was submitted.')
    wanted = {pk: statuses.get(pk, PRESENT) for pk in listed}
    if any(v not in (PRESENT, ABSENT, ON_DUTY) for v in wanted.values()):
        raise ValidationError('Unknown attendance status.')
    is_admin = user.user_type == CustomUser.COLLEGE_ADMIN
    with transaction.atomic():
        attendance, created = _get_or_create_class(offering, day, period, user, key)
        existing = {r.student_id: r for r in AttendanceReport.objects.filter(attendance=attendance)}
        changed = 0
        for student_id, status in wanted.items():
            report = existing.get(student_id)
            review = status == ON_DUTY and not is_admin
            if report is None:
                AttendanceReport(student_id=student_id, attendance=attendance, status=status, needs_review=review).save()
                changed += 1
            elif report.status != status:
                report.status, report.needs_review = status, review
                report.save()
                changed += 1
        if not created and changed and attendance.taken_by_id != user.pk:
            attendance.taken_by = user
            attendance.save()
    return Result(attendance, created, changed, sum(v == ABSENT for v in wanted.values()),
                  sum(v == ON_DUTY for v in wanted.values()))


def _get_or_create_class(offering, day, period, user, key):
    existing = Attendance.objects.select_for_update().filter(offering=offering, date=day, period=period).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            return Attendance.objects.create(offering=offering, date=day, period=period, taken_by=user,
                                             submission_key=key[:40]), True
    except IntegrityError:  # a simultaneous submit created it first: use that one
        return Attendance.objects.select_for_update().get(offering=offering, date=day, period=period), False


# --- percentages and status -------------------------------------------------------------------------------------------

@dataclass
class Entry:
    offering: object  # None for the overall entry
    held: int
    attended: int
    percent: Decimal | None
    status: str | None
    can_miss: int
    pending_on_duty: int = 0


@dataclass
class Summary:
    student: Student
    rule: AttendanceRule
    entries: list = field(default_factory=list)  # one per subject, or a single overall entry

    @property
    def status(self):
        order = {SHORTAGE: 3, WARNING: 2, CONDONED: 1, OK: 0}
        found = [e.status for e in self.entries if e.status]
        return max(found, key=order.get) if found else None


def evaluate(attended, held, rule, condoned=False):
    """(percent, status, can_miss). Exact integer arithmetic at the band edges (no float rounding)."""
    if held == 0:
        return None, None, 0
    percent = (Decimal(attended) * 100 / held).quantize(Decimal('0.01'))
    if attended * 100 < rule.required_percent * held:
        status = CONDONED if condoned else SHORTAGE
    elif attended * 100 < rule.warning_percent * held:
        status = WARNING
    else:
        status = OK
    can_miss = 0
    if rule.required_percent > 0:
        can_miss = max(0, math.floor(Decimal(attended) * 100 / rule.required_percent - held))
    return percent, status, can_miss


def summarize(students):
    """Summaries for many students with a constant number of queries."""
    students = list(students)
    ids = [s.pk for s in students]
    sections = {s.pk: s for s in Student.objects.filter(pk__in=ids).select_related('section__batch')}
    rules = {r.regulation_id: r for r in AttendanceRule.objects.all()}
    rows = (AttendanceReport.objects.filter(student_id__in=ids)
            .values('student_id', 'attendance__offering_id')
            .annotate(held=Count('id'), present=Count('id', filter=Q(status=PRESENT)),
                      on_duty=Count('id', filter=Q(status=ON_DUTY)),
                      pending=Count('id', filter=Q(status=ON_DUTY, needs_review=True))))
    by_student = {}
    for row in rows:
        by_student.setdefault(row['student_id'], []).append(row)
    condoned = {}
    for c in Condonation.objects.filter(student_id__in=ids, revoked_at__isnull=True):
        condoned.setdefault(c.student_id, set()).add(c.offering_id)  # None = overall
    from academics.models import SubjectOffering
    offerings = {o.pk: o for o in SubjectOffering.objects.filter(
        pk__in={r['attendance__offering_id'] for rs in by_student.values() for r in rs}).select_related('subject')}
    result = {}
    for student in students:
        section = sections[student.pk].section
        rule = rules.get(section.batch.regulation_id) if section else None
        rule = rule or default_rule()
        summary = Summary(student, rule)
        rows_here = by_student.get(student.pk, [])

        def attended(row, rule=rule):
            return row['present'] + (row['on_duty'] if rule.on_duty_counts_as_present else 0)
        mine = condoned.get(student.pk, set())
        if rule.basis == AttendanceRule.PER_SUBJECT:
            for row in sorted(rows_here, key=lambda r: r['attendance__offering_id']):
                att = attended(row)
                pct, status, can_miss = evaluate(att, row['held'], rule, row['attendance__offering_id'] in mine)
                summary.entries.append(Entry(offerings[row['attendance__offering_id']], row['held'], att, pct, status,
                                             can_miss, row['pending']))
        else:
            held = sum(r['held'] for r in rows_here)
            att = sum(attended(r) for r in rows_here)
            pct, status, can_miss = evaluate(att, held, rule, None in mine)
            summary.entries.append(Entry(None, held, att, pct, status, can_miss, sum(r['pending'] for r in rows_here)))
        result[student.pk] = summary
    return result


def shortage_students(section=None, offering=None, statuses=(SHORTAGE, WARNING)):
    """Rows (student, entry) whose status is in `statuses`. The single source for reports and the later WhatsApp digest."""
    students = Student.objects.select_related('admin', 'section__batch__programme')
    if section is not None:
        students = students.filter(section=section)
    if offering is not None:
        ids = Enrolment.objects.filter(offering=offering, status=Enrolment.ACTIVE).values('student_id')
        students = students.filter(pk__in=ids)
    summaries = summarize(students)
    rows = []
    for student in students:
        for entry in summaries[student.pk].entries:
            if entry.status in statuses and (offering is None or entry.offering is None or entry.offering.pk == offering.pk):
                rows.append((student, entry))
    rows.sort(key=lambda r: (r[1].percent if r[1].percent is not None else Decimal(101)))
    return rows


# --- condonation, on-duty review, substitutes -------------------------------------------------------------------------

def condonable_entry(student, offering):
    """The entry a condonation would apply to (per-subject or overall), or None."""
    summary = summarize([student])[student.pk]
    for entry in summary.entries:
        if (entry.offering is None and offering is None) or (entry.offering is not None and offering is not None
                                                             and entry.offering.pk == offering.pk):
            return summary.rule, entry
    return summary.rule, None


@transaction.atomic
def condone(admin_user, student, offering, reason):
    if admin_user.user_type != CustomUser.COLLEGE_ADMIN:
        raise PermissionDenied('Only the college admin can condone a shortage.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required.')
    rule, entry = condonable_entry(student, offering)
    if rule.basis == AttendanceRule.OVERALL and offering is not None:
        raise ValidationError('Attendance is counted overall: condone the overall figure.')
    if rule.basis == AttendanceRule.PER_SUBJECT and offering is None:
        raise ValidationError('Attendance is counted per subject: choose a subject.')
    if entry is None or entry.held == 0:
        raise ValidationError('There is no attendance to condone yet.')
    if entry.attended * 100 >= rule.required_percent * entry.held:
        raise ValidationError('This student is not below the required percentage.')
    if entry.attended * 100 < rule.condonable_percent * entry.held:
        raise ValidationError(f'Below the condonable band ({rule.condonable_percent}%): it cannot be condoned.')
    try:
        with transaction.atomic():
            return Condonation.objects.create(student=student, offering=offering, reason=reason,
                                              percentage_at_approval=entry.percent, approved_by=admin_user)
    except IntegrityError as exc:
        raise ValidationError('This student already has an active condonation for it.') from exc


@transaction.atomic
def revoke_condonation(admin_user, condonation):
    from django.utils import timezone
    if admin_user.user_type != CustomUser.COLLEGE_ADMIN:
        raise PermissionDenied('Only the college admin can revoke a condonation.')
    if condonation.revoked_at is None:
        condonation.revoked_by, condonation.revoked_at = admin_user, timezone.now()
        condonation.save()
    return condonation


@transaction.atomic
def review_on_duty(admin_user, report, approve):
    if admin_user.user_type != CustomUser.COLLEGE_ADMIN:
        raise PermissionDenied('Only the college admin can review on-duty marks.')
    if not report.needs_review:
        return report
    report.needs_review = False
    if not approve:
        report.status = ABSENT
    report.save()
    return report


@transaction.atomic
def assign_substitute(admin_user, offering, day, period, staff):
    if admin_user.user_type != CustomUser.COLLEGE_ADMIN:
        raise PermissionDenied('Only the college admin can assign a substitute.')
    if staff.pk == offering.faculty_id:
        raise ValidationError('That is the regular teacher of the class.')
    assignment, _ = SubstituteAssignment.objects.update_or_create(
        offering=offering, date=day, period=period, defaults={'substitute': staff, 'assigned_by': admin_user})
    return assignment
