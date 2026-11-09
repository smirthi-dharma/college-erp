from django.core.management.base import BaseCommand
from django_tenants.utils import tenant_context

from colleges.models import College, Domain


class Command(BaseCommand):
    help = ('Create a college (own schema + subdomain) with one admin, staff and '
            'student user for local development. Safe to re-run.')

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='demo')
        parser.add_argument('--name', default='Demo College')
        parser.add_argument('--domain', default='demo.localhost')
        parser.add_argument('--password', default='demo-pass-123',
                            help='Dev password for the three demo users.')

    def handle(self, *args, **opts):
        from main_app.models import CustomUser

        college, created = College.objects.get_or_create(
            schema_name=opts['schema'], defaults={'name': opts['name']})
        Domain.objects.get_or_create(
            domain=opts['domain'], defaults={'tenant': college, 'is_primary': True})
        with tenant_context(college):
            # user_type is an int on purpose: the profile-creating signals compare ints.
            for email, user_type, last in (('admin', 1, 'Admin'), ('staff', 2, 'Staff'),
                                           ('student', 3, 'Student')):
                address = '%s@%s.in' % (email, opts['schema'])
                if not CustomUser.objects.filter(email=address).exists():
                    CustomUser.objects.create_user(
                        address, opts['password'], first_name='Demo',
                        last_name=last, user_type=user_type)
        self.stdout.write(self.style.SUCCESS(
            '%s college "%s" at http://%s:8000/ (users admin/staff/student@%s.in)' % (
                'Created' if created else 'Found', college.name, opts['domain'], opts['schema'])))
