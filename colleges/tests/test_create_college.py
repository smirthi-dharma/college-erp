from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from django_tenants.utils import tenant_context

from colleges.models import College, Domain


class CreateCollegeTests(TransactionTestCase):
    """TransactionTestCase: creating a schema cannot happen inside a test transaction."""

    def tearDown(self):
        connection.set_schema_to_public()
        for college in College.objects.filter(schema_name='newco'):
            Domain.objects.filter(tenant=college).delete()
            college.delete(force_drop=True)

    def test_creates_an_isolated_college_with_one_admin(self):
        from main_app.models import CustomUser
        out = StringIO()
        call_command('create_college', 'newco', 'New Co', 'newco.example.in', 'Boss@Newco.in', stdout=out)
        self.assertIn('One-time password', out.getvalue())
        college = College.objects.get(schema_name='newco')
        self.assertTrue(Domain.objects.filter(tenant=college, domain='newco.example.in').exists())
        with tenant_context(college):
            admin = CustomUser.objects.get()
            self.assertEqual((admin.email, admin.user_type), ('boss@newco.in', CustomUser.COLLEGE_ADMIN))
        connection.set_schema_to_public()
        # nothing leaked into any other schema
        for other in College.objects.exclude(schema_name='newco'):
            with tenant_context(other):
                self.assertFalse(CustomUser.objects.filter(email='boss@newco.in').exists())

    def test_refuses_a_duplicate_schema_domain_or_bad_name(self):
        call_command('create_college', 'newco', 'New Co', 'newco.example.in', 'a@newco.in', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('create_college', 'newco', 'Again', 'other.example.in', 'a@x.in', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('create_college', 'other', 'Again', 'newco.example.in', 'a@x.in', stdout=StringIO())
        for bad in ('public', 'Bad-Name', 'has space'):
            with self.assertRaises(CommandError):
                call_command('create_college', bad, 'X', 'x.example.in', 'a@x.in', stdout=StringIO())
