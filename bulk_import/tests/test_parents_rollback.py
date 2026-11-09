"""C2 rollback, C3 consent guard, C5 siblings, performance (check 17)."""
import os
import re
import time
from unittest import mock

from django.conf import settings
from django_tenants.utils import tenant_context

from academics.models import Enrolment, SubjectOffering
from audit_trail.models import AuditLog
from bulk_import import services
from bulk_import.models import ImportBatch
from main_app import whatsapp
from main_app.models import AttendanceReport, CustomUser, Guardian, Student, StudentGuardian, StudentResult

from .helpers import PARENT_HEADERS, STUDENT_HEADERS, ImportTestBase, student_row


def parent_row(roll, name='Ravi Kumar', relation='father', phone='9876543211', email='', primary='yes'):
    return [roll, name, relation, phone, email, primary]


class ParentImportTests(ImportTestBase):
    def setUp(self):
        super().setUp()
        self.run_import('students', STUDENT_HEADERS, [student_row(1), student_row(2), student_row(3)])

    def test_parents_are_linked_to_the_right_students(self):
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001'), parent_row('21CS002', 'Sita Kumar', 'mother', '9876543212', 's@x.in', 'no')])
        with tenant_context(self.college_a):
            self.assertEqual(Guardian.objects.count(), 2)
            link = StudentGuardian.objects.get(student__roll_number='21CS001')
            self.assertEqual((link.relation, link.is_primary, link.guardian.phone), ('father', True, '+919876543211'))
            self.assertEqual(Guardian.objects.get(phone='+919876543212').email, 's@x.in')

    def test_siblings_share_one_guardian(self):
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001'), parent_row('21CS002'), parent_row('21CS003', primary='no')])
        with tenant_context(self.college_a):
            self.assertEqual(Guardian.objects.count(), 1)
            guardian = Guardian.objects.get()
            self.assertEqual(guardian.studentguardian_set.count(), 3)
            self.assertEqual(sorted(guardian.studentguardian_set.values_list('student__roll_number', flat=True)),
                             ['21CS001', '21CS002', '21CS003'])
        batch = self.latest_batch()
        self.assertEqual((batch.created, len(batch.created_refs['guardians']), len(batch.created_refs['links'])), (3, 1, 3))

    def test_a_guardian_already_on_record_is_linked_not_duplicated(self):
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS002', name='R. Kumar')])  # same phone, another child
        with tenant_context(self.college_a):
            self.assertEqual(Guardian.objects.count(), 1)
            self.assertEqual(Guardian.objects.get().full_name, 'Ravi Kumar')  # not overwritten
        warn = self.preview('parents', PARENT_HEADERS, [parent_row('21CS003', name='Someone Else')])
        self.assertTrue(warn.context['plan'].ok)
        self.assertIn('name', [c for _, c, _ in warn.context['plan'].warnings])

    def test_consent_is_unknown_and_an_import_can_never_set_it(self):
        headers = PARENT_HEADERS + ['whatsapp_consent']
        resp = self.preview('parents', headers, [parent_row('21CS001') + ['yes']])
        self.assertIn('whatsapp_consent', [c for _, c, _ in resp.context['plan'].warnings])
        self.confirm(resp)
        with tenant_context(self.college_a):
            self.assertEqual(Guardian.objects.get().whatsapp_consent, 'unknown')

    def test_validation_rules(self):
        cases = {
            'student_roll_number': parent_row('NOPE'),
            'relation': parent_row('21CS001', relation='uncle'),
            'phone': parent_row('21CS001', phone='123'),
            'name': parent_row('21CS001', name=''),
            'is_primary': parent_row('21CS001', primary='maybe'),
        }
        for column, row in cases.items():
            resp = self.preview('parents', PARENT_HEADERS, [row])
            self.assertIn(column, [c for _, c, _ in self.problems(resp)], column)

    def test_one_primary_guardian_per_student(self):
        two = self.preview('parents', PARENT_HEADERS, [parent_row('21CS001'), parent_row('21CS001', 'Sita', 'mother', '9876543212')])
        self.assertIn('is_primary', [c for _, c, _ in self.problems(two)])
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        third = self.preview('parents', PARENT_HEADERS, [parent_row('21CS001', 'Sita', 'mother', '9876543212')])
        self.assertIn('is_primary', [c for _, c, _ in self.problems(third)])

    def test_existing_links_are_errors_in_create_mode_and_nochange_in_update_mode(self):
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        self.assertFalse(self.preview('parents', PARENT_HEADERS, [parent_row('21CS001')]).context['plan'].ok)
        self.assertEqual(self.preview('parents', PARENT_HEADERS, [parent_row('21CS001')], update=True).context['counts'], (0, 0, 1))

    def test_the_database_allows_at_most_one_primary_per_student(self):
        from django.db import IntegrityError, transaction
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        with tenant_context(self.college_a):
            student = Student.objects.get(roll_number='21CS001')
            other = Guardian.objects.create(full_name='X', phone='+919000000001')
            with self.assertRaises(IntegrityError), transaction.atomic():
                StudentGuardian.objects.create(student=student, guardian=other, relation='mother', is_primary=True)

    def latest_batch(self):
        with tenant_context(self.college_a):
            return ImportBatch.objects.first()


