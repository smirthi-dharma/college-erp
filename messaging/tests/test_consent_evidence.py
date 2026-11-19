"""Consent evidence (DPDP): who, when, how, optional signed-form scan; append-only; private scans."""
import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from main_app import consent
from main_app.models import ConsentRecord, ConsentRecordImmutable, Guardian

from .base import DigestTestCase

PDF = b'%PDF-1.4\n%fake signed form\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20
JPG = b'\xff\xd8\xff\xe0' + b'0' * 20


def upload(name, content):
    return SimpleUploadedFile(name, content, content_type='application/octet-stream')


class EvidenceTestCase(DigestTestCase):
    def setUp(self):
        super().setUp()
        with self.t():
            Guardian.objects.filter(pk=self.g_sib.pk).update(whatsapp_consent='unknown')

    def record(self, value='yes', method='paper_form', note='', scan=None, user=None, **kwargs):
        with self.t():
            guardian = Guardian.objects.get(pk=self.g_sib.pk)
            record = consent.record_consent(guardian, value, method, user=user or self.admin, note=note, scan=scan, **kwargs)
            if record.scan:
                self.addCleanup(self.remove_file, record.scan.path)  # the storage's folder is the project's media folder
            return record

    @staticmethod
    def remove_file(path):
        if os.path.exists(path):
            os.remove(path)

    def post(self, data, files=None, user=None):
        data = dict({'guardian': self.g_sib.pk}, **data)
        if files:
            data.update(files)
        return self.as_user(user or self.admin).post('/messaging/consent/', data)

    def current(self):
        with self.t():
            return Guardian.objects.get(pk=self.g_sib.pk).whatsapp_consent


class RecordingTests(EvidenceTestCase):
    def test_a_record_captures_who_when_how_and_updates_the_current_answer(self):
        before = timezone.now()
        record = self.record(note='Signed at admission, form no. 42')
        self.assertEqual((record.recorded_by_id, record.recorded_by_email, record.recorded_by_role), (self.admin.pk, 'admin@a.in', '1'))
        self.assertEqual((record.value, record.method, record.note), ('yes', 'paper_form', 'Signed at admission, form no. 42'))
        self.assertTrue(before <= record.recorded_at <= timezone.now())
        self.assertEqual(self.current(), 'yes')
        with self.t():
            self.assertEqual(Guardian.objects.get(pk=self.g_sib.pk).consent_recorded_at, record.recorded_at)

    def test_every_change_is_a_new_record_so_a_withdrawal_keeps_the_history(self):
        self.record('yes')
        self.record('no', 'verbal', note='Father phoned the office')
        with self.t():
            history = list(ConsentRecord.objects.filter(guardian=self.g_sib).order_by('id').values_list('value', 'method'))
        self.assertEqual(history, [('yes', 'paper_form'), ('no', 'verbal')])
        self.assertEqual(self.current(), 'no')

    def test_records_cannot_be_edited_or_deleted(self):
        record = self.record()
        with self.t():
            with self.assertRaises(ConsentRecordImmutable):
                record.note = 'changed'
                record.save()
            with self.assertRaises(ConsentRecordImmutable):
                record.delete()
            with self.assertRaises(ConsentRecordImmutable):
                ConsentRecord.objects.all().update(value='no')
            with self.assertRaises(ConsentRecordImmutable):
                ConsentRecord.objects.all().delete()

    def test_the_rules(self):
        with self.assertRaises(consent.ConsentError):
            self.record('maybe')
        with self.assertRaises(consent.ConsentError):
            self.record(method='telepathy')
        with self.assertRaises(consent.ConsentError):
            self.record(method='verbal', note='')  # verbal needs a note
        with self.assertRaises(consent.ConsentError):
            self.record(method='parent_self_service')  # staff cannot claim the parent did it themselves
        with self.assertRaises(consent.ConsentError):
            self.record(method='whatsapp_stop', value='no')  # a STOP reply is the system's, not a person's
        with self.assertRaises(consent.ConsentError):
            self.record(user=self.staff1)  # only the college admin
        self.assertEqual(self.current(), 'unknown')
        with self.t():
            self.assertEqual(ConsentRecord.objects.count(), 0)  # nothing half-saved

    def test_the_portal_can_record_self_service_later(self):
        record = self.record(method='parent_self_service', user=self.admin, allow_self_service=True)
        self.assertEqual(record.method, 'parent_self_service')

    def test_a_stop_reply_is_recorded_by_the_system(self):
        self.record('yes')
        self.post_webhook({'entry': [{'changes': [{'value': {'messages': [{'from': '919000000001', 'text': {'body': 'STOP'}}]}}]}]})
        with self.t():
            latest = ConsentRecord.objects.filter(guardian=self.g_sib).first()
        self.assertEqual((latest.value, latest.method, latest.recorded_by_email, latest.recorded_by_id), ('no', 'whatsapp_stop', 'system', None))
        self.assertEqual(self.current(), 'no')


