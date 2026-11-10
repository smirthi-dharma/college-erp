"""Shared fixture for the WhatsApp digest tests: College A from WorldTestCase plus guardians and a day of absences.

Day 2026-01-05, 17:30 India time (after the 17:00 send time):
  stud1 absent in period 2 of Maths;  stud2 (stud1's sibling) absent in periods 1 and 2;  stud3 absent in nothing.
  g_sib  (+919000000001, consent yes) is the primary guardian of stud1 AND stud2  -> ONE message for both
  g_other(+919000000002, consent yes) is the primary guardian of stud3             -> nothing to tell
No test may touch the network: requests.post is replaced by a function that fails the test.
"""
import datetime
import hmac
import json
import os
from hashlib import sha256
from unittest import mock

from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone
from django_tenants.utils import tenant_context

from main_app import whatsapp
from main_app.tests.world import WorldTestCase

DAY = datetime.date(2026, 1, 5)
NOW = timezone.make_aware(datetime.datetime(2026, 1, 5, 17, 30))
APP_SECRET, VERIFY_TOKEN = 'test-app-secret', 'test-verify-token'  # pragma: allowlist secret
KEY = Fernet.generate_key().decode()


def signed(payload, secret=APP_SECRET):
    body = json.dumps(payload).encode()
    return body, 'sha256=' + hmac.new(secret.encode(), body, sha256).hexdigest()


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class DigestTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        from main_app.models import Attendance, AttendanceReport, Guardian, StudentGuardian
        from messaging.models import DigestSettings
        env = mock.patch.dict(os.environ, {'WHATSAPP_APP_SECRET': APP_SECRET, 'WHATSAPP_VERIFY_TOKEN': VERIFY_TOKEN,
                                           'WHATSAPP_PHONE_NUMBER_ID': '111', 'WHATSAPP_ACCESS_TOKEN': 'platform-token-1234'})  # pragma: allowlist secret
        env.start()
        self.addCleanup(env.stop)
        net = mock.patch('main_app.whatsapp.requests.post', side_effect=AssertionError('a test tried to use the network'))
        net.start()
        self.addCleanup(net.stop)
        self.sent = []  # (phone, template name, language, params)
        transport = mock.patch('main_app.whatsapp._transport', side_effect=self.fake_transport)
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.transport_errors = []  # exceptions to raise on successive calls, then succeed
        with tenant_context(self.college_a):
            self.g_sib = Guardian.objects.create(full_name='Mr Sib', phone='+919000000001', whatsapp_consent='yes')
            self.g_other = Guardian.objects.create(full_name='Mrs Other', phone='+919000000002', whatsapp_consent='yes')
            for student, guardian in ((self.stud1, self.g_sib), (self.stud2, self.g_sib), (self.stud3, self.g_other)):
                StudentGuardian.objects.create(student=student.student, guardian=guardian, relation='father', is_primary=True)
            AttendanceReport.objects.filter(attendance_id=self.att1).delete()
            Attendance.objects.filter(pk=self.att1).update(date=DAY)  # Maths, period 1, on DAY
            AttendanceReport.objects.create(student=self.stud1.student, attendance_id=self.att1, status='present')
            AttendanceReport.objects.create(student=self.stud2.student, attendance_id=self.att1, status='absent')
            second = Attendance.objects.create(offering_id=self.sub1, date=DAY, period=2)
            AttendanceReport.objects.create(student=self.stud1.student, attendance=second, status='absent')
            AttendanceReport.objects.create(student=self.stud2.student, attendance=second, status='absent')
            DigestSettings.objects.update_or_create(pk=1, defaults={'enabled': True, 'dry_run': False})

    def fake_transport(self, phone, name, language, params, credentials):
        self.sent.append((phone, name, language, list(params)))
        if self.transport_errors:
            error = self.transport_errors.pop(0)
            if error is not None:
                raise error
        return whatsapp.Sent(wamid='wamid.%d' % len(self.sent), category='utility')

    def t(self):
        return tenant_context(self.college_a)

    def start(self, now=NOW, **kwargs):
        from messaging import services
        with self.t():
            return services.start_run(now, **kwargs)

    def send(self, message_id):
        from messaging import services
        with self.t():
            return services.send_message(message_id)

    def message_for(self, guardian):
        from messaging.models import DigestMessage
        with self.t():
            return DigestMessage.objects.get(guardian=guardian)

    def post_webhook(self, payload, signature=True, host='a'):
        from django.test import Client

        from colleges.tests.base import HOST_A, HOST_B
        body, sig = signed(payload)
        extra = {'HTTP_X_HUB_SIGNATURE_256': sig} if signature else {}
        return Client(HTTP_HOST=HOST_A if host == 'a' else HOST_B).post('/webhooks/whatsapp/', data=body, content_type='application/json', **extra)

    @staticmethod
    def status_payload(wamid, status, timestamp='1', **extra):
        return {'entry': [{'changes': [{'value': {'statuses': [dict({'id': wamid, 'status': status, 'timestamp': timestamp}, **extra)]}}]}]}
