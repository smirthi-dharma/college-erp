"""Acceptance checks 3, 4, 8-20 of docs/WHATSAPP_DIGEST_ACCEPTANCE.md (the number is in each test name)."""
import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from main_app import whatsapp

from .base import DAY, NOW, DigestTestCase


class ConsentAndTemplateTests(DigestTestCase):
    def test_03_unknown_and_no_consent_are_never_sent_to(self):
        from main_app.models import Guardian
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(whatsapp_consent='unknown')
        run, ids = self.start()
        self.assertEqual(ids, [])
        self.assertEqual(self.message_for(self.g_sib).status, 'skipped_no_consent')
        self.assertEqual(self.sent, [])

    def test_03_consent_withdrawn_between_build_and_send_is_honoured(self):
        from main_app.models import Guardian
        _, ids = self.start()
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(whatsapp_consent='no')
        self.assertEqual(self.send(ids[0])[0], 'done')
        self.assertEqual(self.message_for(self.g_sib).status, 'skipped_no_consent')
        self.assertEqual(self.sent, [])

    def test_04_there_is_no_free_text_path_and_only_approved_templates_send(self):
        self.assertFalse(hasattr(whatsapp, 'send_whatsapp'))
        with self.t():
            with self.assertRaises(whatsapp.TemplateNotApproved):
                whatsapp.send_template(self.g_sib, 'made_up_template', 'en', ['x'])
            with self.assertRaises(whatsapp.TemplateNotApproved):
                whatsapp.send_template(self.g_sib, 'absence_digest', 'fr', ['x'])
        self.assertEqual(self.sent, [])


class ContentTests(DigestTestCase):
    def test_08_siblings_get_one_message_listing_every_period(self):
        _, ids = self.start()
        self.assertEqual(len(ids), 1)  # g_other has nothing to be told
        self.send(ids[0])
        self.assertEqual(len(self.sent), 1)
        phone, name, language, params = self.sent[0]
        self.assertEqual((phone, name, language), ('+919000000001', 'absence_digest_en', 'en'))
        self.assertEqual(params[0], 'Mr Sib')
        self.assertEqual(params[1], '5 Jan 2026')
        self.assertIn('Stu One: P2 Maths', params[2])
        self.assertIn('Stu Two: P1 Maths, P2 Maths', params[2])
        self.assertNotIn('\n', params[2])
        self.assertEqual(params[3], self.college_a.name)

    def test_10_all_present_and_no_classes_send_nothing(self):
        from messaging.models import DigestMessage
        run, ids = self.start(now=NOW + datetime.timedelta(days=2))  # 7 Jan: no class was marked
        self.assertEqual(ids, [])
        with self.t():
            self.assertEqual(DigestMessage.objects.count(), 0)
        self.assertEqual(run.state, 'done')

    def test_11_only_absent_counts_not_on_duty_and_not_withdrawn(self):
        from academics.models import Enrolment
        from main_app.models import AttendanceReport
        with self.t():
            AttendanceReport.objects.filter(student=self.stud1.student).update(status='on_duty')  # stud1 no longer absent
            Enrolment.objects.filter(student=self.stud2.student, offering_id=self.sub1).update(withdrawn_on=DAY)  # off the list that day
        _, ids = self.start()
        self.assertEqual(ids, [])  # nothing left to report
        from messaging.models import DigestMessage
        with self.t():
            self.assertEqual(DigestMessage.objects.count(), 0)

    def test_12_language_follows_the_guardian_then_the_college_default(self):
        from main_app.models import Guardian
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(preferred_language='ta')
        _, ids = self.start()
        self.send(ids[0])
        self.assertEqual(self.sent[0][1:3], ('absence_digest_ta', 'ta'))
        self.assertIn('அன்புள்ள', self.message_for(self.g_sib).rendered_text)

    def test_12_college_default_language_applies_when_the_guardian_has_none(self):
        from messaging.models import DigestSettings
        with self.t():
            DigestSettings.objects.filter(pk=1).update(default_language='ta')
        _, ids = self.start()
        self.send(ids[0])
        self.assertEqual(self.sent[0][2], 'ta')

    def test_12_a_long_list_is_cut_to_fit_with_and_n_more(self):
        from messaging import digest
        class Name:
            def __init__(self, first, last):
                self.first_name, self.last_name = first, last

        class Student:
            def __init__(self, i):
                self.pk, self.admin = i, Name('Student%02d' % i, 'Longsurname')

        def student(i):
            return Student(i)
        students = [student(i) for i in range(60)]
        absent = {s: [(1, 'Programming in Python'), (2, 'Engineering Mathematics')] for s in students}
        for language in ('en', 'ta'):
            text = digest.summary(students, absent, language)
            self.assertLessEqual(len(text), digest.MAX_SUMMARY)
            self.assertRegex(text, r'\d+')
            self.assertTrue(text.startswith('Student00 Longsurname'))
        self.assertIn('more, please contact the college', digest.summary(students, absent, 'en'))