class ConsentGuardTests(ImportTestBase):
    def guardian(self, consent):
        with tenant_context(self.college_a):
            return Guardian.objects.create(full_name='G', phone='+919000000009', whatsapp_consent=consent)

    def test_nothing_is_sent_unless_consent_is_exactly_yes(self):
        with mock.patch('main_app.whatsapp._transport') as transport, tenant_context(self.college_a):
            for consent in ('unknown', 'no', '', 'YES', 'true'):
                guardian = self.guardian('unknown')
                Guardian.objects.filter(pk=guardian.pk).update(whatsapp_consent=consent)
                with self.assertRaises(whatsapp.ConsentRequired):
                    whatsapp.send_whatsapp(guardian, 'hello')
                Guardian.objects.filter(pk=guardian.pk).delete()
            transport.assert_not_called()

    def test_yes_passes_and_a_withdrawal_is_honoured_even_with_a_stale_object(self):
        with mock.patch('main_app.whatsapp._transport', return_value='sent') as transport, tenant_context(self.college_a):
            guardian = self.guardian('yes')
            self.assertEqual(whatsapp.send_whatsapp(guardian, 'hi'), 'sent')
            transport.assert_called_once_with('+919000000009', 'hi')
            Guardian.objects.filter(pk=guardian.pk).update(whatsapp_consent='no')  # withdrawn elsewhere; guardian is stale
            with self.assertRaises(whatsapp.ConsentRequired):
                whatsapp.send_whatsapp(guardian, 'again')
            self.assertEqual(transport.call_count, 1)

    def test_no_other_code_may_call_the_transport(self):
        offenders = []
        for root, dirs, files in os.walk(settings.BASE_DIR):
            dirs[:] = [d for d in dirs if d not in ('.venv', 'node_modules', '.git', 'migrations')]
            for name in files:
                path = os.path.join(root, name)
                if name.endswith('.py') and '_transport' in open(path, encoding='utf-8').read():
                    if not (path.endswith(os.path.join('main_app', 'whatsapp.py')) or '/tests/' in path):
                        offenders.append(path)
        self.assertEqual(offenders, [])

    def test_new_guardians_start_as_unknown(self):
        with tenant_context(self.college_a):
            self.assertEqual(Guardian.objects.create(full_name='x', phone='+919111111111').whatsapp_consent, 'unknown')
            self.assertEqual(re.sub(r'\W', '', Guardian._meta.get_field('whatsapp_consent').default), 'unknown')


