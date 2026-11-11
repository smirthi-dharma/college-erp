import secrets

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from colleges.models import College, Domain


class Command(BaseCommand):
    help = ('Create a real college (own schema + domain) with its first college-admin user. '
            'Prints a one-time password; the admin should change it at first login. Refuses to reuse a schema or domain.')

    def add_arguments(self, parser):
        parser.add_argument('schema', help='Short lowercase schema name, e.g. nitc')
        parser.add_argument('name', help='College name')
        parser.add_argument('domain', help='Full host name, e.g. nitc.staging.ourerp.in')
        parser.add_argument('admin_email')

    def handle(self, *args, **opts):
        from main_app.models import CustomUser
        schema, domain, email = opts['schema'], opts['domain'].lower(), opts['admin_email'].strip().lower()
        if not schema.isidentifier() or schema != schema.lower() or schema in ('public', 'pg_catalog'):
            raise CommandError('Schema must be a lowercase identifier such as "nitc".')
        if College.objects.filter(schema_name=schema).exists():
            raise CommandError(f'A college with schema "{schema}" already exists.')
        if Domain.objects.filter(domain=domain).exists():
            raise CommandError(f'The domain {domain} is already in use.')
        college = College.objects.create(schema_name=schema, name=opts['name'])  # creates + migrates the schema
        Domain.objects.create(tenant=college, domain=domain, is_primary=True)
        password = secrets.token_urlsafe(12)
        with tenant_context(college):
            CustomUser.objects.create_user(email, password, first_name='College', last_name='Admin', user_type=1)
        self.stdout.write(self.style.SUCCESS(f'Created "{college.name}" at https://{domain}/'))
        self.stdout.write(f'College admin: {email}\nOne-time password (shown once): {password}')
