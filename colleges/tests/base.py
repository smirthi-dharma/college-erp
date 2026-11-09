"""Shared fixtures for tenant-isolation tests.

Design under test (not yet built; these tests are the spec):

* ``colleges.models.College``  - the tenant model (django_tenants TenantMixin),
  one PostgreSQL schema per college.
* ``colleges.models.Domain``   - one subdomain per college (DomainMixin).
* All ERP data (``main_app``) and Django sessions/auth live in the tenant schema.
* The public schema holds only the college registry, never college data.
"""
from django.db import connection
from django.test import TestCase, override_settings
from django_tenants.utils import tenant_context

from colleges.models import College, Domain

HOST_A = 'a.testserver'
HOST_B = 'b.testserver'
PASSWORD = 'Sup3r-secret-pw'  # pragma: allowlist secret
EMAIL_BACKEND = 'main_app.EmailBackend.EmailBackend'

# DEBUG=True so tests do not depend on the whitenoise static manifest or on
# reCAPTCHA keys (login skips the captcha only when DEBUG and no keys).
@override_settings(DEBUG=True, ALLOWED_HOSTS=['.testserver', 'testserver'],
                   RECAPTCHA_SITE_KEY='', RECAPTCHA_SECRET_KEY='')
class TwoCollegeTestCase(TestCase):
    """Creates College A and College B once per test class."""

    # Colleges are created *outside* Django's per-class transaction (before
    # super().setUpClass()) because creating/dropping a schema inside it fails
    # with "pending trigger events". Per-test data is still rolled back.
    @classmethod
    def setUpClass(cls):
        cls.college_a = cls._make_college('college_a', 'College A', HOST_A)
        cls.college_b = cls._make_college('college_b', 'College B', HOST_B)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        for college in (cls.college_a, cls.college_b):
            Domain.objects.filter(tenant=college).delete()
            college.delete(force_drop=True)

    @staticmethod
    def _make_college(schema, name, host):
        college = College(schema_name=schema, name=name)
        college.save()  # auto-creates the schema and runs tenant migrations
        Domain.objects.create(tenant=college, domain=host, is_primary=True)
        return college

    def tearDown(self):
        connection.set_schema_to_public()
        super().tearDown()

    # -- helpers -----------------------------------------------------------
    def make_user(self, college, email, user_type, first, last):
        """Create a user (and its role profile) inside ``college``'s schema.

        ``user_type`` is passed as an int on purpose: the existing signal in
        main_app.models compares it to ints (known string-vs-int bug, see gap list).
        """
        from main_app.models import CustomUser
        with tenant_context(college):
            return CustomUser.objects.create_user(
                email, PASSWORD, first_name=first, last_name=last, user_type=user_type)

    def client_for(self, college, user):
        """A test client bound to ``college``'s host, logged in as ``user``."""
        from django.test import Client
        host = HOST_A if college is self.college_a else HOST_B
        client = Client(HTTP_HOST=host)
        with tenant_context(college):  # session row must land in this schema
            client.force_login(user, backend=EMAIL_BACKEND)
        return client

    def anon_client(self, host):
        from django.test import Client
        return Client(HTTP_HOST=host)
