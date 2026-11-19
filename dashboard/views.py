"""The principal dashboard (college admin and Management, read-only) and the pages where the college admin manages Management users."""
import io

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook

from attendance import services as attendance
from audit_trail.export import log_export
from bulk_import.services import safe_cell
from main_app.access import COLLEGE_ADMIN, MANAGEMENT, role_required
from main_app.models import CustomUser

from . import services
from .forms import ManagementUserForm

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SESSION_KEY = 'dashboard_term'
can_look = role_required(COLLEGE_ADMIN, MANAGEMENT, strict=True)
admin_only = role_required(COLLEGE_ADMIN, strict=True)  # Management can never create or change users


def _chosen_term(request, all_terms):
    """(term, error). ?term=<id> is the picker (remembered for this sign-in); an unknown id is refused, not guessed."""
    asked = request.GET.get('term')
    if asked:
        term = next((t for t in all_terms if str(t.pk) == asked), None)
        if term is None:
            return services.default_term(all_terms, timezone.localdate()), 'That term does not exist.'
        request.session[SESSION_KEY] = term.pk
        return term, None
    remembered = request.session.get(SESSION_KEY)
    term = next((t for t in all_terms if t.pk == remembered), None)
    return term or services.default_term(all_terms, timezone.localdate()), None


@can_look
@require_GET
def principal_dashboard(request):
    all_terms = services.terms()
    term, error = _chosen_term(request, all_terms)
    if error:
        messages.error(request, error)
    context = dict(services.tiles(term), terms=all_terms, page_title='Principal dashboard', is_current=bool(
        term and term.start_date <= timezone.localdate() <= term.end_date))
    return render(request, 'dashboard/principal.html', context, status=400 if error else 200)


@can_look
@require_GET
def principal_export(request):
    all_terms = services.terms()
    term, error = _chosen_term(request, all_terms)
    if error:
        raise Http404(error)
    data = services.tiles(term)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Summary'
    shortage, today, consent = data['shortage'], data['attendance'], data['consent']
    sheet.append(['Principal dashboard', safe_cell(str(term) if term else 'No term yet')])
    sheet.append(['As of', data['as_of'].strftime('%Y-%m-%d %H:%M')])
    sheet.append([])
    sheet.append(['Classes recorded today', today['classes']])
    sheet.append(['Present % today', float(today['present_percent']) if today['present_percent'] is not None else 'no attendance recorded today'])
    sheet.append(['Sections with a subject this term', data['sections']['total']])
    sheet.append(['Sections not marked today', len(data['sections']['unmarked'])])
    sheet.append(['Students in shortage', shortage['shortage']])
    sheet.append(['Students on warning', shortage['warning']])
    sheet.append(['Students condoned', shortage['condoned']])
    sheet.append(['Students OK', shortage['ok']])
    sheet.append(['Students with no classes yet', shortage['no_data']])
    sheet.append(['Guardians with consent (yes / no / not asked)', '%s / %s / %s' % (consent['yes'], consent['no'], consent['unknown'])])
    sheet.append(['Fees', 'Not available yet'])

    unmarked = book.create_sheet('Sections not marked')
    unmarked.append(['Programme', 'Section', 'Teachers'])
    for row in data['sections']['unmarked']:
        unmarked.append([safe_cell(row['programme']), safe_cell(str(row['section'])), safe_cell(', '.join(row['teachers']))])

    short = book.create_sheet('Shortage')
    short.append(['Roll number', 'Name', 'Section', 'Subject', 'Held', 'Attended', 'Percent', 'Status', 'Can still miss'])
    for student, entry in attendance.shortage_students(statuses=(attendance.SHORTAGE, attendance.WARNING, attendance.CONDONED), term=term):
        short.append([safe_cell(student.roll_number), safe_cell(str(student)), safe_cell(student.section),
                      safe_cell(entry.offering.subject.name if entry.offering else 'Overall'), entry.held, entry.attended,
                      float(entry.percent), entry.status, entry.can_miss])

    coverage = book.create_sheet('Consent coverage')
    coverage.append(['Programme', 'Guardians', 'Yes', 'No', 'Not asked', 'Yes %'])
    for row in consent['programmes']:
        coverage.append([safe_cell(row['name']), row['total'], row['yes'], row['no'], row['unknown'],
                         float(row['yes_percent']) if row['yes_percent'] is not None else None])
    buffer = io.BytesIO()
    book.save(buffer)
    log_export('dashboard.PrincipalDashboard', 'Excel export, term %s' % (term or 'none'), {'term': [None, term.pk if term else None]})
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response['Content-Disposition'] = 'attachment; filename="principal_dashboard.xlsx"'
    return response


# --- the college admin manages Management users --------------------------------------------------------------------------

def _management_users():
    return CustomUser.objects.filter(user_type=CustomUser.MANAGEMENT).order_by('last_name', 'first_name', 'pk')


@admin_only
@require_GET
def management_users(request):
    return render(request, 'dashboard/users.html', {'users': _management_users(), 'page_title': 'Management / Principal users'})


@admin_only
def management_user_add(request):
    form = ManagementUserForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        CustomUser.objects.create_user(data['email'], data['password'], user_type=CustomUser.MANAGEMENT, first_name=data['first_name'],
                                       last_name=data['last_name'], phone=data['phone'])
        messages.success(request, 'Management user added.')
        return redirect('management_users')
    return render(request, 'dashboard/user_form.html', {'form': form, 'page_title': 'Add a Management / Principal user'})


@admin_only
def management_user_edit(request, pk):
    user = get_object_or_404(_management_users(), pk=pk)
    initial = {'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email, 'phone': user.phone}
    form = ManagementUserForm(request.POST or None, initial=initial, user=user)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        user.first_name, user.last_name, user.email, user.phone = data['first_name'], data['last_name'], data['email'], data['phone']
        if data['password']:
            user.set_password(data['password'])
        user.save()
        messages.success(request, 'Saved.')
        return redirect('management_users')
    return render(request, 'dashboard/user_form.html', {'form': form, 'editing': user, 'page_title': 'Edit a Management / Principal user'})


@admin_only
@require_POST
def management_user_active(request, pk):
    user = get_object_or_404(_management_users(), pk=pk)
    user.is_active = request.POST.get('active') == '1'
    user.save()
    messages.success(request, 'Activated.' if user.is_active else 'Deactivated: this person can no longer sign in.')
    return redirect('management_users')
