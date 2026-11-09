"""Checks 6-12, 14, 15: permissions, all-or-nothing saving, update mode, audit, isolation, idempotency."""
import hashlib
from unittest import mock

from django.test import Client
from django_tenants.utils import tenant_context
from openpyxl import load_workbook

from academics.models import Enrolment
from audit_trail.models import AuditLog
from bulk_import.models import ImportBatch
from colleges.tests.base import EMAIL_BACKEND, HOST_B
from main_app.models import CustomUser, Student

from .helpers import STAFF_HEADERS, STUDENT_HEADERS, ImportTestBase, student_row, upload_file, xlsx_bytes

IMPORT_URLS = ['/import/', '/import/students/', '/import/parents/', '/import/staff/', '/import/students/template/',
               '/import/parents/template/', '/import/staff/template/', '/import/problems/']
POST_URLS = ['/import/students/', '/import/confirm/', '/import/batch/1/rollback/']


class PermissionTests(ImportTestBase):
    def test_college_admin_can_use_every_page(self):
        for url in IMPORT_URLS[:-1]:
            self.assertEqual(self.client_admin.get(url).status_code, 200, url)

    def test_staff_students_and_head_of_department_get_403_everywhere(self):
        head = self.make_user(self.college_a, 'head@a.in', '4', 'Hari', 'Head')
        for user in (self.staff1, self.stud1, head):
            client = self.as_user(user)
            for url in IMPORT_URLS:
                self.assertEqual(client.get(url).status_code, 403, (user.email, url))
            for url in POST_URLS:
                self.assertEqual(client.post(url, {}).status_code, 403, (user.email, url))

    def test_a_forbidden_user_cannot_change_anything(self):
        before = self.snapshot()
        self.preview('students', STUDENT_HEADERS, [student_row(1)], client=self.as_user(self.staff1))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_anonymous_users_are_sent_to_login(self):
        client = Client(HTTP_HOST='a.testserver')
        for url in IMPORT_URLS:
            self.assertEqual(client.get(url).status_code, 302, url)

    def test_state_changes_need_post_and_csrf(self):
        for url in ('/import/confirm/', '/import/batch/1/rollback/'):
            self.assertEqual(self.client_admin.get(url).status_code, 405)
        csrf_client = Client(HTTP_HOST='a.testserver', enforce_csrf_checks=True)
        with tenant_context(self.college_a):
            csrf_client.force_login(self.admin, backend=EMAIL_BACKEND)
        self.assertEqual(csrf_client.post('/import/students/', {}).status_code, 403)
        self.assertEqual(csrf_client.post('/import/confirm/', {}).status_code, 403)

    def test_the_role_label_for_4_is_head_of_department_and_it_cannot_log_in_yet(self):
        from main_app.models import CustomUser as U
        self.assertEqual(dict(U.USER_TYPE)['4'], 'Head of department')
        client = Client(HTTP_HOST='a.testserver')
        self.make_user(self.college_a, 'h2@a.in', '4', 'H', 'Two')
        resp = client.post('/doLogin/', {'email': 'h2@a.in', 'password': 'Sup3r-secret-pw'}, follow=True)  # pragma: allowlist secret
        self.assertContains(resp, 'no pages yet')
        self.assertEqual(client.get('/import/').status_code, 302)  # not logged in


