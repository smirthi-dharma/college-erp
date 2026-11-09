import datetime
import io

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from academics.models import Regulation, Section, SubjectOffering
from bulk_import.services import safe_cell
from main_app.access import COLLEGE_ADMIN, STAFF, STUDENT, role_required
from main_app.models import Attendance, AttendanceReport, Student

from . import services
from .forms import CondonationForm, RuleForm, SubstituteForm
from .models import AttendanceRule, Condonation, SubstituteAssignment, rule_for

staff_or_admin = role_required(STAFF, COLLEGE_ADMIN, strict=True)
admin_only = role_required(COLLEGE_ADMIN, strict=True)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _date(value, default=None):
    try:
        return datetime.date.fromisoformat(value) if value else (default or datetime.date.today())
    except ValueError:
        raise Http404('Bad date') from None


def _period(value):
    try:
        period = int(value or 1)
    except ValueError:
        raise Http404('Bad period') from None
    if not 1 <= period <= 8:
        raise Http404('Bad period')
    return period


# --- taking attendance ------------------------------------------------------------------------------------------------

@staff_or_admin
def attendance_home(request):
    today = datetime.date.today()
    day = _date(request.GET.get('date'), today)
    offerings = SubjectOffering.objects.select_related('subject', 'section__batch', 'term').filter(
        term__start_date__lte=day, term__end_date__gte=day)
    if request.user.user_type == STAFF:
        mine = offerings.filter(faculty__admin=request.user)
        covering = SubstituteAssignment.objects.filter(date=day, substitute__admin=request.user).select_related(
            'offering__subject', 'offering__section__batch')
        regular_blocked = {(a.offering_id, a.period) for a in SubstituteAssignment.objects.filter(date=day)}
        classes = [(o, 1, False) for o in mine if (o.pk, 1) not in regular_blocked]
        classes += [(a.offering, a.period, True) for a in covering]
    else:
        classes = [(o, 1, False) for o in offerings]
    return render(request, 'attendance/home.html', {'classes': classes, 'day': day, 'today': today, 'page_title': 'Take attendance'})


def _posted_statuses(post):
    return {int(k[3:]): v for k, v in post.items() if k.startswith('st_') and k[3:].isdigit()}


def _class_context(offering, day, period, statuses, key):
    students = list(services.roster(offering, day))
    existing = {r.student_id: r.status for r in AttendanceReport.objects.filter(
        attendance__offering=offering, attendance__date=day, attendance__period=period)}
    rows = [{'student': s, 'status': statuses.get(s.pk) or existing.get(s.pk) or services.PRESENT} for s in students]
    return {'offering': offering, 'day': day, 'period': period, 'rows': rows, 'key': key,
            'absent': sum(r['status'] == services.ABSENT for r in rows),
            'draft_key': f'att:{offering.pk}:{day.isoformat()}:{period}', 'already': bool(existing),
            'page_title': f'{offering.subject.code} {offering.section}'}


@staff_or_admin
def attendance_class(request, offering_id):
    offering = get_object_or_404(SubjectOffering.objects.select_related('subject', 'section__batch', 'term'), pk=offering_id)
    day = _date(request.GET.get('date') or request.POST.get('date'))
    period = _period(request.GET.get('period') or request.POST.get('period'))
    if not services.can_take(request.user, offering, day, period):
        raise PermissionDenied('You may not take this class.')
    if request.method == 'POST':
        key = request.POST.get('key', '')
        statuses = _posted_statuses(request.POST)
        try:
            result = services.submit(request.user, offering, day, period, statuses, key)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages) + ' Your taps are kept below.')
            context = _class_context(offering, day, period, statuses, key)
            return render(request, 'attendance/class.html', context)
        except PermissionDenied:
            raise
        messages.success(request, f'Saved: {result.absent} absent, {result.on_duty} on duty.')
        url = reverse('attendance_summary', args=[offering.pk]) + f'?date={day.isoformat()}&period={period}'
        return redirect(url)
    import secrets
    return render(request, 'attendance/class.html', _class_context(offering, day, period, {}, secrets.token_hex(16)))


@staff_or_admin
def attendance_summary(request, offering_id):
    offering = get_object_or_404(SubjectOffering.objects.select_related('subject', 'section__batch'), pk=offering_id)
    day = _date(request.GET.get('date'))
    period = _period(request.GET.get('period'))
    viewer_is_teacher = request.user.user_type == STAFF and offering.faculty.admin_id == request.user.pk
    if not (viewer_is_teacher or services.can_take(request.user, offering, day, period)):
        raise PermissionDenied('You may not see this class.')
    attendance = get_object_or_404(Attendance, offering=offering, date=day, period=period)
    reports = list(AttendanceReport.objects.filter(attendance=attendance).select_related('student__admin'))
    summaries = services.summarize([r.student for r in reports])
    flagged = []
    for r in reports:
        for entry in summaries[r.student_id].entries:
            if entry.status in (services.SHORTAGE, services.WARNING) and (entry.offering is None or entry.offering.pk == offering.pk):
                flagged.append((r.student, entry))
    flagged.sort(key=lambda row: row[1].percent)
    return render(request, 'attendance/summary.html', {
        'offering': offering, 'day': day, 'period': period, 'attendance': attendance,
        'absent': [r.student for r in reports if r.status == services.ABSENT],
        'on_duty': [r.student for r in reports if r.status == services.ON_DUTY],
        'flagged': flagged, 'draft_key': f'att:{offering.pk}:{day.isoformat()}:{period}', 'page_title': 'Attendance saved'})


