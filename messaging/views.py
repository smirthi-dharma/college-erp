import csv
import datetime
import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from main_app.access import COLLEGE_ADMIN, public_view, role_required
from main_app.models import Guardian

from . import reports, services, tasks
from . import webhook as hook
from .forms import SettingsForm
from .models import CollegeSecret, DigestMessage, DigestSettings

admin_only = role_required(COLLEGE_ADMIN, strict=True)  # college admin only; teacher, student and HoD get 403


@admin_only
@require_http_methods(['GET', 'POST'])
def settings_page(request):
    settings = DigestSettings.current()
    form = SettingsForm(request.POST or None, instance=settings)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        if form.cleaned_data['clear_attention']:
            saved.state, saved.state_reason = DigestSettings.OK, ''
        saved.save()
        if form.cleaned_data['access_token']:
            CollegeSecret.put(services.TOKEN_SECRET, form.cleaned_data['access_token'])
        messages.success(request, 'WhatsApp digest settings saved.')
        return redirect('messaging_settings')
    secret = CollegeSecret.objects.filter(name=services.TOKEN_SECRET).first()
    return render(request, 'messaging/settings.html', {
        'form': form, 'settings': settings, 'token_last4': secret.last4 if secret else '', 'page_title': 'WhatsApp digest'})


def _parse_day(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return timezone.localdate()


@admin_only
@require_GET
def daily_report(request):
    day = _parse_day(request.GET.get('date'))
    return render(request, 'messaging/report.html', {
        'report': reports.day_report(day), 'columns': reports.COLUMNS, 'settings': DigestSettings.current(),
        'page_title': 'WhatsApp digest: daily report'})


@admin_only
@require_GET
def daily_report_csv(request):
    day = _parse_day(request.GET.get('date'))
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="whatsapp_digest_%s.csv"' % day.isoformat()
    writer = csv.writer(response)
    writer.writerow(['date', 'mode', 'guardian_id', 'status', 'attempts', 'language', 'error'])
    for m in reports.day_report(day)['messages']:
        writer.writerow([m.digest_date, m.mode, m.guardian_id, m.status, m.attempts, m.language, _safe(m.last_error)])
    return response


def _safe(value):
    """Stop spreadsheet formula injection from text we did not write (Meta's error messages)."""
    return "'" + value if value[:1] in ('=', '+', '-', '@') else value


def _month(text):
    try:
        year, month = (int(part) for part in (text or '').split('-'))
        datetime.date(year, month, 1)
        return year, month
    except (TypeError, ValueError):
        today = timezone.localdate()
        return today.year, today.month


@admin_only
@require_GET
def usage_report(request):
    year, month = _month(request.GET.get('month'))
    return render(request, 'messaging/usage.html', {
        'usage': reports.month_usage(year, month), 'month': '%04d-%02d' % (year, month), 'page_title': 'WhatsApp usage'})


@admin_only
@require_GET
def usage_csv(request):
    year, month = _month(request.GET.get('month'))
    usage = reports.month_usage(year, month)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="whatsapp_usage_%04d-%02d.csv"' % (year, month)
    writer = csv.writer(response)
    writer.writerow(['month', 'category', 'messages', 'estimated_cost_inr'])
    for row in usage['rows']:
        writer.writerow(['%04d-%02d' % (year, month), row['category'], row['count'], row['cost']])
    writer.writerow(['%04d-%02d' % (year, month), 'TOTAL', usage['total_count'], usage['total_cost']])
    return response


@admin_only
@require_http_methods(['GET', 'POST'])
def consent_page(request):
    if request.method == 'POST':
        guardian = get_object_or_404(Guardian, pk=request.POST.get('guardian'))
        value, source = request.POST.get('value', ''), request.POST.get('source', '').strip()
        if value not in (Guardian.CONSENT_YES, Guardian.CONSENT_NO):
            messages.error(request, 'Choose yes or no.')
        elif value == Guardian.CONSENT_YES and not source:
            messages.error(request, 'Say how the consent was obtained (for example "signed admission form").')
        else:
            services.set_consent(guardian, value, source or 'Recorded by college admin')
            messages.success(request, 'Consent recorded.')
        return redirect(request.get_full_path())
    q = request.GET.get('q', '').strip()
    guardians = Guardian.objects.order_by('full_name', 'id')
    if q:
        guardians = guardians.filter(full_name__icontains=q) | guardians.filter(phone__icontains=q)
    page = Paginator(guardians.order_by('full_name', 'id'), 50).get_page(request.GET.get('page'))
    return render(request, 'messaging/consent.html', {'page': page, 'q': q, 'page_title': 'WhatsApp consent'})


@admin_only
@require_http_methods(['GET', 'POST'])
def resend_message(request, pk):
    message = get_object_or_404(DigestMessage.objects.select_related('guardian'), pk=pk)
    if request.method == 'POST':
        if request.POST.get('confirm') != 'yes':
            messages.error(request, 'Tick the box to confirm that you understand the parent may receive the message twice.')
            return redirect(request.get_full_path())
        try:
            services.resend(message.pk, request.user)
        except services.ResendError as exc:
            messages.error(request, str(exc))
        else:
            tasks.send_message_task.delay(connection.schema_name, message.pk)
            messages.success(request, 'The message was queued to be sent again.')
        return redirect('%s?date=%s' % (reverse('messaging_report'), message.digest_date.isoformat()))
    return render(request, 'messaging/resend.html', {
        'message': message, 'limits': (services.MAX_RESENDS, services.MAX_RESEND_AGE_DAYS), 'page_title': 'Send a message again'})


@csrf_exempt
@public_view  # called by Meta, not by a logged-in user: protected by the signature check instead
@require_http_methods(['GET', 'POST'])
def webhook(request):
    if request.method == 'GET':  # Meta's one-time handshake when the webhook is registered
        if request.GET.get('hub.mode') == 'subscribe' and hook.verify_token_valid(request.GET.get('hub.verify_token')):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponseForbidden('Bad verify token.')
    if not hook.signature_valid(request.body, request.headers.get('X-Hub-Signature-256', '')):
        return HttpResponseForbidden('Bad signature.')
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest('Not JSON.')
    hook.process(payload)
    return HttpResponse('ok')

