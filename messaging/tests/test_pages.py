"""Acceptance checks 1, 2, 6, 20, 22, 23 (+ the consent screen, Q6): admin pages, isolation, secrets, reports."""
import csv
import io

from django.test import Client

from colleges.tests.base import HOST_A, HOST_B
from main_app.tests.world import WorldTestCase  # noqa: F401  (documents the fixture family)

from .base import DAY, KEY, NOW, DigestTestCase

PAGES = ['/messaging/', '/messaging/report/', '/messaging/report.csv', '/messaging/usage/', '/messaging/usage.csv', '/messaging/consent/']
TOKEN = 'EAAG-super-secret-token-9876'  # pragma: allowlist secret


class AccessTests(DigestTestCase):
    def test_02_only_the_college_admin_may_open_any_page(self):
        hod = self.make_user(self.college_a, 'hod@a.in', '4', 'Hod', 'One')
        for page in PAGES:
            self.assertEqual(self.as_user(self.admin).get(page).status_code, 200, page)
            for user in (self.staff1, self.stud1, hod):
                self.assertEqual(self.as_user(user).get(page).status_code, 403, (page, user.email))
            anon = Client(HTTP_HOST=HOST_A).get(page)
            self.assertEqual(anon.status_code, 302, page)
            self.assertIn('/', anon['Location'])

    def test_02_the_consent_form_and_settings_form_refuse_other_roles(self):
        before = self.message_state()
        post = self.as_user(self.staff1).post('/messaging/consent/', {'guardian': self.g_sib.pk, 'value': 'no'})
        self.assertEqual(post.status_code, 403)
        self.assertEqual(self.as_user(self.stud1).post('/messaging/', {'enabled': 'on'}).status_code, 403)
        self.assertEqual(self.message_state(), before)

    def message_state(self):
        from main_app.models import Guardian
        from messaging.models import DigestSettings
        with self.t():
            return list(Guardian.objects.values_list('pk', 'whatsapp_consent')), DigestSettings.current().enabled

    def test_01_college_b_never_sees_college_a_digest_data(self):
        _, ids = self.start()
        self.send(ids[0])
        from django_tenants.utils import tenant_context

        from messaging.models import DigestMessage, DigestRun, DigestSettings
        with tenant_context(self.college_b):
            self.assertEqual(DigestMessage.objects.count(), 0)
            self.assertEqual(DigestRun.objects.count(), 0)
            self.assertFalse(DigestSettings.current().enabled)  # B has its own settings row, untouched by A
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        client = self.client_for(self.college_b, admin_b)
        for url in ('/messaging/report/?date=2026-01-05', '/messaging/consent/', '/messaging/usage/?month=2026-01'):
            body = client.get(url).content.decode()
            self.assertNotIn('Mr Sib', body)
            self.assertNotIn('Stu Two', body)
        self.assertNotIn('Mr Sib', client.get('/messaging/report.csv?date=2026-01-05').content.decode())