class TimingAndSwitchTests(DigestTestCase):
    def test_13_nothing_before_the_send_time_and_only_when_switched_on(self):
        from messaging.models import DigestSettings
        early = timezone.make_aware(datetime.datetime(2026, 1, 5, 16, 59))
        self.assertEqual(self.start(now=early), (None, []))
        self.assertIsNotNone(self.start(now=timezone.make_aware(datetime.datetime(2026, 1, 5, 17, 0)))[0])
        with self.t():
            DigestSettings.objects.filter(pk=1).update(enabled=False)
        self.assertEqual(self.start(now=NOW + datetime.timedelta(days=1)), (None, []))

    def test_13_switching_off_stops_a_run_in_progress(self):
        from messaging.models import DigestSettings
        _, ids = self.start()
        with self.t():
            DigestSettings.objects.filter(pk=1).update(enabled=False)
        self.send(ids[0])
        self.assertEqual(self.message_for(self.g_sib).status, 'cancelled')
        self.assertEqual(self.sent, [])

    def test_18_new_colleges_are_off_and_in_dry_run(self):
        from messaging.models import DigestSettings
        with self.t():
            DigestSettings.objects.all().delete()
            fresh = DigestSettings.current()
        self.assertFalse(fresh.enabled)
        self.assertTrue(fresh.dry_run)
        self.assertEqual(self.start(), (None, []))
        self.assertEqual(self.sent, [])


class NeverTwiceTests(DigestTestCase):
    def test_09_a_second_run_and_a_second_send_do_nothing(self):
        _, ids = self.start()
        self.assertEqual(self.start(), (None, []))  # the day's run exists
        self.assertEqual(self.send(ids[0])[0], 'done')
        self.assertEqual(self.send(ids[0])[0], 'skip')  # already handled
        self.assertEqual(len(self.sent), 1)

    def test_09_the_database_refuses_a_duplicate_message(self):
        from messaging.models import DigestMessage
        self.start()
        message = self.message_for(self.g_sib)
        with self.t(), self.assertRaises(IntegrityError), transaction.atomic():
            DigestMessage.objects.create(run=message.run, guardian=self.g_sib, digest_date=DAY, mode='live')

    def test_09_a_run_resumed_after_a_crash_adds_no_duplicates(self):
        from messaging.models import DigestMessage, DigestRun
        run, _ = self.start()
        with self.t():
            DigestRun.objects.filter(pk=run.pk).update(state='building', started_at=NOW - datetime.timedelta(hours=1))
        _, ids = self.start(now=NOW + datetime.timedelta(minutes=30))  # resumed: rows already exist
        self.assertEqual(ids, [])
        with self.t():
            self.assertEqual(DigestMessage.objects.count(), 1)

    def test_09_the_command_run_twice_sends_once(self):
        with mock.patch('django.utils.timezone.now', return_value=NOW):
            for _ in range(2):
                call_command('run_digest', schema=self.college_a.schema_name, ignore_time=True, stdout=StringIO())
        self.assertEqual(len(self.sent), 1)

    def test_09_a_worker_killed_mid_send_leaves_unknown_not_a_second_send(self):
        from messaging import services
        from messaging.models import DigestMessage
        _, ids = self.start()
        with self.t():
            DigestMessage.objects.filter(pk=ids[0]).update(status='sending')
            DigestMessage.objects.filter(pk=ids[0]).update(updated_at=NOW - datetime.timedelta(hours=1))
            services.sweep(now=NOW)
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')
        self.assertEqual(self.send(ids[0])[0], 'skip')
        self.assertEqual(self.sent, [])


