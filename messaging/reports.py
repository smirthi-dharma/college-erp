"""Numbers for the daily report and the monthly usage report. One place, so the pages, the CSVs and the tests agree."""
import datetime
from collections import Counter, OrderedDict
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from main_app.models import Attendance

from .models import DigestMessage, DigestRun, DigestSettings

COLUMNS = OrderedDict([
    ('eligible', 'Guardians to tell'), ('sent', 'Sent'), ('delivered', 'Delivered'), ('read', 'Read'),
    ('failed', 'Failed'), ('unknown', 'Unclear'), ('skipped_no_consent', 'No consent'), ('skipped_no_phone', 'No phone'),
    ('cancelled', 'Cancelled'), ('in_progress', 'In progress'), ('dry_run', 'Dry run'),
])


def daily_counts(day, mode):
    by_status = Counter(DigestMessage.objects.filter(digest_date=day, mode=mode).values_list('status', flat=True))
    accepted = sum(by_status[s] for s in DigestMessage.ACCEPTED)
    delivered = by_status[DigestMessage.DELIVERED] + by_status[DigestMessage.READ]
    return {
        'eligible': sum(by_status.values()), 'sent': accepted, 'delivered': delivered, 'read': by_status[DigestMessage.READ],
        'failed': by_status[DigestMessage.FAILED], 'unknown': by_status[DigestMessage.UNKNOWN],
        'skipped_no_consent': by_status[DigestMessage.SKIPPED_CONSENT], 'skipped_no_phone': by_status[DigestMessage.SKIPPED_PHONE],
        'cancelled': by_status[DigestMessage.CANCELLED], 'in_progress': by_status[DigestMessage.QUEUED] + by_status[DigestMessage.SENDING],
        'dry_run': by_status[DigestMessage.DRY_RUN],
    }


def late_marked(day):
    """Classes of that day marked after the send time: they were not in the digest (a second message is never sent)."""
    cutoff = timezone.make_aware(datetime.datetime.combine(day, DigestSettings.current().send_time))
    return Attendance.objects.filter(date=day, created_at__gt=cutoff).count()


def day_report(day):
    live = DigestRun.objects.filter(date=day, mode=DigestRun.LIVE).first()
    dry = DigestRun.objects.filter(date=day, mode=DigestRun.DRY).first()
    return {
        'day': day, 'counts': daily_counts(day, DigestRun.LIVE), 'dry_counts': daily_counts(day, DigestRun.DRY),
        'live_run': live, 'dry_run': dry, 'late_marked': late_marked(day),
        'messages': DigestMessage.objects.filter(digest_date=day).select_related('guardian').order_by('mode', 'status', 'id'),
    }


def month_usage(year, month):
    """Live messages Meta accepted in a month, per category: count and estimated cost."""
    rows = (DigestMessage.objects.filter(mode=DigestRun.LIVE, status__in=DigestMessage.ACCEPTED,
                                         digest_date__year=year, digest_date__month=month)
            .values('category').annotate(count=Count('id'), cost=Sum('billed_rate')).order_by('category'))
    result = [{'category': r['category'] or 'unknown', 'count': r['count'], 'cost': r['cost'] or Decimal('0')} for r in rows]
    return {'rows': result, 'total_count': sum(r['count'] for r in result), 'total_cost': sum((r['cost'] for r in result), Decimal('0'))}