class SettingsAndSecretTests(DigestTestCase):
    def test_20_changing_a_setting_is_audited_with_who(self):
        from audit_trail.models import AuditLog
        client = self.as_user(self.admin)
        response = client.post('/messaging/', {'dry_run': 'on', 'enabled': 'on', 'send_time': '16:30', 'default_language': 'ta',
                                               'primary_guardian_only': 'on', 'phone_number_id': ''})
        self.assertEqual(response.status_code, 302)
        with self.t():
            entry = AuditLog.objects.filter(model_label='messaging.DigestSettings', action='update').latest('id')
        self.assertEqual(entry.actor_email, 'admin@a.in')
        self.assertEqual(set(entry.changes), {'dry_run', 'send_time', 'default_language'})
        self.assertEqual(entry.changes['dry_run'], ['False', 'True'])

    def test_06_the_token_is_stored_encrypted_shown_as_last_four_and_kept_out_of_the_audit_log(self):
        from audit_trail.models import AuditLog
        from messaging.models import CollegeSecret
        client = self.as_user(self.admin)
        form = {'enabled': 'on', 'send_time': '17:00', 'default_language': 'en', 'primary_guardian_only': 'on',
                'phone_number_id': '555000111', 'access_token': TOKEN}
        self.assertEqual(client.post('/messaging/', form).status_code, 302)
        with self.t():
            secret = CollegeSecret.objects.get(name='whatsapp_access_token')
            self.assertNotIn(TOKEN, secret.ciphertext)
            self.assertEqual(secret.last4, '9876')
            self.assertEqual(CollegeSecret.get('whatsapp_access_token'), TOKEN)
            audit = ' '.join(str(e.changes) + e.object_repr for e in AuditLog.objects.filter(model_label='messaging.CollegeSecret'))
        self.assertTrue(audit)  # the change IS recorded ...
        self.assertNotIn(TOKEN, audit)  # ... without its value or its ciphertext
        self.assertNotIn(secret.ciphertext, audit)
        page = client.get('/messaging/').content.decode()
        self.assertNotIn(TOKEN, page)
        self.assertIn('****9876', page)
        self.assertNotIn('value="%s' % TOKEN[:6], page)

    def test_06_the_college_token_is_used_only_with_its_own_number(self):
        from messaging import services
        from messaging.models import CollegeSecret, DigestSettings
        with self.t():
            CollegeSecret.put('whatsapp_access_token', TOKEN)
            settings = DigestSettings.current()
            self.assertIsNone(services.credentials_for(settings))  # no own number yet: platform credentials
            DigestSettings.objects.filter(pk=1).update(phone_number_id='555')
            creds = services.credentials_for(DigestSettings.current())
        self.assertEqual((creds.phone_number_id, creds.access_token), ('555', TOKEN))
        self.assertNotIn(TOKEN, repr(creds))

    def test_06_a_phone_number_without_a_token_is_rejected(self):
        form = {'enabled': 'on', 'send_time': '17:00', 'default_language': 'en', 'phone_number_id': '555'}
        response = self.as_user(self.admin).post('/messaging/', form)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'needs its access token')

    def test_06_secrets_cannot_be_read_without_the_key(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.test import override_settings

        from messaging import crypto
        with override_settings(FIELD_ENCRYPTION_KEY=''), self.assertRaises(ImproperlyConfigured):
            crypto.encrypt('x')
        text = crypto.encrypt('hello')
        with override_settings(FIELD_ENCRYPTION_KEY=__import__('cryptography.fernet', fromlist=['Fernet']).Fernet.generate_key().decode()):
            with self.assertRaises(ImproperlyConfigured):
                crypto.decrypt(text)
        self.assertEqual(crypto.decrypt(text), 'hello')
        self.assertTrue(KEY)

    def test_needs_attention_is_shown_and_can_be_cleared_by_the_admin(self):
        from messaging.models import DigestSettings
        with self.t():
            DigestSettings.objects.filter(pk=1).update(state='needs_attention', state_reason='WhatsApp rejected the access token.')
        client = self.as_user(self.admin)
        self.assertContains(client.get('/messaging/'), 'Needs attention')
        self.assertContains(client.get('/messaging/report/'), 'WhatsApp rejected the access token.')
        client.post('/messaging/', {'enabled': 'on', 'send_time': '17:00', 'default_language': 'en', 'clear_attention': 'on'})
        with self.t():
            self.assertEqual(DigestSettings.current().state, 'ok')


class ConsentScreenTests(DigestTestCase):
    def test_q6_yes_needs_a_source_and_is_audited_then_a_digest_goes_out(self):
        from audit_trail.models import AuditLog
        from main_app.models import Guardian
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(whatsapp_consent='unknown')
        client = self.as_user(self.admin)
        client.post('/messaging/consent/', {'guardian': self.g_sib.pk, 'value': 'yes', 'source': ''})
        with self.t():
            self.assertEqual(Guardian.objects.get(pk=self.g_sib.pk).whatsapp_consent, 'unknown')  # refused: no source
        client.post('/messaging/consent/', {'guardian': self.g_sib.pk, 'value': 'yes', 'source': 'Signed admission form 2026'})
        with self.t():
            guardian = Guardian.objects.get(pk=self.g_sib.pk)
            self.assertEqual((guardian.whatsapp_consent, guardian.consent_source), ('yes', 'Signed admission form 2026'))
            self.assertTrue(AuditLog.objects.filter(model_label='main_app.Guardian', actor_email='admin@a.in').exists())
        _, ids = self.start()
        self.assertEqual(len(ids), 1)

    def test_q6_the_list_searches_and_rejects_nonsense(self):
        client = self.as_user(self.admin)
        self.assertContains(client.get('/messaging/consent/?q=Sib'), 'Mr Sib')
        self.assertNotContains(client.get('/messaging/consent/?q=Sib'), 'Mrs Other')
        before = client.post('/messaging/consent/', {'guardian': self.g_sib.pk, 'value': 'maybe'})
        self.assertEqual(before.status_code, 302)
        self.assertEqual(client.post('/messaging/consent/', {'guardian': 99999, 'value': 'no'}).status_code, 404)


class ReportTests(DigestTestCase):
    def test_22_the_daily_report_equals_the_database(self):
        from main_app.models import Guardian, StudentGuardian
        from messaging.models import DigestMessage
        with self.t():  # a variety of outcomes
            ok = Guardian.objects.create(full_name='Mr Three', phone='+919000000003', whatsapp_consent='yes')
            no = Guardian.objects.create(full_name='Mr Four', phone='+919000000004', whatsapp_consent='no')
            StudentGuardian.objects.filter(student=self.stud2.student).update(guardian=ok)
            from main_app.models import AttendanceReport
            AttendanceReport.objects.filter(student=self.stud3.student).update(status='absent')
            from main_app.models import Attendance
            Attendance.objects.filter(pk=self.att2).update(date=DAY)
            StudentGuardian.objects.filter(student=self.stud3.student).update(guardian=no)
        _, ids = self.start()
        for message_id in ids:
            self.send(message_id)
        from messaging import reports
        with self.t():
            counts = reports.daily_counts(DAY, 'live')
            rows = list(DigestMessage.objects.filter(mode='live').values_list('status', flat=True))
        self.assertEqual(counts['eligible'], len(rows))
        self.assertEqual(counts['sent'], sum(s in ('sent', 'delivered', 'read') for s in rows))
        self.assertEqual(counts['skipped_no_consent'], rows.count('skipped_no_consent'))
        self.assertEqual(counts['skipped_no_consent'], 1)
        response = self.as_user(self.admin).get('/messaging/report/?date=2026-01-05')
        self.assertEqual(response.context['report']['counts'], counts)
        body = response.content.decode()
        self.assertIn('data-col="eligible">%d<' % counts['eligible'], body)

    def test_22_the_csv_has_one_row_per_message_and_neutralises_formulas(self):
        from messaging.models import DigestMessage
        _, ids = self.start()
        with self.t():
            DigestMessage.objects.filter(pk=ids[0]).update(status='failed', last_error='=HYPERLINK("http://evil")')
        rows = list(csv.reader(io.StringIO(self.as_user(self.admin).get('/messaging/report.csv?date=2026-01-05').content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], 'failed')
        self.assertTrue(rows[1][6].startswith("'="))

    def test_22_late_marked_classes_are_counted_not_sent(self):
        from main_app.models import Attendance
        from messaging import reports
        with self.t():
            Attendance.objects.filter(date=DAY).update(created_at=NOW.replace(hour=15))
            Attendance.objects.filter(pk=self.att1).update(created_at=NOW.replace(hour=18))  # marked after 17:00
            self.assertEqual(reports.late_marked(DAY), 1)

    def test_23_usage_counts_each_live_message_once_and_failed_or_dry_cost_nothing(self):
        from messaging.models import DigestMessage, UsageRate
        with self.t():
            UsageRate.objects.create(category='utility', effective_from=DAY.replace(month=1, day=1), rate='0.1150')
            UsageRate.objects.create(category='utility', effective_from=DAY.replace(month=1, day=10), rate='0.2000')  # later: not applied to the 5th
        _, ids = self.start()
        self.send(ids[0])
        from messaging import reports
        with self.t():
            usage = reports.month_usage(2026, 1)
            message = DigestMessage.objects.get()
        self.assertEqual(str(message.billed_rate), '0.1150')
        self.assertEqual((usage['total_count'], str(usage['total_cost'])), (1, '0.1150'))
        with self.t():
            DigestMessage.objects.filter(pk=message.pk).update(status='failed')
            self.assertEqual(reports.month_usage(2026, 1)['total_count'], 0)
            self.assertEqual(reports.month_usage(2026, 2)['total_count'], 0)

    def test_23_the_monthly_usage_page_and_csv(self):
        _, ids = self.start()
        self.send(ids[0])
        client = self.as_user(self.admin)
        page = client.get('/messaging/usage/?month=2026-01')
        self.assertContains(page, 'estimate until it is reconciled')
        rows = list(csv.reader(io.StringIO(client.get('/messaging/usage.csv?month=2026-01').content.decode())))
        self.assertEqual(rows[0], ['month', 'category', 'messages', 'estimated_cost_inr'])
        self.assertEqual(rows[-1][:3], ['2026-01', 'TOTAL', '1'])
        self.assertEqual(client.get('/messaging/usage/?month=garbage').status_code, 200)

    def test_25_no_test_touches_the_network(self):
        import requests
        with self.assertRaises(AssertionError):
            requests.post('https://graph.facebook.com/', timeout=1)
        self.assertEqual(HOST_B, 'b.testserver')
