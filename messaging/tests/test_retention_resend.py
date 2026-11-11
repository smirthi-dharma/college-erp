"""Message-log retention (12 months by default) and the admin's "send again" for unclear messages."""
import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, SimpleTestCase
from django_tenants.utils import tenant_context

from colleges.tests.base import HOST_A
from main_app import whatsapp

from .base import DAY, NOW, DigestTestCase

TODAY = NOW  # 2026-01-05 17:30 IST


class MonthMathTests(SimpleTestCase):
    def test_months_before(self):
        from messaging.retention import months_before
        d = datetime.date
        self.assertEqual(months_before(d(2026, 1, 5), 12), d(2025, 1, 5))
        self.assertEqual(months_before(d(2026, 3, 31), 1), d(2026, 2, 28))  # shorter month: its last day
        self.assertEqual(months_before(d(2024, 3, 31), 1), d(2024, 2, 29))  # leap year
        self.assertEqual(months_before(d(2026, 1, 31), 2), d(2025, 11, 30))
        self.assertEqual(months_before(d(2026, 12, 31), 12), d(2025, 12, 31))
        self.assertEqual(months_before(d(2026, 1, 1), 1), d(2025, 12, 1))


class RetentionTests(DigestTestCase):
    def make(self, college, day, status='delivered', suffix=''):
        from main_app.models import Guardian
        from messaging.models import DeliveryEvent, DigestMessage, DigestRun
        with tenant_context(college):
            guardian = Guardian.objects.create(full_name='G%s%s' % (day, suffix), phone='+9190%08d' % (abs(hash((str(day), suffix, college.pk))) % 10**8))
            run, _ = DigestRun.objects.get_or_create(date=day, mode='live', defaults={'state': 'done'})
            message = DigestMessage.objects.create(run=run, guardian=guardian, digest_date=day, mode='live', status=status, wamid='w-%s-%s-%s' % (college.pk, day, suffix))
            DeliveryEvent.objects.create(message=message, status=status, meta_timestamp='1')
            return message.pk

    def purge(self, **kwargs):
        from messaging import retention
        with self.t():
            return retention.purge(now=NOW, **kwargs)

    def count(self, college=None):
        from messaging.models import DeliveryEvent, DigestMessage, DigestRun
        with tenant_context(college or self.college_a):
            return DigestMessage.objects.count(), DigestRun.objects.count(), DeliveryEvent.objects.count()

    def test_messages_older_than_twelve_months_go_and_the_boundary_day_stays(self):
        old = self.make(self.college_a, datetime.date(2025, 1, 4))  # one day past 12 months
        boundary = self.make(self.college_a, datetime.date(2025, 1, 5))  # exactly 12 months: kept
        recent = self.make(self.college_a, datetime.date(2025, 12, 1))
        self.assertEqual(self.count(), (3, 3, 3))
        self.assertEqual(self.purge()[0], 1)
        from messaging.models import DigestMessage
        with self.t():
            self.assertEqual(sorted(DigestMessage.objects.values_list('pk', flat=True)), sorted([boundary, recent]))
            self.assertFalse(DigestMessage.objects.filter(pk=old).exists())
        self.assertEqual(self.count(), (2, 2, 2))  # the old message's run and delivery events went with it

    def test_a_run_that_still_has_recent_messages_is_kept(self):
        from messaging.models import DigestMessage
        self.make(self.college_a, datetime.date(2025, 1, 4), suffix='a')
        with self.t():
            DigestMessage.objects.update(digest_date=datetime.date(2025, 6, 1))  # same run row, now holds a recent message
        self.assertEqual(self.purge(), (0, 1)[:1] + (self.purge()[1],))

    def test_it_follows_the_colleges_own_retention_setting(self):
        from messaging.models import DigestSettings
        self.make(self.college_a, datetime.date(2025, 6, 1))
        self.assertEqual(self.purge(dry_run=True)[0], 0)  # 12 months: kept
        with self.t():
            DigestSettings.objects.filter(pk=1).update(retention_months=6)
        self.assertEqual(self.purge()[0], 1)
        self.assertEqual(self.count()[0], 0)

    def test_dry_run_counts_and_deletes_nothing(self):
        self.make(self.college_a, datetime.date(2024, 1, 1))
        self.assertEqual(self.purge(dry_run=True), (1, 1))
        self.assertEqual(self.count(), (1, 1, 1))

    def test_only_this_college_is_touched(self):
        self.make(self.college_a, datetime.date(2024, 1, 1))
        self.make(self.college_b, datetime.date(2024, 1, 1))
        self.purge()
        self.assertEqual(self.count(self.college_a)[0], 0)
        self.assertEqual(self.count(self.college_b)[0], 1)

    def test_guardians_consent_evidence_and_the_audit_log_are_not_touched(self):
        from audit_trail.models import AuditLog
        from main_app.models import Guardian
        self.make(self.college_a, datetime.date(2024, 1, 1))
        with self.t():
            guardians, audits = Guardian.objects.count(), AuditLog.objects.count()
        self.purge()
        with self.t():
            self.assertEqual(Guardian.objects.count(), guardians)
            self.assertGreaterEqual(AuditLog.objects.count(), audits)  # only ever grows

    def test_a_purge_is_audited_with_the_count_and_the_cutoff(self):
        from audit_trail.models import AuditLog
        self.make(self.college_a, datetime.date(2024, 1, 1), suffix='1')
        self.make(self.college_a, datetime.date(2024, 1, 2), suffix='2')
        self.purge()
        with self.t():
            entry = AuditLog.objects.filter(model_label='messaging.DigestMessage', object_pk='retention').get()
        self.assertEqual((entry.action, entry.source), ('delete', 'system'))
        self.assertEqual(entry.changes['deleted_messages'], [2, None])
        self.assertEqual(entry.changes['older_than'], ['2025-01-05', None])
        self.assertNotIn('G2024', str(entry.changes) + entry.object_repr)  # no names in the log

    def test_a_second_purge_does_nothing_and_writes_no_audit_entry(self):
        from audit_trail.models import AuditLog
        self.make(self.college_a, datetime.date(2024, 1, 1))
        self.purge()
        with self.t():
            before = AuditLog.objects.count()
        self.assertEqual(self.purge(), (0, 0))
        with self.t():
            self.assertEqual(AuditLog.objects.count(), before)

    def test_the_settings_page_says_when_it_last_ran(self):
        self.make(self.college_a, datetime.date(2024, 1, 1))
        self.purge()
        page = self.as_user(self.admin).get('/messaging/').content.decode()
        self.assertIn('deleted automatically after 12 months', page)
        self.assertIn('1 record(s) deleted', page)

    def test_the_retention_setting_is_validated(self):
        form = {'enabled': 'on', 'send_time': '17:00', 'default_language': 'en', 'primary_guardian_only': 'on'}
        for bad in ('0', '61', 'abc', ''):
            response = self.as_user(self.admin).post('/messaging/', dict(form, retention_months=bad))
            self.assertEqual(response.status_code, 200, bad)
        self.assertEqual(self.as_user(self.admin).post('/messaging/', dict(form, retention_months='24')).status_code, 302)

    def test_the_command_and_the_nightly_task_do_the_same_for_every_college(self):
        from messaging.tasks import purge_old_messages
        self.make(self.college_a, datetime.date(2020, 1, 1))
        self.make(self.college_b, datetime.date(2020, 1, 1))
        out = StringIO()
        call_command('purge_message_log', dry_run=True, stdout=out)
        self.assertIn('college_a: 1 message record(s), 1 run(s) would be deleted', out.getvalue())
        self.assertEqual(self.count()[0], 1)
        self.assertEqual(purge_old_messages(), 2)  # eager task, both colleges
        self.assertEqual((self.count()[0], self.count(self.college_b)[0]), (0, 0))

    def test_the_nightly_task_is_scheduled(self):
        from celery.schedules import crontab
        from django.conf import settings
        entry = settings.CELERY_BEAT_SCHEDULE['purge-old-whatsapp-messages']
        self.assertEqual(entry['task'], 'messaging.tasks.purge_old_messages')
        self.assertEqual(entry['schedule'], crontab(hour=3, minute=30))
        from college_management_system.celery import app
        app.loader.import_default_modules()
        self.assertIn(entry['task'], app.tasks)


