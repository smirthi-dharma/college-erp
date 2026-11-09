"""The role field must behave the same however it is supplied (the inherited signals compared ints)."""
from django.db import transaction
from django_tenants.utils import tenant_context

from colleges.tests.base import TwoCollegeTestCase


class UserTypeTests(TwoCollegeTestCase):
    def create(self, email, user_type):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            return CustomUser.objects.create_user(email, 'a-long-password-1', first_name='F', last_name='L',
                                                  user_type=user_type).pk

    def test_profile_is_created_for_string_and_int_user_types(self):
        from main_app.models import Admin, Staff, Student
        cases = [('h1@a.in', '1', Admin), ('h2@a.in', 1, Admin), ('t1@a.in', '2', Staff),
                 ('t2@a.in', 2, Staff), ('s1@a.in', '3', Student), ('s2@a.in', 3, Student)]
        for email, user_type, model in cases:
            pk = self.create(email, user_type)
            with tenant_context(self.college_a):
                self.assertTrue(model.objects.filter(admin_id=pk).exists(), (email, user_type))

    def test_user_type_is_always_stored_as_a_string(self):
        from main_app.models import CustomUser
        pk = self.create('n1@a.in', 3)
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.get(pk=pk).user_type, '3')

    def test_user_without_explicit_type_is_not_an_admin(self):
        # the inherited default was 1 (HoD): any code path that forgot the type created an administrator
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser(email='nodefault@a.in')
            user.set_password('a-long-password-1')  # pragma: allowlist secret
            user.save()
            self.assertNotEqual(user.user_type, '1')
            self.assertEqual(user.user_type, '3')

    def test_superuser_is_an_admin_with_a_profile(self):
        from main_app.models import Admin, CustomUser
        with tenant_context(self.college_a), transaction.atomic():
            su = CustomUser.objects.create_superuser('root@a.in', 'a-long-password-1', first_name='R', last_name='S')
            self.assertEqual(su.user_type, '1')
            self.assertTrue(Admin.objects.filter(admin=su).exists())

    def test_invalid_user_type_is_rejected(self):
        from django.core.exceptions import ValidationError

        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser(email='bad@a.in', user_type='9')
            with self.assertRaises(ValidationError):
                user.full_clean(exclude=['password', 'profile_pic', 'address', 'gender', 'fcm_token'])


class CollegeAdminRoleTests(TwoCollegeTestCase):
    """The inherited role "1" (called HOD) is now College admin: same stored value, same powers."""

    def test_the_stored_value_is_unchanged_and_the_label_is_college_admin(self):
        from main_app.models import CustomUser
        self.assertEqual(CustomUser.COLLEGE_ADMIN, '1')
        self.assertEqual(dict(CustomUser.USER_TYPE)['1'], 'College admin')
        self.assertFalse(hasattr(CustomUser, 'HOD'))

    def test_a_user_stored_with_1_by_the_old_code_is_a_college_admin(self):
        # insert exactly what the pre-rename code stored, bypassing the new model code
        from django.db import connection
        with tenant_context(self.college_a), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO main_app_customuser (password, is_superuser, first_name, last_name, is_staff, is_active,"
                " date_joined, email, user_type, gender, profile_pic, address, phone, fcm_token, created_at, updated_at)"
                " VALUES ('x', false, 'Old', 'Admin', false, true, now(), 'old@a.in', '1', 'M', '', '', '', '', now(), now())")
        from django.test import Client

        from colleges.tests.base import EMAIL_BACKEND, HOST_A
        from main_app.access import COLLEGE_ADMIN
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='old@a.in')
            self.assertEqual((user.user_type, user.get_user_type_display()), (COLLEGE_ADMIN, 'College admin'))
            client = Client(HTTP_HOST=HOST_A)
            client.force_login(user, backend=EMAIL_BACKEND)
        self.assertEqual(client.get('/admin/home/').status_code, 200)

    def test_renaming_the_label_changes_no_database_column(self):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('sqlmigrate', 'main_app', '0008', stdout=out)
        sql = out.getvalue().upper()
        self.assertIn('NO-OP', sql)  # state-only: the choices live in Django, not in the column
        for statement in ('ALTER TABLE', 'UPDATE ', 'INSERT ', 'DROP '):
            self.assertNotIn(statement, sql)