class SavingTests(ImportTestBase):
    def test_confirm_creates_users_without_passwords_in_the_right_section_with_enrolments(self):
        self.run_import('students', STUDENT_HEADERS, [student_row(1), student_row(2, section='A')])
        with tenant_context(self.college_a):
            for email in ('s1@a.in', 's2@a.in'):
                user = CustomUser.objects.get(email=email)
                self.assertFalse(user.has_usable_password())
                self.assertEqual(user.user_type, '3')
                student = user.student
                self.assertEqual(student.section_id, self.sec1)
                self.assertTrue(Enrolment.objects.filter(student=student, offering_id=self.sub1, status='active').exists())
            self.assertEqual(Student.objects.get(roll_number='21CS001').date_of_birth.isoformat(), '2006-04-23')
            self.assertEqual(CustomUser.objects.get(email='s1@a.in').phone, '+919876543210')

    def test_a_failure_part_way_leaves_nothing_behind(self):
        real_save = Student.save
        calls = []

        def flaky(self_, *a, **k):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError('boom')
            return real_save(self_, *a, **k)
        before = self.snapshot()
        resp = self.preview('students', STUDENT_HEADERS, [student_row(n) for n in range(1, 6)])
        with mock.patch.object(Student, 'save', flaky), self.assertRaises(RuntimeError):
            self.confirm(resp)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_confirm_needs_the_token_of_the_validated_file(self):
        resp = self.preview('students', STUDENT_HEADERS, [student_row(1)])
        bad = self.client_admin.post('/import/confirm/', {'token': 'wrong'}, follow=True)
        self.assertContains(bad, 'expired or was already used')
        self.assertEqual(self.count(ImportBatch), 0)
        # the wrong attempt used the preview up; a fresh upload is needed
        self.assertContains(self.confirm(resp), '', status_code=302)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_replacing_the_file_invalidates_the_old_preview(self):
        first = self.preview('students', STUDENT_HEADERS, [student_row(1)])
        self.preview('students', STUDENT_HEADERS, [student_row(2)])
        self.confirm(first)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_a_problem_file_cannot_be_confirmed(self):
        resp = self.preview('students', STUDENT_HEADERS, [student_row(1, email='bad')])
        self.confirm(resp)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_double_click_does_not_import_twice(self):
        resp = self.preview('students', STUDENT_HEADERS, [student_row(1)])
        self.confirm(resp)
        self.confirm(resp)
        self.assertEqual(self.count(ImportBatch), 1)
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.filter(email='s1@a.in').count(), 1)

    def test_previews_expire(self):
        resp = self.preview('students', STUDENT_HEADERS, [student_row(1)])
        with mock.patch('bulk_import.views.time.time', return_value=10 ** 12):
            self.confirm(resp)
        self.assertEqual(self.count(ImportBatch), 0)

    def test_the_batch_records_counts_and_the_sha256_of_the_file(self):
        payload = xlsx_bytes(STUDENT_HEADERS, [student_row(1)])
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client_admin.post('/import/students/', {'file': SimpleUploadedFile('a.xlsx', payload)})
        self.confirm(resp)
        with tenant_context(self.college_a):
            batch = ImportBatch.objects.get()
        self.assertEqual(batch.file_sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual((batch.created, batch.updated, batch.unchanged, batch.status), (1, 0, 0, 'applied'))
        self.assertEqual(batch.imported_by_id, self.admin.pk)

    def test_the_same_file_again_warns_and_creates_nothing(self):
        payload = xlsx_bytes(STUDENT_HEADERS, [student_row(1)])
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.confirm(self.client_admin.post('/import/students/', {'file': SimpleUploadedFile('a.xlsx', payload)}))
        again = self.client_admin.post('/import/students/', {'file': SimpleUploadedFile('a.xlsx', payload)})
        self.assertIsNotNone(again.context['duplicate'])
        self.assertContains(again, 'already imported')
        self.assertFalse(again.context['plan'].ok)  # create mode: every row now exists
        self.assertEqual(self.count(ImportBatch), 1)


class UpdateModeTests(ImportTestBase):
    def setUp(self):
        super().setUp()
        self.run_import('students', STUDENT_HEADERS, [student_row(1)])

    def update(self, **over):
        return self.preview('students', STUDENT_HEADERS, [student_row(1, **over)], update=True)

    def test_unchanged_rows_write_nothing(self):
        resp = self.update()
        self.assertEqual(resp.context['counts'], (0, 0, 1))
        with tenant_context(self.college_a):
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first()
        self.confirm(resp)
        with tenant_context(self.college_a):
            changed = {e.model_label for e in AuditLog.objects.filter(id__gt=mark)}
            self.assertEqual(changed, {'bulk_import.ImportBatch'})  # no person or student row was touched
            self.assertEqual(ImportBatch.objects.count(), 2)

    def test_only_the_listed_fields_change_and_the_password_is_untouched(self):
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='s1@a.in')
            user.set_password('Sup3r-secret-pw')  # pragma: allowlist secret
            user.save()
            hash_before = user.password
        resp = self.update(first_name='Renamed', phone='9123456780', section='A')
        self.assertEqual(resp.context['counts'], (0, 1, 0))
        self.confirm(resp)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='s1@a.in')
            self.assertEqual((user.first_name, user.phone, user.email), ('Renamed', '+919123456780', 's1@a.in'))
            self.assertEqual(user.password, hash_before)

    def test_identity_is_never_changed(self):
        problems = self.problems(self.update(email='other@a.in'))
        self.assertEqual([(r, c) for r, c, _ in problems], [(2, 'email')])

    def test_unticked_update_mode_treats_existing_rows_as_errors(self):
        resp = self.preview('students', STUDENT_HEADERS, [student_row(1)])
        self.assertFalse(resp.context['plan'].ok)

    def test_a_section_change_follows_the_transfer_rules(self):
        resp = self.update(programme_code='C2')
        self.confirm(resp)
        with tenant_context(self.college_a):
            student = Student.objects.get(roll_number='21CS001')
            self.assertEqual(student.section_id, self.sec2)
            self.assertEqual(Enrolment.objects.get(student=student, offering_id=self.sub1).status, 'withdrawn')
            self.assertTrue(Enrolment.objects.filter(student=student, offering_id=self.sub2, status='active').exists())

    def test_the_preview_warns_that_updates_cannot_be_rolled_back(self):
        self.assertContains(self.update(first_name='Renamed'), 'cannot be rolled back')


