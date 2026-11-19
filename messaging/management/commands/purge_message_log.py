from django.core.management.base import BaseCommand
from django_tenants.utils import tenant_context

from colleges.models import College
from messaging import retention


class Command(BaseCommand):
    help = ('Delete WhatsApp message records older than each college\'s retention (default 12 months). '
            'Runs nightly by itself (Celery beat); use this to run it by hand or from cron. --dry-run deletes nothing.')

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='One college only (default: all).')
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be deleted.')

    def handle(self, *args, **opts):
        colleges = College.objects.filter(is_active=True).exclude(schema_name='public')
        if opts['schema']:
            colleges = colleges.filter(schema_name=opts['schema'])
        for college in colleges:
            with tenant_context(college):
                messages, runs = retention.purge(dry_run=opts['dry_run'])
            self.stdout.write('%s: %d message record(s), %d run(s) %s' % (
                college.schema_name, messages, runs, 'would be deleted' if opts['dry_run'] else 'deleted'))
