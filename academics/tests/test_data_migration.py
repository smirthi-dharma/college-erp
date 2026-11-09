"""Acceptance checks 5 and 6: old Course/Session/Subject data survives the move to the new structure."""
from datetime import date

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import tenant_context

from colleges.tests.base import TwoCollegeTestCase

BEFORE = [('main_app', '0004_drop_library_models')]
AFTER = [('main_app', '0007_remove_legacy_academics')]


class LegacyDataMigrationTests(TwoCollegeTestCase):
    def migrate(self, targets):
        with connection.cursor() as cursor:  # settle deferred FK checks so ALTER TABLE is allowed
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_no_pending_model_changes(self):
        call_command('makemigrations', check=True, dry_run=True, verbosity=0)

    def test_legacy_rows_are_converted_without_losing_attendance_or_marks(self):
        with tenant_context(self.college_a):
            old = self.migrate(BEFORE)
            User = old.get_model('main_app', 'CustomUser')
            Course, Session, Subject = (old.get_model('main_app', n) for n in ('Course', 'Session', 'Subject'))
            Student, Staff = old.get_model('main_app', 'Student'), old.get_model('main_app', 'Staff')
            Attendance, Report, Result = (old.get_model('main_app', n) for n in ('Attendance', 'AttendanceReport', 'StudentResult'))

            def user(email, kind):
                return User.objects.create(email=email, user_type=kind, password='x')  # pragma: allowlist secret

            course = Course.objects.create(name='B.Sc Physics')
            s2024 = Session.objects.create(start_year=date(2024, 6, 1), end_year=date(2025, 5, 31))
            s2025 = Session.objects.create(start_year=date(2025, 6, 1), end_year=date(2026, 5, 31))
            teacher = Staff.objects.create(admin=user('t@a.in', '2'), course=course)
            maths = Subject.objects.create(name='Maths', staff=teacher, course=course)
            alice = Student.objects.create(admin=user('al@a.in', '3'), course=course, session=s2024)
            bob = Student.objects.create(admin=user('bo@a.in', '3'), course=course, session=s2025)
            att_a = Attendance.objects.create(session=s2024, subject=maths, date=date(2025, 1, 6))
            att_b = Attendance.objects.create(session=s2025, subject=maths, date=date(2026, 1, 6))
            Report.objects.create(student=alice, attendance=att_a, status=True)
            Report.objects.create(student=bob, attendance=att_b, status=False)
            Result.objects.create(student=alice, subject=maths, test=31, exam=52)
            Result.objects.create(student=bob, subject=maths, test=22, exam=44)
            ids = {'alice': alice.pk, 'bob': bob.pk, 'teacher': teacher.pk}

            new = self.migrate(AFTER)
            Student, Staff = new.get_model('main_app', 'Student'), new.get_model('main_app', 'Staff')
            Attendance, Report, Result = (new.get_model('main_app', n) for n in ('Attendance', 'AttendanceReport', 'StudentResult'))
            Programme, Department = new.get_model('academics', 'Programme'), new.get_model('academics', 'Department')
            Enrolment, Offering = new.get_model('academics', 'Enrolment'), new.get_model('academics', 'SubjectOffering')
            Batch = new.get_model('academics', 'Batch')

            self.assertEqual(Programme.objects.get().name, 'B.Sc Physics')
            self.assertEqual(sorted(Batch.objects.values_list('start_year', flat=True)), [2024, 2025])
            self.assertEqual(Staff.objects.get(pk=ids['teacher']).department.name, 'B.Sc Physics')
            alice_new, bob_new = Student.objects.get(pk=ids['alice']), Student.objects.get(pk=ids['bob'])
            self.assertEqual((alice_new.section.batch.start_year, bob_new.section.batch.start_year), (2024, 2025))
            # one offering per section, taught by the old subject's teacher
            self.assertEqual(Offering.objects.count(), 2)
            self.assertEqual({o.faculty_id for o in Offering.objects.all()}, {ids['teacher']})
            # attendance and marks are attached to the right student and offering
            self.assertEqual(Attendance.objects.count(), 2)
            self.assertEqual(Report.objects.get(student_id=ids['alice']).attendance.offering.section_id, alice_new.section_id)
            self.assertEqual(Report.objects.get(student_id=ids['bob']).attendance.offering.section_id, bob_new.section_id)
            self.assertEqual(Report.objects.get(student_id=ids['alice']).status, True)
            r_alice = Result.objects.get(student_id=ids['alice'])
            self.assertEqual((r_alice.test, r_alice.exam, r_alice.offering.section_id), (31, 52, alice_new.section_id))
            r_bob = Result.objects.get(student_id=ids['bob'])
            self.assertEqual((r_bob.test, r_bob.exam, r_bob.offering.section_id), (22, 44, bob_new.section_id))
            # both students are enrolled where their section's core offering is
            self.assertEqual(Enrolment.objects.filter(student_id=ids['alice'], offering=r_alice.offering).count(), 1)
            self.assertEqual(Enrolment.objects.filter(student_id=ids['bob'], offering=r_bob.offering).count(), 1)
            self.assertEqual(Department.objects.count(), 1)

    def test_migration_on_an_empty_college_is_a_no_op(self):
        with tenant_context(self.college_b):
            self.migrate(BEFORE)
            new = self.migrate(AFTER)
            self.assertEqual(new.get_model('academics', 'Programme').objects.count(), 0)
