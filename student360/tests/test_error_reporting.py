"""No personal data in error reports (DPDP Act), proved with real errors raised from pages that show student details.

A real Sentry client is started with the project's own settings and a transport that keeps what would have been sent. Errors
are then raised from a student's page, from a search, from an admin form post and from a log line, each carrying names, phone
numbers, e-mail addresses, roll numbers, addresses, dates of birth and a guardian's name in the message, the URL or the form.
Every event that would reach Sentry is searched for every one of those values.
"""
import json
import logging
from datetime import date
from unittest import mock

import sentry_sdk
from django.core.signals import request_finished, request_started
from django.core.wsgi import get_wsgi_application
from django.db import close_old_connections
from django.test import RequestFactory
from django_tenants.utils import tenant_context
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.transport import Transport

from college_management_system.observability import WITHHELD, init_sentry
from colleges.tests.base import HOST_A
from main_app.tests.helpers import STRONG, picture
from main_app.tests.world import WorldTestCase
from student360 import services

FIRST, LAST, ROLL, PHONE, DOB, ADDRESS = 'Karthikeyan', 'Venkatachalam', 'KV-2026-0042', '+919876500001', date(2004, 3, 5), '12 Secret Park Road'
GUARDIAN, GUARDIAN_PHONE = 'Gitanjali Raghunathan', '+919812345678'
NEW = {'first_name': 'Zainabunnisa', 'last_name': 'Quereshiwala', 'email': 'zainab.q@a.in', 'roll_number': 'ZQ-77-ROLL',
       'phone': '9123456780', 'address': '77 Hidden Lane'}
SECRETS = [FIRST, LAST, ROLL, PHONE.lstrip('+'), '9876500001', '2004-03-05', 'Secret Park', GUARDIAN, 'Gitanjali', GUARDIAN_PHONE.lstrip('+'),
           '9812345678', 'stud1@a.in', *NEW.values(), '9123456780']
SECRETS = [s for s in SECRETS if s not in ('a.in',)]


