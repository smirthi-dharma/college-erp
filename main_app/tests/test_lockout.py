"""Login lockout (django-axes): 5 failures for the same email + address lock them out for 30 minutes."""
from datetime import timedelta

from django.test import Client
from django.utils import timezone
from django_tenants.utils import tenant_context

from audit_trail.models import AuditLog
from colleges.tests.base import HOST_A, HOST_B, PASSWORD
from main_app.tests.world import WorldTestCase

IP = '203.0.113.20'
WRONG = 'definitely-the-wrong-password'  # pragma: allowlist secret


class LockoutTests(WorldTestCase):
    def attempt(self, email='stud1@a.in', password=WRONG, ip=IP, host=HOST_A):
        client = Client(HTTP_HOST=host, REMOTE_ADDR=ip)
        resp = client.post('/doLogin/', {'email': email, 'password': password})
        return client, resp

    def logged_in(self, client):
        return client.get('/student/home/').status_code == 200

    def test_five_failures_lock_out_even_the_correct_password(self):
        for _ in range(4):
            self.assertEqual(self.attempt()[1].status_code, 302)  # ordinary "invalid details" redirect
        self.assertEqual(self.attempt()[1].status_code, 429)  # the 5th failure triggers the lockout
        client, resp = self.attempt(password=PASSWORD)  # right password, but locked
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(self.logged_in(client))

    def test_lockout_applies_to_unknown_emails_too_without_revealing_which_exist(self):
        for _ in range(5):
            _, last = self.attempt(email='nobody@a.in')
        self.assertEqual(last.status_code, 429)

    def test_a_successful_login_resets_the_count(self):
        for _ in range(4):
            self.attempt()
        client, resp = self.attempt(password=PASSWORD)
        self.assertTrue(self.logged_in(client))
        for _ in range(4):
            self.assertEqual(self.attempt()[1].status_code, 302)  # counting started again from zero

    def test_the_lock_is_per_email_and_address(self):
        for _ in range(5):
            self.attempt()
        self.assertEqual(self.attempt(email='stud2@a.in')[1].status_code, 302)  # other account, same address
        client, resp = self.attempt(password=PASSWORD, ip='198.51.100.77')  # same account, other address
        self.assertTrue(self.logged_in(client))

    def test_lockout_ends_after_the_cooloff_period(self):
        from axes.models import AccessAttempt
        for _ in range(5):
            self.attempt()
        with tenant_context(self.college_a):
            AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=31))
        client, resp = self.attempt(password=PASSWORD)
        self.assertTrue(self.logged_in(client))

    def test_lockouts_do_not_cross_colleges(self):
        self.make_user(self.college_b, 'stud1@a.in', '3', 'Same', 'Email')  # same email, other college
        for _ in range(5):
            self.attempt()
        client = Client(HTTP_HOST=HOST_B, REMOTE_ADDR=IP)
        self.assertEqual(client.post('/doLogin/', {'email': 'stud1@a.in', 'password': WRONG}).status_code, 302)

    def test_the_lockout_is_written_to_the_audit_log(self):
        mark = self.audit_mark()
        for _ in range(5):
            self.attempt()
        with tenant_context(self.college_a):
            actions = list(AuditLog.objects.filter(id__gt=mark).values_list('action', flat=True))
        self.assertEqual(actions.count('login_failed'), 5)
        self.assertEqual(actions.count('locked_out'), 1)

    def audit_mark(self):
        with tenant_context(self.college_a):
            return AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0

    def test_a_spoofed_forwarded_header_cannot_dodge_the_lockout(self):
        for i in range(5):
            client = Client(HTTP_HOST=HOST_A, REMOTE_ADDR=IP, HTTP_X_FORWARDED_FOR='10.9.8.%d' % i)
            client.post('/doLogin/', {'email': 'stud1@a.in', 'password': WRONG})
        client = Client(HTTP_HOST=HOST_A, REMOTE_ADDR=IP, HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(client.post('/doLogin/', {'email': 'stud1@a.in', 'password': PASSWORD}).status_code, 429)
