from django.core.management.base import BaseCommand
from django_tenants.utils import tenant_context

from colleges.models import College
from messaging import services


class Command(BaseCommand):
    help = ('Run the WhatsApp absence digest now, in this process (no Redis needed): start today\'s run if it is due '
            'and send its messages. Safe to run repeatedly or from cron: a day is never sent twice.')

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='One college only (default: all).')
        parser.add_argument('--ignore-time', action='store_true', help='Do not wait for the college\'s send time.')

    def handle(self, *args, **opts):
        colleges = College.objects.filter(is_active=True).exclude(schema_name='public')
        if opts['schema']:
            colleges = colleges.filter(schema_name=opts['schema'])
        for college in colleges:
            with tenant_context(college):
                run, ids = services.start_run(ignore_time=opts['ignore_time'])
                outcomes = [services.send_message(i)[0] for i in ids]
                self.stdout.write('%s: %s' % (college.schema_name, 'nothing due' if run is None else
                                              '%s run for %s, %d to send, %d handled' % (run.mode, run.date, len(ids), len(outcomes))))