# --- reports ----------------------------------------------------------------------------------------------------------

@admin_only
def shortage_report(request):
    section = Section.objects.filter(pk=request.GET.get('section')).first() if request.GET.get('section') else None
    offering = SubjectOffering.objects.filter(pk=request.GET.get('offering')).first() if request.GET.get('offering') else None
    statuses = (services.SHORTAGE,) if request.GET.get('only') == 'shortage' else (services.SHORTAGE, services.WARNING, services.CONDONED)
    rows = services.shortage_students(section=section, offering=offering, statuses=statuses)
    if request.GET.get('format') == 'xlsx':
        wb = Workbook()
        ws = wb.active
        ws.title = 'Attendance shortage'
        ws.append(['Roll number', 'Name', 'Section', 'Subject', 'Held', 'Attended', 'Percent', 'Status', 'Can still miss'])
        for student, entry in rows:
            ws.append([safe_cell(student.roll_number), safe_cell(str(student)), safe_cell(student.section),
                       safe_cell(entry.offering.subject.name if entry.offering else 'Overall'), entry.held, entry.attended,
                       float(entry.percent), entry.status, entry.can_miss])
        buf = io.BytesIO()
        wb.save(buf)
        response = HttpResponse(buf.getvalue(), content_type=XLSX)
        response['Content-Disposition'] = 'attachment; filename="attendance_shortage.xlsx"'
        return response
    return render(request, 'attendance/shortage.html', {
        'rows': rows, 'sections': Section.objects.all(), 'offerings': SubjectOffering.objects.select_related('subject', 'section'),
        'section': section, 'offering': offering, 'page_title': 'Attendance shortage'})


@role_required(STUDENT, strict=True)
def my_attendance(request):
    student = get_object_or_404(Student, admin=request.user)
    summary = services.summarize([student])[student.pk]
    return render(request, 'attendance/mine.html', {'summary': summary, 'page_title': 'My attendance'})


# --- on-duty review ---------------------------------------------------------------------------------------------------

@admin_only
def onduty_review(request):
    reports = AttendanceReport.objects.filter(status=services.ON_DUTY, needs_review=True).select_related(
        'student__admin', 'attendance__offering__subject').order_by('attendance__date')
    return render(request, 'attendance/onduty.html', {'reports': reports, 'page_title': 'On-duty review'})


@admin_only
@require_POST
def onduty_decide(request, report_id):
    report = get_object_or_404(AttendanceReport, pk=report_id)
    services.review_on_duty(request.user, report, approve=request.POST.get('decision') == 'approve')
    messages.success(request, 'Decision saved.')
    return redirect(reverse('onduty_review'))


# --- condonation ------------------------------------------------------------------------------------------------------

@admin_only
def condonation_list(request):
    form = CondonationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.condone(request.user, form.cleaned_data['student'], form.cleaned_data['offering'], form.cleaned_data['reason'])
            messages.success(request, 'Condonation recorded.')
            return redirect(reverse('condonation_list'))
        except ValidationError as exc:
            form.add_error(None, exc)
    records = Condonation.objects.select_related('student__admin', 'offering__subject', 'approved_by').order_by('-approved_at')
    return render(request, 'attendance/condonation.html', {'form': form, 'records': records, 'page_title': 'Condonation'})


@admin_only
@require_POST
def condonation_revoke(request, pk):
    services.revoke_condonation(request.user, get_object_or_404(Condonation, pk=pk))
    messages.success(request, 'Condonation revoked.')
    return redirect(reverse('condonation_list'))


# --- substitutes ------------------------------------------------------------------------------------------------------

@admin_only
def substitute_list(request):
    form = SubstituteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            services.assign_substitute(request.user, d['offering'], d['date'], d['period'], d['substitute'])
            messages.success(request, 'Substitute assigned. Only they can take that class on that date.')
            return redirect(reverse('substitute_list'))
        except ValidationError as exc:
            form.add_error(None, exc)
    records = SubstituteAssignment.objects.select_related('offering__subject', 'offering__section', 'substitute__admin').order_by('-date')
    return render(request, 'attendance/substitute.html', {'form': form, 'records': records, 'page_title': 'Substitutes'})


@admin_only
@require_POST
def substitute_delete(request, pk):
    get_object_or_404(SubstituteAssignment, pk=pk).delete()
    messages.success(request, 'Substitute removed.')
    return redirect(reverse('substitute_list'))


# --- rules ------------------------------------------------------------------------------------------------------------

@admin_only
def rules_list(request):
    rows = [(r, rule_for(r), AttendanceRule.objects.filter(regulation=r).exists())
            for r in Regulation.objects.select_related('programme')]
    return render(request, 'attendance/rules.html', {'rows': rows, 'page_title': 'Attendance rules'})


@admin_only
def rule_edit(request, regulation_id):
    regulation = get_object_or_404(Regulation, pk=regulation_id)
    instance = AttendanceRule.objects.filter(regulation=regulation).first() or AttendanceRule(regulation=regulation)
    form = RuleForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Rule saved.')
        return redirect(reverse('rules_list'))
    return render(request, 'attendance/rule_form.html', {'form': form, 'regulation': regulation, 'page_title': 'Edit attendance rule'})


