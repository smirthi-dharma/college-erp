"""Acceptance checks 19-21: the Django admin is for platform staff only, needs a 2FA code, and its edits are audited."""
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_tenants.utils import tenant_context

from audit_trail.models import AuditLog
from colleges.tests.base import HOST_A, HOST_B, PASSWORD, TwoCollegeTestCase
from main_app.tests.world import WorldTestCase

OPS = 'ops@ourerp.in'
ADMIN = '/platform-admin/'


def token(device, offset=0):
    return '%06d' % totp(device.bin_key, device.step, device.t0, device.digits, device.drift + offset)


@override_settings(PLATFORM_ADMIN_EMAILS=[OPS])
class AdminTestBase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.ops = self.make_user(self.college_a, OPS, '1', 'Ops', 'Person')
        with tenant_context(self.college_a):
            self.ops.is_staff = self.ops.is_superuser = True
            self.ops.save()
            self.device = TOTPDevice.objects.create(user=self.ops, name='platform', confirmed=True)

    def login(self, email=OPS, password=PASSWORD, code=None, host=HOST_A):
        """Walk the admin login form like a person would. Returns the client."""
        client = Client(HTTP_HOST=host)
        client.get(ADMIN + 'login/')
        data = {'username': email, 'password': password, 'next': ADMIN,
                'otp_device': self.device.persistent_id, 'otp_token': code or ''}
        client.post(ADMIN + 'login/', data)
        return client

    def reaches_admin(self, client):
        resp = client.get(ADMIN)
        return resp.status_code == 200 and b'Site administration' in resp.content


class TwoFactorLoginTests(AdminTestBase):
    def test_password_and_a_valid_code_open_the_admin(self):
        self.assertTrue(self.reaches_admin(self.login(code=token(self.device))))

    def test_password_alone_does_not(self):
        self.assertFalse(self.reaches_admin(self.login(code='')))

    def test_a_wrong_code_does_not(self):
        wrong = '%06d' % ((int(token(self.device)) + 1) % 1000000)
        self.assertFalse(self.reaches_admin(self.login(code=wrong)))

    def test_a_wrong_password_with_a_valid_code_does_not(self):
        self.assertFalse(self.reaches_admin(self.login(password='not-the-password', code=token(self.device))))  # pragma: allowlist secret

    def test_a_used_code_cannot_be_replayed(self):
        code = token(self.device)
        self.assertTrue(self.reaches_admin(self.login(code=code)))
        self.assertFalse(self.reaches_admin(self.login(code=code)))

    def test_failed_codes_are_throttled(self):
        for _ in range(3):
            self.login(code='000000')
        with tenant_context(self.college_a):
            self.device.refresh_from_db()
            self.assertGreaterEqual(self.device.throttling_failure_count, 1)
        # even the correct code is refused right after failures
        self.assertFalse(self.reaches_admin(self.login(code=token(self.device))))

    def test_a_session_without_the_code_step_is_not_enough(self):
        client = self.as_user(self.ops)  # password-less force_login: never passed the OTP step
        self.assertFalse(self.reaches_admin(client))
        self.assertEqual(client.get(ADMIN).status_code, 302)

    def test_the_session_of_one_college_is_useless_in_another(self):
        client = self.login(code=token(self.device))
        self.assertTrue(self.reaches_admin(client))
        other = Client(HTTP_HOST=HOST_B)
        other.cookies = client.cookies
        self.assertFalse(self.reaches_admin(other))


class OnlyPlatformStaffTests(AdminTestBase):
    def test_college_users_never_reach_the_admin(self):
        for user in (self.admin, self.staff1, self.stud1):
            client = self.as_user(user)
            for path in (ADMIN, ADMIN + 'main_app/customuser/'):
                resp = client.get(path)
                self.assertIn(resp.status_code, (302, 403), (user.email, path))
                self.assertNotContains(resp, 'Site administration', status_code=resp.status_code)

    def test_a_college_hod_flagged_as_superuser_is_still_refused(self):
        with tenant_context(self.college_a):
            self.admin.is_staff = self.admin.is_superuser = True
            self.admin.save()
            device = TOTPDevice.objects.create(user=self.admin, name='d', confirmed=True)
        client = Client(HTTP_HOST=HOST_A)
        client.get(ADMIN + 'login/')
        client.post(ADMIN + 'login/', {'username': 'admin@a.in', 'password': PASSWORD, 'next': ADMIN,
                                       'otp_device': device.persistent_id, 'otp_token': token(device)})
        self.assertFalse(self.reaches_admin(client))

    def test_a_superuser_not_on_the_platform_list_is_refused_even_with_a_valid_code(self):
        with override_settings(PLATFORM_ADMIN_EMAILS=['someone-else@ourerp.in']):
            self.assertFalse(self.reaches_admin(self.login(code=token(self.device))))

    def test_an_empty_platform_list_locks_the_admin_for_everyone(self):
        with override_settings(PLATFORM_ADMIN_EMAILS=[]):
            self.assertFalse(self.reaches_admin(self.login(code=token(self.device))))

    def test_the_old_admin_path_is_gone(self):
        self.assertEqual(Client(HTTP_HOST=HOST_A).get('/admin/login/').status_code, 404)


