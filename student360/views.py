from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from academics.models import Batch, Programme, Section
from main_app.access import COLLEGE_ADMIN, STAFF, STUDENT, role_required
from main_app.models import Student

from . import services

PAGE_SIZE = 50
staff_or_admin = role_required(STAFF, COLLEGE_ADMIN, strict=True)
student_only = role_required(STUDENT, strict=True)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@staff_or_admin
@require_GET
def student_list(request):
    qs = services.visible_students(request.user).select_related(
        'admin', 'section__batch__programme').order_by('admin__last_name', 'admin__first_name', 'pk')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(admin__first_name__icontains=q) | Q(admin__last_name__icontains=q)
                       | Q(admin__email__icontains=q) | Q(roll_number__icontains=q))
    programme, batch, section = (_int(request.GET.get(k)) for k in ('programme', 'batch', 'section'))
    if programme:
        qs = qs.filter(section__batch__programme_id=programme)
    if batch:
        qs = qs.filter(section__batch_id=batch)
    if section:
        qs = qs.filter(section_id=section)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    statuses = services.list_alerts(list(page.object_list))
    rows = [(s, statuses.get(s.pk), services.STATUS_BADGE.get(statuses.get(s.pk))) for s in page.object_list]
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'student360/list.html', {
        'rows': rows, 'page': page, 'q': q, 'querystring': params.urlencode(),
        'programmes': Programme.objects.all(), 'batches': Batch.objects.select_related('programme'),
        'sections': Section.objects.select_related('batch__programme'),
        'sel': {'programme': programme, 'batch': batch, 'section': section},
        'page_title': 'Students',
    })


@staff_or_admin
@require_GET
def student_detail(request, pk):
    student = get_object_or_404(Student, pk=pk)
    services.require_access(request.user, student)
    services.log_access(request.user, student)
    ctx = services.build(request.user, student)
    ctx['page_title'] = f'Student 360: {student.admin.first_name} {student.admin.last_name}'
    return render(request, 'student360/detail.html', ctx)


@student_only
@require_GET
def student_me(request):
    student = get_object_or_404(Student, admin=request.user)
    ctx = services.build(request.user, student)
    ctx['page_title'] = 'My profile'
    return render(request, 'student360/detail.html', ctx)