class RollbackTests(ImportTestBase):
    def import_students(self, n=2):
        self.run_import('students', STUDENT_HEADERS, [student_row(i) for i in range(1, n + 1)])
        with tenant_context(self.college_a):
            return ImportBatch.objects.first()

    def rollback(self, batch, client=None):
        return (client or self.client_admin).post(f'/import/batch/{batch.pk}/rollback/', follow=True)

    def refresh(self, batch):
        with tenant_context(self.college_a):
            return ImportBatch.objects.get(pk=batch.pk)

    def test_a_clean_batch_is_rolled_back_exactly_and_audited(self):
        batch = self.import_students(3)
        with tenant_context(self.college_a):
            before_users = CustomUser.objects.count()
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first()
        self.assertEqual(services.rollback_blockers(batch), [])
        self.assertContains(self.rollback(batch), 'rolled back')
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.count(), before_users - 3)
            self.assertFalse(Student.objects.filter(roll_number__startswith='21CS').exists())
            self.assertFalse(Enrolment.objects.filter(student__roll_number__startswith='21CS').exists())
            self.assertTrue(CustomUser.objects.filter(email='stud1@a.in').exists())  # others untouched
            deletes = AuditLog.objects.filter(id__gt=mark, action='delete')
            self.assertEqual(deletes.filter(model_label='main_app.CustomUser').count(), 3)
            self.assertTrue(all(e.actor_email == 'admin@a.in' for e in deletes))
            self.assertTrue(AuditLog.objects.filter(id__gt=mark, model_label='bulk_import.ImportBatch', action='update').exists())
        done = self.refresh(batch)
        self.assertEqual((done.status, done.rolled_back_by_id), ('rolled_back', self.admin.pk))
        self.assertIn('already rolled back', ' '.join(services.rollback_blockers(done)))

    def test_it_cannot_be_rolled_back_twice(self):
        batch = self.import_students()
        self.rollback(batch)
        self.assertContains(self.rollback(batch), 'already rolled back')

    def test_each_kind_of_use_blocks_a_rollback(self):
        for label, make in (
            ('attendance', lambda s: AttendanceReport.objects.create(student=s, attendance_id=self.att1, status=True)),
            ('marks', lambda s: StudentResult.objects.create(student=s, offering_id=self.sub1, test=1, exam=1)),
            ('enrolled by hand', lambda s: Enrolment.objects.create(offering_id=self.sub2, student=s, source='manual')),
        ):
            batch = self.import_students(1)
            with tenant_context(self.college_a):
                make(Student.objects.get(roll_number='21CS001'))
            response = self.rollback(batch)
            self.assertContains(response, 'Cannot roll back')
            with tenant_context(self.college_a):
                self.assertTrue(Student.objects.filter(roll_number='21CS001').exists(), label)
                CustomUser.objects.filter(email='s1@a.in').delete()
                ImportBatch.objects.all().delete()

    def test_a_person_who_has_logged_in_blocks_a_rollback(self):
        batch = self.import_students(1)
        with tenant_context(self.college_a):
            from django.utils import timezone
            user = CustomUser.objects.get(email='s1@a.in')
            user.last_login = timezone.now()
            user.save()
        self.assertContains(self.rollback(batch), 'already logged in')

    def test_parent_links_block_a_students_rollback_until_the_parents_are_rolled_back(self):
        students = self.import_students(2)
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001'), parent_row('21CS002', primary='no')])
        self.assertContains(self.rollback(students), 'roll back the parents import first')
        with tenant_context(self.college_a):
            parents = ImportBatch.objects.get(kind='parents')
        self.assertContains(self.rollback(parents), 'rolled back')
        with tenant_context(self.college_a):
            self.assertEqual((Guardian.objects.count(), StudentGuardian.objects.count()), (0, 0))
        self.assertContains(self.rollback(students), 'rolled back')

    def test_a_guardian_shared_with_an_older_child_is_kept(self):
        self.import_students(2)
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS002', primary='no')])
        with tenant_context(self.college_a):
            second = ImportBatch.objects.filter(kind='parents').first()
        self.rollback(second)
        with tenant_context(self.college_a):
            self.assertEqual((Guardian.objects.count(), StudentGuardian.objects.count()), (1, 1))

    def test_a_recorded_consent_blocks_rolling_back_the_parents(self):
        self.import_students(1)
        self.run_import('parents', PARENT_HEADERS, [parent_row('21CS001')])
        with tenant_context(self.college_a):
            Guardian.objects.update(whatsapp_consent='yes')
            parents = ImportBatch.objects.get(kind='parents')
        self.assertContains(self.rollback(parents), 'consent answer')

    def test_a_batch_that_updated_records_cannot_be_rolled_back(self):
        self.import_students(1)
        self.run_import('students', STUDENT_HEADERS, [student_row(1, first_name='New')], update=True)
        with tenant_context(self.college_a):
            batch = ImportBatch.objects.first()
        self.assertContains(self.rollback(batch), 'updates cannot be undone')
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.get(email='s1@a.in').first_name, 'New')

    def test_other_modules_can_register_blockers_such_as_fees(self):
        batch = self.import_students(1)
        blocker = mock.Mock(return_value=['fees have been charged'])
        with mock.patch.object(services, 'ROLLBACK_BLOCKERS', [blocker]):
            self.assertContains(self.rollback(batch), 'fees have been charged')
        blocker.assert_called()
        with tenant_context(self.college_a):
            self.assertTrue(CustomUser.objects.filter(email='s1@a.in').exists())

    def test_staff_imports_are_blocked_by_teaching_assignments(self):
        from .helpers import STAFF_HEADERS
        self.run_import('staff', STAFF_HEADERS, [['E1', 'Meena', 'Iyer', 'm@a.in', 'F', '', 'CSE']])
        with tenant_context(self.college_a):
            batch = ImportBatch.objects.first()
            from main_app.models import Staff
            SubjectOffering.objects.filter(pk=self.sub1).update(faculty=Staff.objects.get(employee_id='E1'))
        self.assertContains(self.rollback(batch), 'teach subject offerings')

    def test_only_the_college_admin_can_roll_back(self):
        batch = self.import_students(1)
        for user in (self.staff1, self.stud1):
            self.assertEqual(self.client_admin.__class__ and self.as_user(user).post(f'/import/batch/{batch.pk}/rollback/').status_code, 403)
        self.assertEqual(self.refresh(batch).status, 'applied')

    def test_the_home_page_explains_why_a_batch_cannot_be_rolled_back(self):
        batch = self.import_students(1)
        with tenant_context(self.college_a):
            StudentResult.objects.create(student=Student.objects.get(roll_number='21CS001'), offering_id=self.sub1, test=1, exam=1)
        page = self.client_admin.get('/import/')
        self.assertContains(page, 'marks have been entered')
        self.assertEqual(self.refresh(batch).status, 'applied')


class PerformanceTests(ImportTestBase):
    def test_2000_students_validate_and_save_within_the_request_budget(self):
        rows = [student_row(n, roll_number=f'P{n:05d}', email=f'p{n}@a.in') for n in range(1, 2001)]
        start = time.monotonic()
        resp = self.preview('students', STUDENT_HEADERS, rows)
        validate_seconds = time.monotonic() - start
        self.assertTrue(resp.context['plan'].ok, resp.context['plan'].problems[:3])
        start = time.monotonic()
        self.confirm(resp)
        save_seconds = time.monotonic() - start
        print(f'\n[2000 rows] validate {validate_seconds:.1f}s, save {save_seconds:.1f}s')
        self.assertLess(validate_seconds, 15)
        self.assertLess(save_seconds, 60)
        self.assertEqual(self.count(ImportBatch), 1)
        with tenant_context(self.college_a):
            self.assertEqual(Student.objects.filter(roll_number__startswith='P').count(), 2000)
