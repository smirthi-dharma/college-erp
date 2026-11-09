"""Tenant isolation: College A must never see College B's data.

Acceptance checks (CLAUDE.md non-negotiable #1). Each class covers one layer.
"""
from django.db import ProgrammingError, connection, transaction
from django_tenants.utils import schema_context, tenant_context

from .base import HOST_A, HOST_B, PASSWORD, TwoCollegeTestCase


class SchemaLayoutTests(TwoCollegeTestCase):
    """1. Each college has its own schema; the public schema holds no college data."""

    def _tables(self, schema):
        with connection.cursor() as cur:
            cur.execute("select table_name from information_schema.tables "
                        "where table_schema = %s", [schema])
            return {r[0] for r in cur.fetchall()}

    def test_each_college_has_its_own_schema_with_erp_tables(self):
        for college in (self.college_a, self.college_b):
            tables = self._tables(college.schema_name)
            self.assertIn('main_app_customuser', tables)
            self.assertIn('main_app_student', tables)
            self.assertIn('django_session', tables)

    def test_public_schema_has_no_college_data_tables(self):
        public = self._tables('public')
        self.assertIn('colleges_college', public)
        self.assertNotIn('main_app_customuser', public)
        self.assertNotIn('main_app_student', public)
        self.assertNotIn('django_session', public)

    def test_querying_college_data_from_public_schema_fails(self):
        from main_app.models import CustomUser
        with schema_context('public'):
            with self.assertRaises(ProgrammingError):
                with transaction.atomic():
                    CustomUser.objects.count()


class OrmIsolationTests(TwoCollegeTestCase):
    """2. Data written in one college is invisible in the other."""

    def test_users_are_invisible_across_colleges(self):
        from main_app.models import CustomUser
        self.make_user(self.college_a, 'x@a.in', 3, 'Asha', 'Alpha')
        self.make_user(self.college_b, 'y@b.in', 3, 'Bala', 'Beta')
        with tenant_context(self.college_a):
            self.assertEqual(list(CustomUser.objects.values_list('email', flat=True)), ['x@a.in'])
        with tenant_context(self.college_b):
            self.assertEqual(list(CustomUser.objects.values_list('email', flat=True)), ['y@b.in'])

    def test_same_email_may_exist_in_both_colleges(self):
        self.make_user(self.college_a, 'same@x.in', 2, 'Sam', 'A')
        self.make_user(self.college_b, 'same@x.in', 2, 'Sam', 'B')  # must not violate unique
        from main_app.models import CustomUser
        for college, last in ((self.college_a, 'A'), (self.college_b, 'B')):
            with tenant_context(college):
                self.assertEqual(CustomUser.objects.get(email='same@x.in').last_name, last)

    def test_academic_data_is_invisible_across_colleges(self):
        from main_app.models import Course, Student
        self.make_user(self.college_a, 's@a.in', 3, 'Stu', 'A')
        with tenant_context(self.college_a):
            Course.objects.create(name='A-only course')
        with tenant_context(self.college_b):
            self.assertEqual(Course.objects.count(), 0)
            self.assertEqual(Student.objects.count(), 0)

    def test_deleting_in_one_college_does_not_touch_the_other(self):
        from main_app.models import Course
        for college in (self.college_a, self.college_b):
            with tenant_context(college):
                Course.objects.create(name='Shared name')
        with tenant_context(self.college_a):
            Course.objects.all().delete()
        with tenant_context(self.college_b):
            self.assertEqual(Course.objects.count(), 1)

    def test_primary_keys_are_independent_per_college(self):
        # Each college has its own table and sequence, so an id issued in A
        # means nothing in B: a row created in A is not found by id in B.
        from main_app.models import Course
        with tenant_context(self.college_a):
            a1 = Course.objects.create(name='A1')
            a2 = Course.objects.create(name='A2')
        with tenant_context(self.college_b):
            Course.objects.create(name='B1')
            self.assertFalse(Course.objects.filter(name__in=['A1', 'A2']).exists())
            self.assertFalse(Course.objects.filter(pk__in=[a1.pk, a2.pk], name__startswith='A').exists())