class Capture(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                self.events.append(event)


class ErrorReportingTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        from main_app.models import CustomUser, Guardian, StudentGuardian
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.stud1.pk)
            user.first_name, user.last_name, user.phone, user.address = FIRST, LAST, PHONE, ADDRESS
            user.save()
            student = user.student
            student.roll_number, student.date_of_birth = ROLL, DOB
            student.save()
            guardian = Guardian.objects.create(full_name=GUARDIAN, phone=GUARDIAN_PHONE)
            StudentGuardian.objects.create(student=student, guardian=guardian, relation='mother', is_primary=True)
            self.student_pk = student.pk
        self.transport = Capture()
        init_sentry({'SENTRY_DSN': 'https://key@o1.ingest.example.com/1', 'SENTRY_ENVIRONMENT': 'test'}, transport=self.transport)
        self.addCleanup(self.stop_sentry)

    @staticmethod
    def stop_sentry():
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # no DSN: back to switched off for the other tests

    def wsgi(self, method, path, user, data=None):
        """Call the real WSGI application the way gunicorn does, with the Sentry WSGI layer (the test client skips both).

        That layer is what puts the URL, query string, headers and cookies into a real event, so it has to be in the test.
        Returns the HTTP status. Django's test client is not used because it bypasses the WSGI layer.
        """
        token = 'c' * 32  # a CSRF token the app will accept: the test client would skip the check, the real handler does not
        cookies = '; '.join([f'{name}={morsel.value}' for name, morsel in self.as_user(user).cookies.items()] + [f'csrftoken={token}'])
        factory = RequestFactory(HTTP_HOST=HOST_A, HTTP_COOKIE=cookies, HTTP_USER_AGENT='Mozilla/5.0 (Android; test)',
                                 HTTP_AUTHORIZATION='Bearer not-a-real-token')
        environ = factory.post(path, dict(data or {}, csrfmiddlewaretoken=token)).environ if method == 'post' else factory.get(path).environ
        status = []
        app = SentryWsgiMiddleware(get_wsgi_application())
        request_started.disconnect(close_old_connections)  # as the test client does: keep the test's database connection open
        request_finished.disconnect(close_old_connections)
        try:
            b''.join(app(environ, lambda s, headers, exc_info=None: status.append(s)))
        finally:
            request_started.connect(close_old_connections)
            request_finished.connect(close_old_connections)
        return int(status[0].split()[0])

    def sent(self):
        sentry_sdk.flush()
        return self.transport.events

    def secret_text(self):
        return f'cannot build {FIRST} {LAST} roll {ROLL} phone {PHONE} email stud1@a.in guardian {GUARDIAN} born {DOB} at {ADDRESS}'

    def assert_clean(self, events, expect_type=None):
        self.assertTrue(events, 'nothing was captured: the test would prove nothing')
        for event in events:
            dumped = json.dumps(event, default=str)
            for secret in SECRETS:
                self.assertNotIn(secret, dumped, f'{secret!r} would be sent to Sentry')
            for forbidden in ('sessionid', 'csrftoken', 'Cookie', STRONG):  # the password VALUE; the word appears in source lines
                self.assertNotIn(forbidden, dumped, forbidden)
        if expect_type:
            kinds = [v['type'] for e in events for v in (e.get('exception') or {}).get('values', [])]
            self.assertIn(expect_type, kinds)

    def test_an_error_on_a_students_page_carries_no_personal_data_but_still_helps_debugging(self):
        with mock.patch.object(services, 'build', side_effect=RuntimeError(self.secret_text())):
            status = self.wsgi('get', f'/students/{self.student_pk}/?q={LAST}&roll={ROLL}', self.admin)
        self.assertEqual(status, 500)
        events = self.sent()
        self.assert_clean(events, 'RuntimeError')
        event = next(e for e in events if e.get('exception'))
        self.assertEqual(event['exception']['values'][0]['value'], WITHHELD)
        self.assertTrue(event['request']['url'].endswith(f'/students/{self.student_pk}/'), event['request'])  # path stays, query gone
        self.assertEqual(event['request']['method'], 'GET')
        self.assertEqual(set(event['request']) - {'url', 'method'}, set(), 'only the URL and the method may remain of the request')
        self.assertEqual(event['tags']['college'], 'college_a')
        frames = event['exception']['values'][0]['stacktrace']['frames']
        self.assertTrue(any('student360' in (f.get('module') or f.get('filename') or '') for f in frames), 'the stack must stay')
        self.assertTrue(all('vars' not in f for f in frames))

    def test_a_search_for_a_name_does_not_leak_the_name_through_the_url(self):
        with mock.patch.object(services, 'list_alerts', side_effect=ValueError('failed')):
            status = self.wsgi('get', f'/students/?q={FIRST}+{LAST}&section=1', self.admin)
        self.assertEqual(status, 500)
        self.assert_clean(self.sent(), 'ValueError')

    def test_a_failed_form_post_logged_with_its_data_carries_none_of_it(self):
        data = {'first_name': NEW['first_name'], 'last_name': NEW['last_name'], 'email': NEW['email'], 'gender': 'F', 'password': STRONG,
                'address': NEW['address'], 'phone': NEW['phone'], 'roll_number': NEW['roll_number'], 'date_of_birth': '2005-01-02',
                'section': self.sec1, 'profile_pic': picture(f"{NEW['first_name']}-aadhaar.png")}
        with mock.patch('main_app.hod_views.CustomUser.objects.create_user',
                        side_effect=RuntimeError(f"could not save {NEW['first_name']} {NEW['phone']} {NEW['email']}")):
            status = self.wsgi('post', '/student/add/', self.admin, data)
        self.assertEqual(status, 200)  # the view catches it, logs it with logger.exception, shows a message
        events = self.sent()
        self.assert_clean(events, 'RuntimeError')
        self.assertTrue(any(e.get('logentry') or e.get('exception') for e in events))
        self.assertNotIn('aadhaar', json.dumps(events, default=str))  # an uploaded file's name can be a person's name

    def test_a_log_line_with_a_persons_details_is_withheld(self):
        logging.getLogger('some.module').error('Digest failed for %s (%s, %s)', f'{FIRST} {LAST}', PHONE, 'stud1@a.in')
        events = self.sent()
        self.assert_clean(events)
        self.assertEqual(events[0]['logentry']['formatted'], WITHHELD)

    def test_an_exception_raised_outside_a_request_is_clean_too(self):
        try:
            raise OSError(self.secret_text())
        except OSError:
            sentry_sdk.capture_exception()
        self.assert_clean(self.sent(), 'OSError')

    def test_with_no_dsn_nothing_is_sent_at_all(self):
        self.stop_sentry()
        transport = Capture()
        self.assertFalse(init_sentry({}, transport=transport))
        logging.getLogger('x').error('boom %s', FIRST)
        sentry_sdk.flush()
        self.assertEqual(transport.events, [])
