"""Acceptance checks 5, 7, 21 and 24: the webhook."""
from django.test import Client

from colleges.tests.base import HOST_A

from .base import VERIFY_TOKEN, DigestTestCase, signed


class SignatureTests(DigestTestCase):
    def sent_message(self):
        _, ids = self.start()
        self.send(ids[0])
        return self.message_for(self.g_sib)

    def test_05_a_missing_or_bad_signature_is_refused_and_changes_nothing(self):
        message = self.sent_message()
        payload = self.status_payload(message.wamid, 'delivered')
        self.assertEqual(self.post_webhook(payload, signature=False).status_code, 403)
        body, _ = signed(payload)
        bad = Client(HTTP_HOST=HOST_A).post('/webhooks/whatsapp/', data=body, content_type='application/json',
                                            HTTP_X_HUB_SIGNATURE_256='sha256=' + '0' * 64)
        self.assertEqual(bad.status_code, 403)
        wrong_secret_body, wrong_sig = signed(payload, secret='another-secret')  # pragma: allowlist secret
        wrong = Client(HTTP_HOST=HOST_A).post('/webhooks/whatsapp/', data=wrong_secret_body, content_type='application/json',
                                              HTTP_X_HUB_SIGNATURE_256=wrong_sig)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(self.message_for(self.g_sib).status, 'sent')

    def test_05_without_a_configured_secret_nothing_is_accepted(self):
        from unittest import mock
        message = self.sent_message()
        with mock.patch.dict('os.environ', {'WHATSAPP_APP_SECRET': ''}):
            response = self.post_webhook(self.status_payload(message.wamid, 'delivered'))
        self.assertEqual(response.status_code, 403)

    def test_05_the_handshake_needs_the_right_verify_token(self):
        client = Client(HTTP_HOST=HOST_A)
        ok = client.get('/webhooks/whatsapp/', {'hub.mode': 'subscribe', 'hub.verify_token': VERIFY_TOKEN, 'hub.challenge': '12345'})
        self.assertEqual((ok.status_code, ok.content), (200, b'12345'))
        self.assertEqual(client.get('/webhooks/whatsapp/', {'hub.mode': 'subscribe', 'hub.verify_token': 'nope', 'hub.challenge': '1'}).status_code, 403)
        self.assertEqual(client.get('/webhooks/whatsapp/').status_code, 403)

    def test_05_the_webhook_works_on_the_public_platform_address_too(self):
        from django.conf import settings
        from django.urls import get_urlconf, resolve
        self.assertEqual(resolve('/webhooks/whatsapp/', urlconf=settings.PUBLIC_SCHEMA_URLCONF).url_name, 'whatsapp_webhook')
        self.assertIsNone(get_urlconf())