class HttpIsolationTests(TwoCollegeTestCase):
    """3. Through the website: host decides the college, nothing crosses over."""

    def setUp(self):
        super().setUp()
        self.admin_a = self.make_user(self.college_a, 'admin@a.in', 1, 'Ada', 'Admin')
        self.admin_b = self.make_user(self.college_b, 'admin@b.in', 1, 'Bob', 'Admin')
        self.make_user(self.college_a, 'stu@a.in', 3, 'Alpha', 'Studentone')
        self.make_user(self.college_b, 'stu1@b.in', 3, 'Beta', 'Studentone')
        self.make_user(self.college_b, 'stu2@b.in', 3, 'Beta', 'Studenttwo')
        self.make_user(self.college_b, 'stu3@b.in', 3, 'Beta', 'Studentthree')

    def test_admin_home_works_on_own_college_host(self):
        # positive control: guards against the isolation tests passing vacuously
        client = self.client_for(self.college_a, self.admin_a)
        self.assertEqual(client.get('/admin/home/').status_code, 200)

    def test_student_list_shows_only_own_college(self):
        resp_a = self.client_for(self.college_a, self.admin_a).get('/student/manage/')
        resp_b = self.client_for(self.college_b, self.admin_b).get('/student/manage/')
        self.assertContains(resp_a, 'stu@a.in')
        self.assertNotContains(resp_a, '@b.in')
        self.assertContains(resp_b, 'stu1@b.in')
        self.assertNotContains(resp_b, '@a.in')

    def test_other_colleges_record_id_is_not_reachable(self):
        # Pick a student id that exists only in College B (sequences are not
        # rolled back between tests, so compute it instead of assuming 3).
        from main_app.models import Student
        with tenant_context(self.college_a):
            ids_a = set(Student.objects.values_list('pk', flat=True))
        with tenant_context(self.college_b):
            only_b = sorted(set(Student.objects.values_list('pk', flat=True)) - ids_a)
        self.assertTrue(only_b, 'test setup: need an id present in B but not A')
        client = self.client_for(self.college_a, self.admin_a)
        resp = client.get('/student/edit/%d' % only_b[0])
        self.assertEqual(resp.status_code, 404)
        self.assertNotContains(resp, 'Beta', status_code=404)

    def test_session_cookie_from_a_is_useless_on_b(self):
        client_a = self.client_for(self.college_a, self.admin_a)
        stolen = client_a.cookies['sessionid'].value
        client_b = self.anon_client(HOST_B)
        client_b.cookies['sessionid'] = stolen
        resp = client_b.get('/admin/home/')
        self.assertEqual(resp.status_code, 302)  # bounced to login, not served

    def test_college_a_credentials_do_not_log_in_on_b(self):
        client = self.anon_client(HOST_B)
        client.post('/doLogin/', {'email': 'admin@a.in', 'password': PASSWORD})
        self.assertEqual(client.get('/admin/home/').status_code, 302)

    def test_own_credentials_do_log_in_on_own_host(self):
        client = self.anon_client(HOST_A)
        resp = client.post('/doLogin/', {'email': 'admin@a.in', 'password': PASSWORD})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(client.get('/admin/home/').status_code, 200)

    def test_unknown_subdomain_serves_no_college(self):
        client = self.anon_client('nobody.testserver')
        self.assertEqual(client.get('/').status_code, 404)

    def test_new_session_rows_land_in_the_colleges_own_schema(self):
        from django.contrib.sessions.models import Session
        self.client_for(self.college_a, self.admin_a)
        with tenant_context(self.college_a):
            self.assertEqual(Session.objects.count(), 1)
        with tenant_context(self.college_b):
            self.assertEqual(Session.objects.count(), 0)


class MediaIsolationTests(TwoCollegeTestCase):
    """4. Uploaded files (profile photos, documents) are stored per college."""

    def test_media_paths_are_separate_per_college(self):
        from django.core.files.storage import default_storage
        with tenant_context(self.college_a):
            path_a = default_storage.path('photo.png')
        with tenant_context(self.college_b):
            path_b = default_storage.path('photo.png')
        self.assertIn('college_a', path_a)
        self.assertNotIn('college_b', path_a)
        self.assertIn('college_b', path_b)
        self.assertNotEqual(path_a, path_b)
