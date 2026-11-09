"""Audit log: every change is recorded, with who/when/where, and cannot be altered."""
import json

from django.db import DatabaseError, connection, transaction
from django_tenants.utils import tenant_context

from audit_trail.models import AuditLog, AuditLogImmutable
from main_app.tests.world import WorldTestCase


class AuditTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.mark = self._max_id()  # the world fixture itself writes entries; look only at new ones

    def _max_id(self, college=None):
        with tenant_context(college or self.college_a):
            return AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0

    def new(self, college=None, **filters):
        with tenant_context(college or self.college_a):
            return list(AuditLog.objects.filter(id__gt=self.mark, **filters).order_by('id'))


class RecordingTests(AuditTestCase):
    def test_create_update_delete_are_recorded_with_field_changes(self):
        from main_app.models import StudentResult
        with tenant_context(self.college_a):
            result = StudentResult.objects.get(student__admin_id=self.stud1.pk, subject_id=self.sub1)
            result_pk = result.pk
            result.test = 99
            result.save()
            result.delete()
        entries = self.new(model_label='main_app.StudentResult')
        self.assertEqual([e.action for e in entries], ['update', 'delete'])
        self.assertEqual(entries[0].changes, {'test': ['11.0', '99.0']})  # only the changed field
        self.assertEqual(entries[0].object_pk, str(result_pk))
        self.assertEqual(entries[1].changes['test'], ['99.0', None])

    def test_create_is_recorded(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            course = Course.objects.create(name='Brand new')
        (entry,) = self.new(model_label='main_app.Course')
        self.assertEqual((entry.action, entry.object_pk, entry.changes['name']), ('create', str(course.pk), [None, 'Brand new']))

    def test_a_save_that_changes_nothing_is_not_logged(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            course = Course.objects.get(pk=self.course1)
            course.save()
        self.assertEqual(self.new(model_label='main_app.Course'), [])

    def test_cascaded_deletes_are_recorded(self):
        with tenant_context(self.college_a):
            from main_app.models import Subject
            Subject.objects.get(pk=self.sub1).delete()
        labels = {e.model_label for e in self.new(action='delete')}
        self.assertTrue({'main_app.Subject', 'main_app.Attendance', 'main_app.AttendanceReport',
                         'main_app.StudentResult'} <= labels)

    def test_changes_made_outside_a_request_are_marked_as_system(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            Course.objects.create(name='From a script')
        (entry,) = self.new(model_label='main_app.Course')
        self.assertEqual((entry.source, entry.actor_id, entry.actor_email), ('system', None, ''))

    def test_a_rolled_back_change_leaves_no_entry(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    Course.objects.create(name='Rolled back')
                    raise RuntimeError('boom')
        self.assertEqual(self.new(model_label='main_app.Course'), [])

    def test_unaudited_framework_tables_are_not_logged(self):
        self.as_user(self.admin).get('/admin/home/')
        self.assertEqual([e for e in self.new() if 'session' in e.model_label.lower()], [])


class WhoAndWhereTests(AuditTestCase):
    def test_http_change_records_actor_role_ip_and_request(self):
        self.as_user(self.admin, REMOTE_ADDR='203.0.113.7', HTTP_USER_AGENT='TestBrowser/1').post(
            '/course/add', {'name': 'Added by HoD'})
        (entry,) = self.new(model_label='main_app.Course')
        self.assertEqual(entry.actor_email, 'admin@a.in')
        self.assertEqual(entry.actor_role, '1')
        self.assertEqual(entry.actor_id, self.admin.pk)
        self.assertEqual((entry.ip_address, entry.user_agent), ('203.0.113.7', 'TestBrowser/1'))
        self.assertEqual((entry.request_method, entry.request_path, entry.source), ('POST', '/course/add', 'web'))

    def test_staff_marks_entry_is_attributed_to_the_teacher(self):
        self.as_user(self.staff1).post('/staff/result/add/', {
            'student_list': self.stud_ids['stud1@a.in'], 'subject': self.sub1, 'test': 61, 'exam': 62})
        (entry,) = self.new(model_label='main_app.StudentResult')
        self.assertEqual((entry.actor_email, entry.actor_role), ('staff1@a.in', '2'))
        self.assertEqual(entry.changes, {'test': ['11.0', '61.0'], 'exam': ['22.0', '62.0']})

    def test_forwarded_header_is_ignored_unless_a_trusted_proxy_header_is_configured(self):
        self.as_user(self.admin, REMOTE_ADDR='198.51.100.9', HTTP_X_FORWARDED_FOR='6.6.6.6').post(
            '/course/add', {'name': 'Spoof attempt'})
        (entry,) = self.new(model_label='main_app.Course')
        self.assertEqual(entry.ip_address, '198.51.100.9')

    def test_login_success_failure_and_logout_are_recorded(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        client = Client(HTTP_HOST=HOST_A, REMOTE_ADDR='203.0.113.50')
        client.post('/doLogin/', {'email': 'admin@a.in', 'password': 'wrong-password-123'})  # pragma: allowlist secret
        client.post('/doLogin/', {'email': 'admin@a.in', 'password': 'Sup3r-secret-pw'})  # pragma: allowlist secret
        client.post('/logout_user/')
        actions = [(e.action, e.actor_email, e.ip_address) for e in self.new() if e.action in
                   ('login', 'login_failed', 'logout')]
        self.assertEqual(actions, [('login_failed', 'admin@a.in', '203.0.113.50'),
                                   ('login', 'admin@a.in', '203.0.113.50'),
                                   ('logout', 'admin@a.in', '203.0.113.50')])
        self.assertNotIn('wrong-password-123', json.dumps([e.changes for e in self.new()]))


class SensitiveDataTests(AuditTestCase):
    def test_password_hashes_and_personal_data_are_never_stored(self):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.stud1.pk)
            user.set_password('A-brand-new-password-9')  # pragma: allowlist secret
            user.first_name = 'Changed-Name'
            user.address = '12 Secret Street'
            user.fcm_token = 'device-token-xyz'
            user.save()
        (entry,) = self.new(model_label='main_app.CustomUser', action='update')
        self.assertEqual(entry.changes, {'password': ['[redacted]', '[redacted]'],
                                         'first_name': ['[personal data]', '[personal data]'],
                                         'address': ['[personal data]', '[personal data]'],
                                         'fcm_token': ['[redacted]', '[redacted]']})
        dump = json.dumps([e.changes for e in self.new()]) + ''.join(e.object_repr for e in self.new())
        for secret in ('pbkdf2', 'Changed-Name', 'Secret Street', 'device-token-xyz', 'Stu'):
            self.assertNotIn(secret, dump)

    def test_deleting_a_user_keeps_the_entry_but_no_personal_data(self):
        pk = self.stud3.pk
        with tenant_context(self.college_a):
            self.stud3.delete()
        deleted = self.new(model_label='main_app.CustomUser', action='delete')
        self.assertEqual(len(deleted), 1)
        self.assertNotIn('stud3@a.in', json.dumps(deleted[0].changes))
        self.assertEqual(deleted[0].object_repr, 'CustomUser #%d' % pk)


class ImmutabilityTests(AuditTestCase):
    def _entry(self):
        with tenant_context(self.college_a):
            return AuditLog.objects.filter(id__gt=self.mark).first() or AuditLog.objects.create(action='update')

    def test_orm_cannot_change_or_delete_entries(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            Course.objects.create(name='x')
            entry = AuditLog.objects.filter(id__gt=self.mark).first()
            entry.changes = {'tampered': True}
            with self.assertRaises(AuditLogImmutable):
                entry.save()
            with self.assertRaises(AuditLogImmutable):
                entry.delete()
            with self.assertRaises(AuditLogImmutable):
                AuditLog.objects.all().update(actor_email='nobody')
            with self.assertRaises(AuditLogImmutable):
                AuditLog.objects.all().delete()

    def test_database_itself_refuses_update_delete_and_truncate(self):
        from main_app.models import Course
        with tenant_context(self.college_a):
            Course.objects.create(name='y')
            for sql in ("UPDATE audit_trail_auditlog SET actor_email = 'x'",
                        "DELETE FROM audit_trail_auditlog",
                        "TRUNCATE audit_trail_auditlog"):
                with self.assertRaises(DatabaseError, msg=sql):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(sql)
            self.assertTrue(AuditLog.objects.exists())


class IsolationTests(AuditTestCase):
    def test_each_college_has_its_own_log(self):
        from main_app.models import Course
        self.mark_b = self._max_id(self.college_b)
        with tenant_context(self.college_a):
            Course.objects.create(name='Only in A')
        with tenant_context(self.college_b):
            Course.objects.create(name='Only in B')
            names_b = [e.changes.get('name') for e in AuditLog.objects.filter(model_label='main_app.Course')]
        with tenant_context(self.college_a):
            names_a = [e.changes.get('name') for e in AuditLog.objects.filter(model_label='main_app.Course')]
        self.assertIn([None, 'Only in A'], names_a)
        self.assertNotIn([None, 'Only in B'], names_a)
        self.assertIn([None, 'Only in B'], names_b)
        self.assertNotIn([None, 'Only in A'], names_b)

    def test_log_table_exists_only_in_college_schemas(self):
        with connection.cursor() as cursor:
            cursor.execute("select table_schema from information_schema.tables where table_name = 'audit_trail_auditlog'")
            schemas = {row[0] for row in cursor.fetchall()}
        self.assertIn('college_a', schemas)
        self.assertNotIn('public', schemas)


class NoUnloggedWritesTests(AuditTestCase):
    def test_application_code_does_not_use_bulk_writes_that_skip_the_log(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[2] / 'main_app'
        offenders = []
        for path in root.glob('*.py'):
            text = path.read_text()
            for m in re.finditer(r'\.(bulk_create|bulk_update|raw)\(|objects\.[a-z_]+\([^)]*\)\.update\(|QuerySet\.update\(', text):
                offenders.append((path.name, m.group(0)))
        self.assertEqual(offenders, [], 'use save()/delete() so the change is audited')