class ScanTests(EvidenceTestCase):
    def test_a_pdf_png_and_jpeg_scan_are_stored_under_a_random_name_in_the_colleges_own_folder(self):
        for name, content in (('form.pdf', PDF), ('form.PNG', PNG), ('form.jpeg', JPG)):
            record = self.record(scan=upload('../../etc/' + name, content))
            self.assertRegex(record.scan.name, r'^consent_scans/[0-9a-f]{32}\.(pdf|png|jpeg)$')
            with self.t():  # the storage resolves the college from the current schema
                self.assertTrue(os.path.exists(record.scan.path))
                self.assertIn(os.path.join('', self.college_a.schema_name, 'consent_scans'), record.scan.path)

    def test_bad_scans_are_refused_and_nothing_is_saved(self):
        cases = [('form.exe', PDF), ('form.pdf', b'MZ\x90\x00 not a pdf'), ('form.png', PDF),
                 ('form.pdf', PDF + b'0' * (consent.SCAN_MAX_BYTES + 1)), ('noextension', PDF)]
        for name, content in cases:
            with self.assertRaises(consent.ConsentError, msg=name):
                self.record(scan=upload(name, content))
        with self.t():
            self.assertEqual(ConsentRecord.objects.count(), 0)
        self.assertEqual(self.current(), 'unknown')

    def test_the_form_accepts_a_scan_and_the_college_admin_can_download_it(self):
        response = self.post({'value': 'yes', 'method': 'paper_form'}, {'scan': upload('f.pdf', PDF)})
        self.assertEqual(response.status_code, 302)
        with self.t():
            record = ConsentRecord.objects.get()
            self.addCleanup(self.remove_file, record.scan.path)
        download = self.as_user(self.admin).get('/messaging/consent/scan/%d/' % record.pk)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), PDF)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', download['Cache-Control'])

    def test_a_record_without_a_scan_has_no_download(self):
        record = self.record()
        self.assertEqual(self.as_user(self.admin).get('/messaging/consent/scan/%d/' % record.pk).status_code, 404)

    def test_the_scan_is_not_reachable_from_a_public_media_url(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        record = self.record(scan=upload('f.pdf', PDF))
        for url in ('/media/' + record.scan.name, '/media/%s/%s' % (self.college_a.schema_name, record.scan.name)):
            anon = Client(HTTP_HOST=HOST_A).get(url)
            self.assertNotEqual(anon.status_code, 200, url)
            self.assertNotIn(PDF, anon.content)


class AccessTests(EvidenceTestCase):
    def test_only_the_college_admin_may_record_view_history_or_download(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        record = self.record(scan=upload('f.pdf', PDF))
        hod = self.make_user(self.college_a, 'hod@a.in', '4', 'Hod', 'One')
        urls = ['/messaging/consent/%d/' % self.g_sib.pk, '/messaging/consent/scan/%d/' % record.pk]
        for user in (self.staff1, self.stud1, hod):
            client = self.as_user(user)
            for url in urls:
                self.assertEqual(client.get(url).status_code, 403, (url, user.email))
            self.assertEqual(client.post('/messaging/consent/', {'guardian': self.g_sib.pk, 'value': 'no', 'method': 'verbal', 'note': 'x'}).status_code, 403)
        for url in urls:
            self.assertEqual(Client(HTTP_HOST=HOST_A).get(url).status_code, 302)
        self.assertEqual(self.current(), 'yes')  # none of the refused requests changed anything

    def test_college_b_cannot_see_or_download_college_a_evidence(self):
        record = self.record(scan=upload('f.pdf', PDF))
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        client = self.client_for(self.college_b, admin_b)
        self.assertEqual(client.get('/messaging/consent/scan/%d/' % record.pk).status_code, 404)
        self.assertEqual(client.get('/messaging/consent/%d/' % self.g_sib.pk).status_code, 404)
        self.assertNotContains(client.get('/messaging/consent/'), 'Mr Sib')


class PageTests(EvidenceTestCase):
    def test_the_list_shows_who_how_when_and_flags_a_missing_evidence(self):
        with self.t():
            Guardian.objects.filter(pk=self.g_other.pk).update(whatsapp_consent='yes')  # a yes with no record: legacy
        self.record(note='Form 7')
        body = self.as_user(self.admin).get('/messaging/consent/').content.decode()
        self.assertIn('Paper form', body)
        self.assertIn('admin@a.in', body)
        self.assertEqual(body.count('No evidence on file'), 1)  # only Mrs Other

    def test_the_history_page_lists_every_decision_newest_first(self):
        self.record('yes', note='Form 7')
        self.record('no', 'verbal', note='Phoned')
        body = self.as_user(self.admin).get('/messaging/consent/%d/' % self.g_sib.pk).content.decode()
        self.assertLess(body.index('Phoned'), body.index('Form 7'))
        self.assertIn('No</td><td>Verbal', body.replace('\n', ''))

    def test_the_screen_never_offers_parent_self_service(self):
        body = self.as_user(self.admin).get('/messaging/consent/').content.decode()
        self.assertIn('value="paper_form"', body)
        self.assertIn('value="verbal"', body)
        self.assertNotIn('parent_self_service', body)
        response = self.post({'value': 'yes', 'method': 'parent_self_service'})
        self.assertEqual(self.current(), 'unknown')
        self.assertEqual(response.status_code, 302)
