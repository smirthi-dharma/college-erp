"""Deleting old message rows (they hold child names): keep each college's log for `retention_months`, default 12.

Only DigestMessage rows (and, with them, their delivery events) and empty DigestRun rows go. Consent records, guardians and the
audit log are not touched. Each purge writes one audit entry saying how many rows were removed and the cutoff date.
"""
import datetime

from django.utils import timezone

from audit_trail.models import AuditLog

from .models import DigestMessage, DigestRun, DigestSettings


def months_before(day, months):
    """The same day `months` earlier (the last day of the month when that month is shorter)."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    next_first = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_first - datetime.timedelta(days=1)).day
    return datetime.date(year, month, min(day.day, last_day))


def cutoff(today=None, months=None):
    """Messages dated BEFORE this day are deleted; a message exactly `months` old is kept."""
    months = months or DigestSettings.current().retention_months
    return months_before(today or timezone.localdate(), months)


def purge(now=None, dry_run=False):
    """Delete messages older than the college's retention. Returns (messages, runs) deleted, or would be deleted."""
    now = now or timezone.now()
    settings = DigestSettings.current()
    limit = cutoff(timezone.localtime(now).date(), settings.retention_months)
    old = DigestMessage.objects.filter(digest_date__lt=limit)
    message_count = old.count()
    run_count = DigestRun.objects.filter(date__lt=limit).count()
    if dry_run:
        return message_count, run_count
    if message_count or run_count:
        old.delete()  # delivery events go with their message
        DigestRun.objects.filter(date__lt=limit, messages__isnull=True).delete()
        AuditLog.objects.create(
            action=AuditLog.Action.DELETE, source='system', model_label='messaging.DigestMessage', object_pk='retention',
            object_repr='%d WhatsApp message record(s) older than %s' % (message_count, limit.isoformat()),
            changes={'deleted_messages': [message_count, None], 'deleted_runs': [run_count, None], 'older_than': [limit.isoformat(), None]})
    # Bookkeeping, not a decision: written with update() so a nightly run adds no "settings changed" audit entry (the purge itself
    # is audited above, only when something was deleted).
    DigestSettings.objects.filter(pk=settings.pk).update(last_purge_at=now, last_purge_count=message_count)
    return message_count, run_count