class AdminEditsAreAuditedTests(AdminTestBase):
    """21: an admin edit writes an audit row with the admin as actor."""

    def setUp(self):
        super().setUp()
        with tenant_context(self.college_a):
            for user in (self.stud1, self.stud3):  # real users have a photo; the admin form requires one
                user.profile_pic, user.gender, user.address = 'photo.png', 'M', '1 Main Road'
                user.save()
            self.mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0
        self.client_ops = self.login(code=token(self.device))
        self.assertTrue(self.reaches_admin(self.client_ops))

    def new(self, **filters):
        with tenant_context(self.college_a):
            return list(AuditLog.objects.filter(id__gt=self.mark, **filters).order_by('id'))

    def test_editing_a_user_through_the_admin_is_logged_with_the_admin_as_actor(self):
        url = f'{ADMIN}main_app/customuser/{self.stud1.pk}/change/'
        form = self.client_ops.get(url).context['adminform'].form
        data = {name: ('' if value is None else value) for name, value in form.initial.items() if name != 'profile_pic'}
        data['first_name'] = 'Renamed'
        resp = self.client_ops.post(url, data)
        self.assertEqual(resp.status_code, 302, getattr(getattr(resp, 'context', None), 'get', lambda k: None)('errors'))
        entries = self.new(model_label='main_app.CustomUser', action='update')
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual((entry.actor_email, entry.object_pk), (OPS, str(self.stud1.pk)))
        self.assertEqual((entry.request_method, entry.source), ('POST', 'web'))
        self.assertTrue(entry.request_path.startswith(ADMIN))
        self.assertIn('first_name', entry.changes)

    def test_deleting_through_the_admin_is_logged(self):
        url = f'{ADMIN}main_app/customuser/{self.stud3.pk}/delete/'
        resp = self.client_ops.post(url, {'post': 'yes'})
        self.assertEqual(resp.status_code, 302)
        deleted = {(e.model_label, e.actor_email) for e in self.new(action='delete')}
        self.assertIn(('main_app.CustomUser', OPS), deleted)
        self.assertIn(('main_app.Student', OPS), deleted)

    def test_academic_structure_edits_through_the_admin_are_logged_too(self):
        resp = self.client_ops.post(f'{ADMIN}academics/department/add/', {'name': 'Mech', 'code': 'MECH'})
        self.assertEqual(resp.status_code, 302)
        (entry,) = self.new(model_label='academics.Department')
        self.assertEqual((entry.action, entry.actor_email), ('create', OPS))
        self.assertEqual(self.client_ops.get(f'{ADMIN}academics/enrolment/').status_code, 200)

    def test_a_refused_admin_cannot_change_anything(self):
        before = self.snapshot()
        client = self.as_user(self.admin)
        client.post(f'{ADMIN}main_app/customuser/{self.stud1.pk}/delete/', {'post': 'yes'})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e for e in self.new() if e.action in ('create', 'update', 'delete')], [])


@override_settings(PLATFORM_ADMIN_EMAILS=[OPS])
class CreatePlatformAdminCommandTests(TwoCollegeTestCase):
    def run_command(self, email, *extra):
        out = StringIO()
        with mock.patch('getpass.getpass', return_value=PASSWORD):
            call_command('create_platform_admin', 'college_a', email, *extra, stdout=out)
        return out.getvalue()

    def test_creates_a_superuser_and_a_device_and_prints_the_setup_link_once(self):
        output = self.run_command(OPS)
        self.assertIn('otpauth://totp/', output)
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email=OPS)
            self.assertTrue(user.is_staff and user.is_superuser)
            self.assertEqual(TOTPDevice.objects.filter(user=user, confirmed=True).count(), 1)
        self.assertNotIn('otpauth://', self.run_command(OPS))  # no second secret
        with tenant_context(self.college_a):
            self.assertEqual(TOTPDevice.objects.filter(user__email=OPS).count(), 1)

    def test_reset_replaces_the_device(self):
        self.run_command(OPS)
        with tenant_context(self.college_a):
            old = TOTPDevice.objects.get(user__email=OPS).key
        self.assertIn('otpauth://', self.run_command(OPS, '--reset-2fa'))
        with tenant_context(self.college_a):
            self.assertNotEqual(TOTPDevice.objects.get(user__email=OPS).key, old)

    def test_refuses_emails_that_are_not_on_the_platform_list(self):
        with self.assertRaises(CommandError):
            self.run_command('rando@gmail.com')
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            self.assertFalse(CustomUser.objects.filter(email='rando@gmail.com').exists())

    def test_refuses_weak_passwords(self):
        with mock.patch('getpass.getpass', return_value='1234'), self.assertRaises(CommandError):
            call_command('create_platform_admin', 'college_a', OPS, stdout=StringIO())