class RetryTests(DigestTestCase):
    def test_14_transient_failures_are_retried_with_growing_delays_then_succeed(self):
        self.transport_errors = [whatsapp.TransientError('Meta 503'), whatsapp.TransientError('rate limit'), None]
        _, ids = self.start()
        self.assertEqual(self.send(ids[0]), ('retry', 60))
        self.assertEqual(self.message_for(self.g_sib).last_error, 'Meta 503')
        self.assertEqual(self.send(ids[0]), ('retry', 300))
        self.assertEqual(self.send(ids[0])[0], 'done')
        message = self.message_for(self.g_sib)
        self.assertEqual((message.status, message.attempts, message.wamid), ('sent', 3, 'wamid.3'))

    def test_14_after_three_attempts_it_gives_up_and_says_why(self):
        self.transport_errors = [whatsapp.TransientError('down')] * 3
        _, ids = self.start()
        self.send(ids[0]), self.send(ids[0])
        self.assertEqual(self.send(ids[0])[0], 'done')
        message = self.message_for(self.g_sib)
        self.assertEqual(message.status, 'failed')
        self.assertIn('Gave up after 3 attempts', message.last_error)

    def test_14_the_background_task_retries_until_sent(self):
        from messaging.tasks import send_message_task
        self.transport_errors = [whatsapp.TransientError('x'), whatsapp.TransientError('y'), None]
        _, ids = self.start()
        send_message_task.delay(self.college_a.schema_name, ids[0])  # tests run tasks inline
        self.assertEqual(self.message_for(self.g_sib).status, 'sent')
        self.assertEqual(len(self.sent), 3)

    def test_15_a_permanent_failure_is_not_retried(self):
        self.transport_errors = [whatsapp.PermanentError('Not a WhatsApp number (code 131026)')]
        _, ids = self.start()
        self.send(ids[0])
        message = self.message_for(self.g_sib)
        self.assertEqual((message.status, message.attempts), ('failed', 1))
        self.assertIn('131026', message.last_error)
        self.assertEqual(self.send(ids[0])[0], 'skip')
        self.assertEqual(len(self.sent), 1)

    def test_15_a_bad_token_stops_the_whole_college_and_flags_it(self):
        from main_app.models import Guardian, StudentGuardian
        from messaging.models import DigestSettings
        with self.t():  # a second guardian with something to report
            other = Guardian.objects.create(full_name='Mr Two', phone='+919000000003', whatsapp_consent='yes')
            StudentGuardian.objects.filter(student=self.stud2.student).update(guardian=other)
        self.transport_errors = [whatsapp.AuthError('Invalid OAuth access token (code 190)')]
        _, ids = self.start()
        self.assertEqual(len(ids), 2)
        self.send(ids[0])
        self.send(ids[1])
        self.assertEqual(len(self.sent), 1)  # the second never called WhatsApp
        from messaging.models import DigestMessage
        with self.t():
            statuses = sorted(DigestMessage.objects.values_list('status', flat=True))
            settings = DigestSettings.current()
        self.assertEqual(statuses, ['cancelled', 'failed'])
        self.assertEqual(settings.state, 'needs_attention')
        self.assertIn('rejected the access token', settings.state_reason)
        self.assertEqual(self.start(now=NOW + datetime.timedelta(days=1)), (None, []))  # paused until an admin clears it

    def test_16_an_unclear_outcome_is_never_retried(self):
        self.transport_errors = [whatsapp.UnknownOutcome('read timeout')]
        _, ids = self.start()
        self.send(ids[0])
        self.assertEqual(self.message_for(self.g_sib).status, 'unknown')
        self.assertEqual(self.send(ids[0])[0], 'skip')
        self.assertEqual(len(self.sent), 1)

    def test_17_one_guardians_crash_does_not_stop_the_others(self):
        from main_app.models import Guardian, StudentGuardian
        from messaging.tasks import dispatch_due_digests
        with self.t():
            other = Guardian.objects.create(full_name='Mr Two', phone='+919000000003', whatsapp_consent='yes')
            StudentGuardian.objects.filter(student=self.stud2.student).update(guardian=other)
        self.transport_errors = [RuntimeError('boom')]
        with mock.patch('django.utils.timezone.now', return_value=NOW), self.assertLogs('messaging.services', level='ERROR'):
            dispatch_due_digests()
        from messaging.models import DigestMessage
        with self.t():
            statuses = sorted(DigestMessage.objects.values_list('status', flat=True))
        self.assertEqual(statuses, ['sent', 'unknown'])  # the crash is recorded as unclear, the other one went out


class DryRunTests(DigestTestCase):
    def test_19_dry_run_logs_the_exact_message_and_sends_nothing(self):
        from messaging.models import DigestSettings
        with self.t():
            DigestSettings.objects.filter(pk=1).update(dry_run=True)
        run, ids = self.start()
        self.assertEqual(ids, [])
        message = self.message_for(self.g_sib)
        self.assertEqual((message.status, message.mode), ('dry_run', 'dry_run'))
        self.assertIn('Stu Two: P1 Maths, P2 Maths', message.rendered_text)
        self.assertEqual(self.sent, [])
        self.assertIsNone(message.billed_rate)

    def test_19_a_dry_run_does_not_block_the_real_send_the_same_day(self):
        from messaging.models import DigestSettings
        with self.t():
            DigestSettings.objects.filter(pk=1).update(dry_run=True)
        self.start()
        with self.t():
            DigestSettings.objects.filter(pk=1).update(dry_run=False)
        _, ids = self.start(now=NOW + datetime.timedelta(minutes=5))
        self.assertEqual(len(ids), 1)
        self.send(ids[0])
        self.assertEqual(len(self.sent), 1)
