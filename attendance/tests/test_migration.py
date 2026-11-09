"""Check 16: a legacy dataset (booleans, duplicate classes, duplicate reports) converts without losing any count."""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import tenant_context

from colleges.tests.base import TwoCollegeTestCase

BEFORE = [('main_app', '0009_guardian_studentguardian_customuser_phone_and_more')]
AFTER = [('main_app', '0012_attendance_status_and_constraints')]


class AttendanceMigrationTests(TwoCollegeTestCase):
    def migrate(self, targets):
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_legacy_data_is_converted_without_losing_a_mark(self):
        with tenant_context(self.college_a):
            old = self.migrate(BEFORE)
            User, Student, Staff = (old.get_model('main_app', n) for n in ('CustomUser', 'Student', 'Staff'))
            Attendance, Report = old.get_model('main_app', 'Attendance'), old.get_model('main_app', 'AttendanceReport')
            Dept, Prog, Reg, Batch, Sec, Subj, Term, Off = (old.get_model('academics', n) for n in (
                'Department', 'Programme', 'Regulation', 'Batch', 'Section', 'Subject', 'AcademicTerm', 'SubjectOffering'))
            d = Dept.objects.create(name='D', code='D')
            p = Prog.objects.create(department=d, name='P', code='P', duration_semesters=8)
            r = Reg.objects.create(programme=p, name='R', effective_from=datetime.date(2025, 1, 1))
            sec = Sec.objects.create(batch=Batch.objects.create(programme=p, regulation=r, start_year=2025), name='A')
            subj = Subj.objects.create(programme=p, regulation=r, semester=1, code='S1', name='S', credits=1)
            term = Term.objects.create(name='T', start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2099, 1, 1))
            teacher = Staff.objects.create(admin=User.objects.create(email='t@a.in', user_type='2', password='x'))  # pragma: allowlist secret
            off = Off.objects.create(subject=subj, section=sec, term=term, faculty=teacher)
            stu = [Student.objects.create(admin=User.objects.create(email=f's{i}@a.in', user_type='3', password='x')) for i in range(2)]  # pragma: allowlist secret
            day = datetime.date(2026, 1, 5)
            first = Attendance.objects.create(offering=off, date=day)
            second = Attendance.objects.create(offering=off, date=day)             # the old code allowed a duplicate class
            other = Attendance.objects.create(offering=off, date=day + datetime.timedelta(days=1))
            Report.objects.create(student=stu[0], attendance=first, status=True)
            Report.objects.create(student=stu[1], attendance=first, status=False)
            Report.objects.create(student=stu[0], attendance=second, status=False)
            Report.objects.create(student=stu[0], attendance=other, status=True)
            Report.objects.create(student=stu[0], attendance=other, status=False)  # duplicate report: the newest wins
            ids = {'first': first.pk, 'second': second.pk, 'other': other.pk, 's0': stu[0].pk, 's1': stu[1].pk}

            new = self.migrate(AFTER)
            Attendance, Report = new.get_model('main_app', 'Attendance'), new.get_model('main_app', 'AttendanceReport')
            self.assertEqual(Attendance.objects.count(), 3)                                    # no class lost
            self.assertEqual(sorted(Attendance.objects.filter(date=day).values_list('period', flat=True)), [1, 2])
            self.assertEqual(Attendance.objects.get(pk=ids['first']).period, 1)
            self.assertEqual(Attendance.objects.get(pk=ids['second']).period, 2)
            status = {(r.attendance_id, r.student_id): r.status for r in Report.objects.all()}
            self.assertEqual(status[(ids['first'], ids['s0'])], 'present')                     # True -> present
            self.assertEqual(status[(ids['first'], ids['s1'])], 'absent')                      # False -> absent
            self.assertEqual(status[(ids['second'], ids['s0'])], 'absent')
            self.assertEqual(status[(ids['other'], ids['s0'])], 'absent')                      # the newest duplicate kept
            self.assertEqual(Report.objects.count(), 4)
            self.assertFalse(any(r.needs_review for r in Report.objects.all()))
            self.assertEqual(sum(1 for v in status.values() if v == 'present'), 1)

    def test_no_pending_model_changes(self):
        from django.core.management import call_command
        call_command('makemigrations', check=True, dry_run=True, verbosity=0)

    def test_an_empty_college_migrates_cleanly(self):
        with tenant_context(self.college_b):
            self.migrate(BEFORE)
            new = self.migrate(AFTER)
            self.assertEqual(new.get_model('main_app', 'Attendance').objects.count(), 0)