class StaffImportTests(ImportTestBase):
    def test_staff_import_creates_staff_with_department_and_no_password(self):
        rows = [['E1001', 'Meena', 'Iyer', 'meena@a.in', 'F', '9876500001', 'CSE'],
                ['e1002', 'Karan', 'Rao', 'karan@a.in', 'M', '', '']]
        self.run_import('staff', STAFF_HEADERS, rows)
        from main_app.models import Staff
        with tenant_context(self.college_a):
            staff = Staff.objects.get(employee_id='E1001')
            self.assertEqual(staff.department_id, self.dept1)
            self.assertEqual(staff.admin.user_type, '2')
            self.assertFalse(staff.admin.has_usable_password())
        again = self.preview('staff', STAFF_HEADERS, [['E1002', 'x', 'y', 'z@a.in', '', '', '']])
        self.assertIn('employee_id', [c for _, c, _ in self.problems(again)])

    def test_unknown_department_is_reported(self):
        resp = self.preview('staff', STAFF_HEADERS, [['E1', 'a', 'b', 'c@a.in', '', '', 'NOPE']])
        self.assertIn((2, 'department_code'), [(r, c) for r, c, _ in self.problems(resp)])


class AuditAndIsolationTests(ImportTestBase):
    def test_every_created_row_and_the_batch_are_audited_with_the_admin_as_actor(self):
        with tenant_context(self.college_a):
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0
        self.run_import('students', STUDENT_HEADERS, [student_row(1), student_row(2)])
        with tenant_context(self.college_a):
            entries = list(AuditLog.objects.filter(id__gt=mark))
        labels = {}
        for e in entries:
            labels.setdefault(e.model_label, []).append(e)
        self.assertEqual(len(labels['main_app.CustomUser']), 2)
        self.assertEqual(len(labels['bulk_import.ImportBatch']), 1)
        for e in entries:
            if e.model_label in ('main_app.CustomUser', 'bulk_import.ImportBatch'):
                self.assertEqual(e.actor_email, 'admin@a.in')
        student_creates = [e for e in labels['main_app.Student'] if e.action == 'create']
        self.assertEqual(len(student_creates), 2)
        for e in student_creates:  # personal fields stay masked
            self.assertNotIn('21CS001', str(e.changes))
            self.assertNotIn('2006-04-23', str(e.changes))
        self.assertNotIn('9876543210', ' '.join(str(e.changes) for e in entries))

    def test_two_colleges_can_use_the_same_roll_numbers_and_previews_do_not_cross(self):
        self.run_import('students', STUDENT_HEADERS, [student_row(1)])
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        with tenant_context(self.college_b):
            import datetime

            from academics.models import AcademicTerm, Batch, Department, Programme, Regulation, Section
            dept = Department.objects.create(name='CSE', code='CSE')
            prog = Programme.objects.create(department=dept, name='C1', code='C1', duration_semesters=8)
            reg = Regulation.objects.create(programme=prog, name='R1', effective_from=datetime.date(2025, 1, 1))
            Section.objects.create(batch=Batch.objects.create(programme=prog, regulation=reg, start_year=2025), name='A')
            AcademicTerm.objects.create(name='T', start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2099, 1, 1))
        client_b = Client(HTTP_HOST=HOST_B)
        with tenant_context(self.college_b):
            client_b.force_login(admin_b, backend=EMAIL_BACKEND)
        resp_a = self.preview('students', STUDENT_HEADERS, [student_row(2)])
        resp_b = self.preview('students', STUDENT_HEADERS, [student_row(1)], client=client_b)  # same roll number as in A
        self.assertTrue(resp_b.context['plan'].ok)
        self.confirm(resp_b, client=client_b)
        client_b.post('/import/confirm/', {'token': resp_a.context['token']})  # A's token is useless in B
        with tenant_context(self.college_b):
            self.assertEqual(Student.objects.filter(roll_number__isnull=False).count(), 1)
            self.assertEqual(ImportBatch.objects.count(), 1)
        with tenant_context(self.college_a):
            self.assertEqual(ImportBatch.objects.count(), 1)  # A's second preview was never confirmed

    def test_the_uploaded_file_is_not_kept(self):
        import os

        from django.conf import settings
        self.run_import('students', STUDENT_HEADERS, [student_row(1)])
        found = [f for root, _, files in os.walk(settings.MEDIA_ROOT) for f in files if f.endswith('.xlsx')] \
            if os.path.isdir(settings.MEDIA_ROOT) else []
        self.assertEqual(found, [])

    def test_templates_and_the_error_sheet_download_as_xlsx(self):
        import io
        for kind in ('students', 'parents', 'staff'):
            resp = self.client_admin.get(f'/import/{kind}/template/')
            self.assertEqual(resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            wb = load_workbook(io.BytesIO(resp.content))
            self.assertEqual(wb.sheetnames, ['Template', 'Notes'])
        notes = load_workbook(io.BytesIO(self.client_admin.get('/import/students/template/').content))['Notes']
        self.assertIn('C1', [c.value for row in notes.iter_rows() for c in row])  # this college's own programme codes
        self.preview('students', STUDENT_HEADERS, [student_row(1, first_name='=cmd', email='nope')])
        sheet = load_workbook(io.BytesIO(self.client_admin.get('/import/problems/').content)).active
        values = [[c.value for c in row] for row in sheet.iter_rows()]
        self.assertEqual(values[0], ['Row', 'Column', 'Problem'])
        self.assertTrue(any(v[1] == 'email' for v in values[1:]))
        for row in values:
            for cell in row:
                self.assertFalse(isinstance(cell, str) and cell.startswith(('=', '+', '-', '@')), cell)

    def test_files_can_be_uploaded_with_the_xlsx_helper(self):
        self.assertTrue(upload_file(STUDENT_HEADERS, []).size > 0)
