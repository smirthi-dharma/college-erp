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


_SHARED = {}


def _make_college(schema, name, host):
    connection.set_schema_to_public()
    college = College.objects.filter(schema_name=schema).first()  # a kept test database may already have it
    if college is None:
        college = College(schema_name=schema, name=name)
        college.save()  # auto-creates the schema and runs tenant migrations
    Domain.objects.get_or_create(tenant=college, domain=host, defaults={'is_primary': True})
    return college


def _shared_colleges():
    if not _SHARED:
        _SHARED['a'] = _make_college('college_a', 'College A', HOST_A)
        _SHARED['b'] = _make_college('college_b', 'College B', HOST_B)
    connection.set_schema_to_public()
    return _SHARED['a'], _SHARED['b']


# DEBUG=True so tests do not depend on the whitenoise static manifest or on
# reCAPTCHA keys (login skips the captcha only when DEBUG and no keys).
@override_settings(DEBUG=True, ALLOWED_HOSTS=['.testserver', 'testserver'],
                   RECAPTCHA_SITE_KEY='', RECAPTCHA_SECRET_KEY='')
class TwoCollegeTestCase(TestCase):
    """College A and College B, created once per test process and shared by every test class.

    Creating a schema runs every tenant migration, which used to be repeated for each of ~70 test classes and was
    most of the suite's run time. The colleges are committed once (outside Django's per-class transaction, because
    creating a schema inside it fails with "pending trigger events") and then reused. Everything a test writes,
    including data migrations it runs, happens inside the test's own transaction and is rolled back, so tests still
    start from empty colleges. With ``--parallel`` each worker process has its own database and creates its own pair.
    """

    @classmethod
    def setUpClass(cls):
        cls.college_a, cls.college_b = _shared_colleges()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

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
