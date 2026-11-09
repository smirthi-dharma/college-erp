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