class RoutingAndStatusTests(DigestTestCase):
    def setUp(self):
        super().setUp()
        from main_app.models import Guardian
        from messaging.models import DigestMessage, DigestRun
        _, ids = self.start()
        self.send(ids[0])
        self.msg_a = self.message_for(self.g_sib)
        from django_tenants.utils import tenant_context
        with tenant_context(self.college_b):  # College B has its own message with its own id
            guardian = Guardian.objects.create(full_name='B guardian', phone='+919000000001', whatsapp_consent='yes')
            run = DigestRun.objects.create(date=self.msg_a.digest_date, mode='live', state='done')
            self.msg_b = DigestMessage.objects.create(run=run, guardian=guardian, digest_date=run.date, mode='live',
                                                      status='sent', wamid='wamid.B1')
        from colleges.models import MessageRoute
        MessageRoute.objects.create(wamid='wamid.B1', college=self.college_b)

    def status_of(self, college, pk):
        from django_tenants.utils import tenant_context

        from messaging.models import DigestMessage
        with tenant_context(college):
            return DigestMessage.objects.get(pk=pk).status

    def test_07_a_report_updates_only_the_college_that_sent_the_message(self):
        self.post_webhook(self.status_payload(self.msg_a.wamid, 'delivered'))
        self.assertEqual(self.status_of(self.college_a, self.msg_a.pk), 'delivered')
        self.assertEqual(self.status_of(self.college_b, self.msg_b.pk), 'sent')
        self.post_webhook(self.status_payload('wamid.B1', 'read'), host='a')  # arrives on A's address, belongs to B
        self.assertEqual(self.status_of(self.college_b, self.msg_b.pk), 'read')
        self.assertEqual(self.status_of(self.college_a, self.msg_a.pk), 'delivered')

    def test_07_a_forged_or_unknown_message_id_is_ignored_and_logged(self):
        with self.assertLogs('messaging.webhook', level='WARNING'):
            response = self.post_webhook(self.status_payload('wamid.DOES-NOT-EXIST', 'delivered'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status_of(self.college_a, self.msg_a.pk), 'sent')

    def test_21_statuses_move_forward_only_and_every_report_is_kept(self):
        from messaging.models import DeliveryEvent
        for status, ts in (('sent', '1'), ('delivered', '2'), ('read', '3'), ('sent', '4'), ('delivered', '5'), ('failed', '6')):
            self.post_webhook(self.status_payload(self.msg_a.wamid, status, ts))
        self.assertEqual(self.status_of(self.college_a, self.msg_a.pk), 'read')  # late "sent"/"delivered"/"failed" did not move it back
        with self.t():
            self.assertEqual(DeliveryEvent.objects.filter(message=self.msg_a).count(), 6)

    def test_21_a_repeated_report_is_counted_once(self):
        from messaging.models import DeliveryEvent
        for _ in range(3):
            self.post_webhook(self.status_payload(self.msg_a.wamid, 'delivered', '2'))
        with self.t():
            self.assertEqual(DeliveryEvent.objects.filter(message=self.msg_a).count(), 1)

    def test_21_a_failed_report_records_metas_reason(self):
        self.post_webhook(self.status_payload(self.msg_a.wamid, 'failed', '2', errors=[{'code': 131026, 'title': 'Message undeliverable'}]))
        from messaging.models import DigestMessage
        with self.t():
            message = DigestMessage.objects.get(pk=self.msg_a.pk)
        self.assertEqual(message.status, 'failed')
        self.assertIn('Message undeliverable (code 131026)', message.last_error)

    def test_21_delivery_events_cannot_be_edited_or_deleted(self):
        from messaging.models import DeliveryEvent, ImmutableEvent
        self.post_webhook(self.status_payload(self.msg_a.wamid, 'delivered', '2'))
        with self.t():
            event = DeliveryEvent.objects.get()
            with self.assertRaises(ImmutableEvent):
                event.status = 'x'
                event.save()
            with self.assertRaises(ImmutableEvent):
                event.delete()
            with self.assertRaises(ImmutableEvent):
                DeliveryEvent.objects.all().update(status='x')
            with self.assertRaises(ImmutableEvent):
                DeliveryEvent.objects.all().delete()

    def test_23_metas_pricing_report_sets_the_category_and_a_non_billable_message_costs_nothing(self):
        from messaging.models import DigestMessage, UsageRate
        with self.t():
            UsageRate.objects.create(category='marketing', effective_from=self.msg_a.digest_date, rate='0.7846')
        self.post_webhook(self.status_payload(self.msg_a.wamid, 'sent', '2', pricing={'category': 'marketing', 'billable': True}))
        with self.t():
            message = DigestMessage.objects.get(pk=self.msg_a.pk)
        self.assertEqual((message.category, str(message.billed_rate)), ('marketing', '0.7846'))
        self.post_webhook(self.status_payload(self.msg_a.wamid, 'delivered', '3', pricing={'category': 'service', 'billable': False}))
        with self.t():
            self.assertEqual(DigestMessage.objects.get(pk=self.msg_a.pk).billed_rate, 0)


class OptOutTests(DigestTestCase):
    def reply(self, text, sender='919000000001'):
        return self.post_webhook({'entry': [{'changes': [{'value': {'messages': [{'from': sender, 'text': {'body': text}}]}}]}]})

    def consent_of(self, college, phone):
        from django_tenants.utils import tenant_context

        from main_app.models import Guardian
        with tenant_context(college):
            return Guardian.objects.filter(phone=phone).values_list('whatsapp_consent', flat=True).first()

    def test_24_stop_withdraws_consent_and_the_next_digest_skips_them(self):
        self.assertEqual(self.reply(' Stop ').status_code, 200)
        self.assertEqual(self.consent_of(self.college_a, '+919000000001'), 'no')
        _, ids = self.start()
        self.assertEqual(ids, [])
        self.assertEqual(self.message_for(self.g_sib).status, 'skipped_no_consent')
        self.assertEqual(self.sent, [])

    def test_24_the_tamil_word_works_and_other_replies_do_not(self):
        self.reply('thanks')
        self.assertEqual(self.consent_of(self.college_a, '+919000000001'), 'yes')
        self.reply('நிறுத்து')
        self.assertEqual(self.consent_of(self.college_a, '+919000000001'), 'no')

    def test_24_an_opt_out_is_recorded_with_where_it_came_from(self):
        from main_app.models import Guardian
        self.reply('STOP')
        with self.t():
            guardian = Guardian.objects.get(pk=self.g_sib.pk)
        self.assertEqual(guardian.consent_source, 'Replied STOP on WhatsApp')
        self.assertIsNotNone(guardian.consent_recorded_at)