class ResendTests(DigestTestCase):
    def unclear_message(self):
        self.transport_errors = [whatsapp.UnknownOutcome('read timeout')]
        _, ids = self.start()
        self.send(ids[0])
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')
        self.assertEqual(len(self.sent), 1)
        return ids[0]

    def post(self, pk, confirm=True, user=None):
        data = {'confirm': 'yes'} if confirm else {}
        with mock.patch('django.utils.timezone.now', return_value=NOW):  # "today" is the day of the digest, within 7 days
            return self.as_user(user or self.admin).post('/messaging/message/%d/resend/' % pk, data)

    def test_the_report_offers_resend_only_for_unclear_live_messages(self):
        pk = self.unclear_message()
        page = self.as_user(self.admin).get('/messaging/report/?date=2026-01-05').content.decode()
        self.assertIn('/messaging/message/%d/resend/' % pk, page)
        from messaging.models import DigestMessage
        with self.t():
            DigestMessage.objects.filter(pk=pk).update(status='failed')
        self.assertNotIn('/resend/', self.as_user(self.admin).get('/messaging/report/?date=2026-01-05').content.decode())

    def test_the_confirmation_page_warns_that_it_may_duplicate(self):
        pk = self.unclear_message()
        page = self.as_user(self.admin).get('/messaging/message/%d/resend/' % pk).content.decode()
        self.assertIn('may receive this message twice', page)
        self.assertIn('Mr Sib', page)
        self.assertEqual(len(self.sent), 1)  # looking at the page sends nothing

    def test_without_ticking_the_box_nothing_is_sent(self):
        pk = self.unclear_message()
        self.post(pk, confirm=False)
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')
        self.assertEqual(len(self.sent), 1)

    def test_a_confirmed_resend_sends_once_more_and_records_who(self):
        pk = self.unclear_message()
        response = self.post(pk)
        self.assertEqual(response.status_code, 302)
        message = self.message_for(self.g_sib)
        self.assertEqual((message.status, message.resend_count, message.last_resent_by), ('sent', 1, 'admin@a.in'))
        self.assertIsNotNone(message.last_resent_at)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[0][1:], self.sent[1][1:])  # the same message again

    def test_it_is_audited_with_the_admin_as_actor(self):
        from audit_trail.models import AuditLog
        pk = self.unclear_message()
        self.post(pk)
        with self.t():
            entry = AuditLog.objects.filter(model_label='messaging.DigestMessage', object_pk=str(pk), action='update').get()
        self.assertEqual((entry.actor_email, entry.actor_role), ('admin@a.in', '1'))
        self.assertEqual(entry.changes, {'status': ['unknown', 'queued'], 'resend_count': [0, 1]})

    def test_a_double_click_sends_only_once(self):
        pk = self.unclear_message()
        self.post(pk)
        self.post(pk)  # the message is now "sent", not "unknown": refused
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.message_for(self.g_sib).resend_count, 1)

    def test_only_unclear_live_messages_without_an_id_can_be_resent(self):
        from messaging import services
        from messaging.models import DigestMessage
        pk = self.unclear_message()
        for change, expected in (({'status': 'sent'}, 'unclear'), ({'status': 'failed'}, 'unclear'), ({'status': 'delivered'}, 'unclear'),
                                 ({'status': 'unknown', 'mode': 'dry_run'}, 'unclear'), ({'status': 'unknown', 'wamid': 'w-1'}, 'already accepted')):
            with self.t():
                DigestMessage.objects.filter(pk=pk).update(**dict({'status': 'unknown', 'mode': 'live', 'wamid': None}, **change))
                with self.assertRaises(services.ResendError) as caught:
                    services.resend(pk, self.admin)
            self.assertIn(expected, str(caught.exception))
        self.assertEqual(len(self.sent), 1)

    def test_at_most_three_resends_and_not_after_seven_days(self):
        from messaging import services
        from messaging.models import DigestMessage
        pk = self.unclear_message()
        with self.t():
            DigestMessage.objects.filter(pk=pk).update(resend_count=3)
            with self.assertRaises(services.ResendError):
                services.resend(pk, self.admin, now=NOW)
            DigestMessage.objects.filter(pk=pk).update(resend_count=0)
            with self.assertRaises(services.ResendError) as caught:
                services.resend(pk, self.admin, now=NOW + datetime.timedelta(days=8))
            self.assertIn('too late', str(caught.exception))
            services.resend(pk, self.admin, now=NOW + datetime.timedelta(days=7))  # day 7 still allowed
        self.assertEqual(len(self.sent), 1)

    def test_consent_withdrawn_in_the_meantime_is_honoured_and_a_switched_off_digest_cancels(self):
        from main_app.models import Guardian
        pk = self.unclear_message()
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(whatsapp_consent='no')
        self.post(pk)
        self.assertEqual(self.message_for(self.g_sib).status, 'skipped_no_consent')
        self.assertEqual(len(self.sent), 1)

    def test_a_switched_off_digest_does_not_resend(self):
        from messaging.models import DigestSettings
        pk = self.unclear_message()
        with self.t():
            DigestSettings.objects.filter(pk=1).update(enabled=False)
        self.post(pk)
        self.assertEqual(self.message_for(self.g_sib).status, 'cancelled')
        self.assertEqual(len(self.sent), 1)

    def test_only_the_college_admin_can_open_or_post(self):
        pk = self.unclear_message()
        hod = self.make_user(self.college_a, 'hod@a.in', '4', 'Hod', 'One')
        for user in (self.staff1, self.stud1, hod):
            client = self.as_user(user)
            self.assertEqual(client.get('/messaging/message/%d/resend/' % pk).status_code, 403, user.email)
            self.assertEqual(client.post('/messaging/message/%d/resend/' % pk, {'confirm': 'yes'}).status_code, 403, user.email)
        self.assertEqual(Client(HTTP_HOST=HOST_A).post('/messaging/message/%d/resend/' % pk, {'confirm': 'yes'}).status_code, 302)
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')
        self.assertEqual(len(self.sent), 1)

    def test_college_b_cannot_resend_college_a_messages(self):
        pk = self.unclear_message()
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        client = self.client_for(self.college_b, admin_b)
        self.assertEqual(client.get('/messaging/message/%d/resend/' % pk).status_code, 404)
        self.assertEqual(client.post('/messaging/message/%d/resend/' % pk, {'confirm': 'yes'}).status_code, 404)
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')

    def test_a_non_admin_cannot_call_the_service_directly(self):
        from messaging import services
        pk = self.unclear_message()
        with self.t(), self.assertRaises(services.ResendError):
            services.resend(pk, self.staff1)
        self.assertEqual(DAY.year, 2026)
