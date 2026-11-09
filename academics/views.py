import logging

from django.contrib import messages
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from main_app.access import HOD, role_required

from . import models, services
from .forms import FORMS, ElectiveImportForm, EnrolStudentForm

logger = logging.getLogger(__name__)

# key -> (model, page title, list columns as (header, attribute path))
REGISTRY = {
    'departments': (models.Department, 'Departments', [('Name', 'name'), ('Code', 'code')]),
    'programmes': (models.Programme, 'Programmes', [('Name', 'name'), ('Code', 'code'), ('Department', 'department'), ('Semesters', 'duration_semesters')]),
    'regulations': (models.Regulation, 'Regulations', [('Programme', 'programme'), ('Name', 'name'), ('Effective from', 'effective_from')]),
    'batches': (models.Batch, 'Batches', [('Programme', 'programme'), ('Regulation', 'regulation'), ('Start year', 'start_year')]),
    'sections': (models.Section, 'Sections', [('Batch', 'batch'), ('Name', 'name')]),
    'subjects': (models.Subject, 'Subjects', [('Code', 'code'), ('Name', 'name'), ('Programme', 'programme'), ('Semester', 'semester'), ('Credits', 'credits'), ('Kind', 'kind')]),
    'terms': (models.AcademicTerm, 'Academic terms', [('Name', 'name'), ('Starts', 'start_date'), ('Ends', 'end_date')]),
    'offerings': (models.SubjectOffering, 'Subject offerings', [('Subject', 'subject'), ('Section', 'section'), ('Term', 'term'), ('Faculty', 'faculty'), ('Kind', 'kind')]),
}


def _entry(key):
    if key not in REGISTRY:
        from django.http import Http404
        raise Http404('Unknown list')
    return REGISTRY[key]


@role_required(HOD)
def academics_home(request):
    items = [(key, REGISTRY[key][1]) for key in REGISTRY]
    return render(request, 'academics/home.html', {'items': items, 'page_title': 'Institution structure'})


@role_required(HOD)
def academics_list(request, key):
    model, title, columns = _entry(key)
    rows = [{'obj': obj, 'cells': [getattr(obj, attr) for _, attr in columns]} for obj in model.objects.all()]
    context = {'key': key, 'page_title': title, 'headers': [h for h, _ in columns], 'rows': rows}
    return render(request, 'academics/list.html', context)


@role_required(HOD)
def academics_edit(request, key, pk=None):
    model, title, _ = _entry(key)
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = FORMS[key](request.POST or None, instance=instance)
    if request.method == 'POST':
        if form.is_valid():
            form.save()
            messages.success(request, 'Saved')
            return redirect(reverse('academics_list', args=[key]))
        messages.error(request, 'Please correct the errors below')
    context = {'form': form, 'key': key, 'page_title': ('Edit ' if instance else 'Add ') + title}
    return render(request, 'academics/form.html', context)


@role_required(HOD)
@require_POST
def academics_delete(request, key, pk):
    model, _, _ = _entry(key)
    obj = get_object_or_404(model, pk=pk)
    try:
        obj.delete()
        messages.success(request, 'Deleted')
    except ProtectedError:
        messages.error(request, 'Cannot delete: other records (students, offerings, attendance or marks) still use it.')
    return redirect(reverse('academics_list', args=[key]))


@role_required(HOD)
def offering_enrolments(request, pk):
    offering = get_object_or_404(models.SubjectOffering.objects.select_related('subject', 'section', 'term', 'faculty__admin'), pk=pk)
    context = {
        'offering': offering,
        'enrolments': models.Enrolment.objects.filter(offering=offering).select_related('student__admin'),
        'enrol_form': EnrolStudentForm(),
        'import_form': ElectiveImportForm(),
        'page_title': f'Enrolments: {offering}',
    }
    return render(request, 'academics/enrolments.html', context)


@role_required(HOD)
@require_POST
def offering_enrol(request, pk):
    offering = get_object_or_404(models.SubjectOffering, pk=pk)
    form = EnrolStudentForm(request.POST)
    if form.is_valid():
        _, changed = services.enrol(form.cleaned_data['student'], offering, models.Enrolment.MANUAL)
        messages.success(request, 'Student enrolled' if changed else 'Already enrolled')
    else:
        messages.error(request, 'Choose a student')
    return redirect(reverse('offering_enrolments', args=[pk]))


@role_required(HOD)
@require_POST
def offering_withdraw(request, pk, enrolment_id):
    enrolment = get_object_or_404(models.Enrolment, pk=enrolment_id, offering_id=pk)
    messages.success(request, 'Withdrawn (marks and attendance are kept)' if services.withdraw(enrolment) else 'Already withdrawn')
    return redirect(reverse('offering_enrolments', args=[pk]))


@role_required(HOD)
@require_POST
def offering_import(request, pk):
    offering = get_object_or_404(models.SubjectOffering, pk=pk)
    form = ElectiveImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, 'Choose an .xlsx file')
    else:
        try:
            count = services.import_electives(offering, form.cleaned_data['file'])
            messages.success(request, f'{count} student(s) enrolled')
        except services.ImportError_ as exc:
            for problem in exc.problems[:20]:
                messages.error(request, problem)
            messages.error(request, 'Nothing was imported. Fix the file and upload it again.')
    return redirect(reverse('offering_enrolments', args=[pk]))
