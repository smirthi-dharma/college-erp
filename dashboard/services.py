"""The principal dashboard: five tiles worked out with a fixed number of queries, however many students the college has.

Every number comes from the same place as the report it summarises (attendance.services for the 75% rule, the consent field on
Guardian), so the dashboard and the reports cannot disagree. Nothing here writes to the database.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from academics.models import AcademicTerm, Section, SubjectOffering
from attendance import services as attendance
from main_app.models import Attendance, AttendanceReport, Guardian, Student, StudentGuardian

PRESENT_LIKE = [AttendanceReport.PRESENT, AttendanceReport.ON_DUTY]  # on duty counts as present, as in the shortage rule's default
STATUS_ORDER = (attendance.SHORTAGE, attendance.WARNING, attendance.CONDONED, attendance.OK)  # worst first


def terms():
    return list(AcademicTerm.objects.all())  # newest first


def default_term(all_terms, today):
    """The term whose dates contain today; if none does, the latest one; None for a college with no term yet."""
    for term in all_terms:
        if term.start_date <= today <= term.end_date:
            return term
    return all_terms[0] if all_terms else None


def percent(part, whole):
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal('0.1')) if whole else None


def attendance_today(term, today):
    if term is None:
        return {'classes': 0, 'marks': 0, 'present_percent': None}
    classes = Attendance.objects.filter(date=today, offering__term=term).count()
    totals = AttendanceReport.objects.filter(attendance__date=today, attendance__offering__term=term).aggregate(
        marks=Count('id'), present=Count('id', filter=Q(status__in=PRESENT_LIKE)))
    return {'classes': classes, 'marks': totals['marks'], 'present_percent': percent(totals['present'], totals['marks'])}


def sections_not_marked(term, today):
    """Sections that have a subject this term and no class recorded today, each with the teachers who teach them."""
    if term is None:
        return {'total': 0, 'unmarked': []}
    marked = Attendance.objects.filter(date=today, offering__term=term, offering__section=OuterRef('pk'))
    sections = list(Section.objects.filter(subjectoffering__term=term).distinct()
                    .annotate(marked=Exists(marked)).select_related('batch__programme'))
    unmarked = [s for s in sections if not s.marked]
    teachers = defaultdict(set)
    for offering in SubjectOffering.objects.filter(term=term, section__in=[s.pk for s in unmarked]).select_related('faculty__admin'):
        name = ('%s %s' % (offering.faculty.admin.first_name, offering.faculty.admin.last_name)).strip()
        teachers[offering.section_id].add(name or offering.faculty.admin.email)
    return {'total': len(sections),
            'unmarked': [{'section': s, 'programme': s.batch.programme.name, 'teachers': sorted(teachers[s.pk])} for s in unmarked]}


def shortage(term):
    """Students counted once each by their worst status. Same rule and same numbers as the shortage report for the term."""
    summaries = attendance.summarize(Student.objects.all(), term=term)
    counts = Counter()
    worst_by_student = {}
    for pk, summary in summaries.items():
        statuses = {e.status for e in summary.entries}
        worst = next((s for s in STATUS_ORDER if s in statuses), 'no_data')
        worst_by_student[pk] = worst
        counts[worst] += 1
    return {'total': len(summaries), 'shortage': counts[attendance.SHORTAGE], 'warning': counts[attendance.WARNING],
            'condoned': counts[attendance.CONDONED], 'ok': counts[attendance.OK], 'no_data': counts['no_data'],
            'worst_by_student': worst_by_student}


def consent_coverage():
    """Guardians by WhatsApp consent: each guardian once in the total, and once per programme they have a child in."""
    totals = {row['whatsapp_consent']: row['n'] for row in Guardian.objects.values('whatsapp_consent').annotate(n=Count('id'))}
    rows = (StudentGuardian.objects.values('student__section__batch__programme__name', 'guardian__whatsapp_consent')
            .annotate(n=Count('guardian', distinct=True)))
    by_programme = defaultdict(lambda: {Guardian.CONSENT_YES: 0, Guardian.CONSENT_NO: 0, Guardian.CONSENT_UNKNOWN: 0})
    for row in rows:
        by_programme[row['student__section__batch__programme__name'] or 'No programme yet'][row['guardian__whatsapp_consent']] += row['n']
    total = sum(totals.values())
    yes, no, unknown = (totals.get(c, 0) for c in (Guardian.CONSENT_YES, Guardian.CONSENT_NO, Guardian.CONSENT_UNKNOWN))
    programmes = []
    for name in sorted(by_programme):
        row = by_programme[name]
        count = sum(row.values())
        programmes.append({'name': name, 'total': count, 'yes': row['yes'], 'no': row['no'], 'unknown': row['unknown'],
                           'yes_percent': percent(row['yes'], count)})
    return {'total': total, 'yes': yes, 'no': no, 'unknown': unknown, 'yes_percent': percent(yes, total), 'programmes': programmes}


def tiles(term, now=None):
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    return {'term': term, 'today': today, 'as_of': now, 'attendance': attendance_today(term, today),
            'sections': sections_not_marked(term, today), 'shortage': shortage(term), 'consent': consent_coverage()}
