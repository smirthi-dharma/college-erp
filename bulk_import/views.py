import secrets
import time

from django.contrib import messages
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from main_app.access import COLLEGE_ADMIN, role_required

from . import parsing, services
from .importers import IMPORTERS
from .models import ImportBatch

SESSION_KEY = 'bulk_import_preview'
PREVIEW_SECONDS = 30 * 60
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
admin_only = role_required(COLLEGE_ADMIN, strict=True)  # College admin only; HoD, staff and students get 403


def _importer_class(kind):
    if kind not in IMPORTERS:
        raise Http404('Unknown import')
    return IMPORTERS[kind]


@admin_only
def import_home(request):
    batches = list(ImportBatch.objects.select_related('imported_by')[:20])
    for batch in batches:
        batch.blockers = services.rollback_blockers(batch)
    return render(request, 'bulk_import/home.html', {
        'kinds': [(k, label) for k, label in ImportBatch.KIND], 'batches': batches, 'page_title': 'Bulk import'})


@admin_only
@require_GET
def import_template(request, kind):
    _importer_class(kind)
    response = HttpResponse(services.template_workbook(kind), content_type=XLSX)
    response['Content-Disposition'] = f'attachment; filename="{kind}_import_template.xlsx"'
    return response


def _preview_context(kind, plan, update, sha, file_name, token=None):
    duplicate = ImportBatch.objects.filter(file_sha256=sha, status=ImportBatch.APPLIED).select_related('imported_by').first()
    return {
        'kind': kind, 'plan': plan, 'update_existing': update, 'file_name': file_name, 'token': token, 'duplicate': duplicate,
        'problems': plan.problems[:100], 'more_problems': max(0, len(plan.problems) - 100),
        'warnings': plan.warnings[:50], 'counts': (plan.count('create'), plan.count('update'), plan.count('nochange')),
        'page_title': 'Import ' + kind,
    }


@admin_only
def import_upload(request, kind):
    importer_class = _importer_class(kind)
    context = {'kind': kind, 'page_title': 'Import ' + kind, 'max_rows': parsing.MAX_ROWS}
    if request.method != 'POST':
        return render(request, 'bulk_import/upload.html', context)
    request.session.pop(SESSION_KEY, None)
    uploaded = request.FILES.get('file')
    if uploaded is None:
        messages.error(request, 'Choose an .xlsx file.')
        return render(request, 'bulk_import/upload.html', context)
    update = request.POST.get('update_existing') == 'on'
    try:
        sha = parsing.sha256_of(uploaded)
        headers, rows = parsing.read_workbook(uploaded)
    except parsing.FileRejected as exc:
        messages.error(request, str(exc))
        return render(request, 'bulk_import/upload.html', context)
    plan = importer_class(update).validate(headers, rows)
    token = secrets.token_urlsafe(24)
    request.session[SESSION_KEY] = {
        'token': token, 'kind': kind, 'update': update, 'sha': sha, 'file_name': uploaded.name, 'at': time.time(),
        'headers': headers, 'rows': [[n, v] for n, v in rows] if plan.ok else [],
        'problems': [list(p) for p in plan.problems],
    }
    return render(request, 'bulk_import/preview.html', _preview_context(kind, plan, update, sha, uploaded.name, token))


@admin_only
@require_POST
def import_confirm(request):
    pending = request.session.pop(SESSION_KEY, None)  # taken out first: a second click finds nothing
    token = request.POST.get('token', '')
    if (not pending or not secrets.compare_digest(pending['token'], token)
            or time.time() - pending['at'] > PREVIEW_SECONDS or pending['problems']):
        messages.error(request, 'That preview has expired or was already used. Upload the file again.')
        return redirect(reverse('import_home'))
    kind, update = pending['kind'], pending['update']
    importer = _importer_class(kind)(update)
    plan = importer.validate(pending['headers'], [(n, v) for n, v in pending['rows']])  # the data may have changed
    if not plan.ok:
        messages.error(request, 'The data changed since the preview, so nothing was imported. Upload the file again.')
        return redirect(reverse('import_home'))
    try:
        batch = services.confirm(kind, update, plan, pending['file_name'], pending['sha'], request.user)
    except IntegrityError:
        messages.error(request, 'A record in the file clashes with existing data, so nothing was imported.')
        return redirect(reverse('import_home'))
    messages.success(request, f'Imported {batch.created} new, {batch.updated} updated, {batch.unchanged} unchanged.')
    return redirect(reverse('import_home'))


@admin_only
@require_GET
def import_errors(request):
    pending = request.session.get(SESSION_KEY)
    if not pending:
        raise Http404('No preview to download')
    response = HttpResponse(services.error_sheet([tuple(p) for p in pending['problems']]), content_type=XLSX)
    response['Content-Disposition'] = 'attachment; filename="import_problems.xlsx"'
    return response


@admin_only
@require_POST
def import_rollback(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reasons = services.rollback(batch, request.user)
    if reasons:
        messages.error(request, 'Cannot roll back: ' + '; '.join(reasons) + '.')
    else:
        messages.success(request, 'Import rolled back: the records it created were removed.')
    return redirect(reverse('import_home'))
