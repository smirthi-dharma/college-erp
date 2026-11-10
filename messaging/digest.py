"""Who was absent on a day, and what each guardian is told. Pure reading and formatting: nothing is sent from here."""
import re
from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from academics.models import Enrolment
from main_app.models import AttendanceReport, StudentGuardian
from main_app.whatsapp import LANGUAGES

from . import texts

MAX_SUMMARY = 900  # WhatsApp allows 1024 characters in a body; leave room for the fixed words


def absences_on(day):
    """{student: [(period, subject name), ...]} for students marked `absent` on `day`.

    `on_duty` and `present` are not absences. A student who was no longer on the class list that day (withdrawn on or
    before it) is left out for that subject, as are inactive users.
    """
    on_list = set(Enrolment.objects.filter(enrolled_on__lte=day)
                  .filter(Q(withdrawn_on__isnull=True) | Q(withdrawn_on__gt=day)).values_list('offering_id', 'student_id'))
    rows = (AttendanceReport.objects.filter(status=AttendanceReport.ABSENT, attendance__date=day, student__admin__is_active=True)
            .select_related('student__admin', 'attendance__offering__subject'))
    result = defaultdict(list)
    for report in rows:
        offering = report.attendance.offering
        if (offering.pk, report.student_id) in on_list:
            result[report.student].append((report.attendance.period, offering.subject.name))
    for items in result.values():
        items.sort()
    return result


def guardians_for(absent, primary_only):
    """({guardian: [student, ...]}, number of absent students nobody could be told about)."""
    links = StudentGuardian.objects.filter(student__in=list(absent)).select_related('guardian')
    if primary_only:
        links = links.filter(is_primary=True)
    by_guardian, reached = defaultdict(list), set()
    for link in links:
        by_guardian[link.guardian].append(link.student)
        reached.add(link.student_id)
    unreachable = sum(1 for student in absent if student.pk not in reached)
    return by_guardian, unreachable


def one_line(value):
    """WhatsApp template variables cannot contain line breaks or long runs of spaces."""
    return re.sub(r'\s+', ' ', str(value)).strip()


def summary(students, absent, language):
    """`Aarthi Kumar: P1 Programming, P3 Maths; Arjun Kumar: P2 Programming`, cut to fit with 'and N more'."""
    parts = []
    for student in sorted(students, key=lambda s: (s.admin.first_name, s.admin.last_name, s.pk)):
        periods = ', '.join('%s %s' % (texts.PERIOD[language].format(n=period), name) for period, name in absent[student])
        parts.append(one_line('%s %s: %s' % (student.admin.first_name, student.admin.last_name, periods)))
    text = '; '.join(parts)
    if len(text) <= MAX_SUMMARY:
        return text
    kept = list(parts)
    while len(kept) > 1:
        kept.pop()
        more = texts.MORE[language].format(n=len(parts) - len(kept))
        text = '; '.join(kept) + more
        if len(text) <= MAX_SUMMARY:
            return text
    return parts[0][:MAX_SUMMARY - 60].rstrip() + texts.MORE[language].format(n=len(parts) - 1) if len(parts) > 1 \
        else parts[0][:MAX_SUMMARY]


def params_for(guardian, students, absent, day, college_name, language):
    return [one_line(guardian.full_name), '%d %s %d' % (day.day, day.strftime('%b'), day.year),
            summary(students, absent, language), one_line(college_name)]


def language_for(guardian, default):
    chosen = guardian.preferred_language or default
    return chosen if chosen in LANGUAGES else 'en'


def local_day(now=None):
    return timezone.localtime(now or timezone.now()).date()
