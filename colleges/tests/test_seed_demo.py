"""seed_demo: demo colleges only, idempotent, never overwrites edits, never touches another college."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django_tenants.utils import tenant_context

from .base import TwoCollegeTestCase, _make_college

SCHEMA = 'demo_seedtest'


def seed(schema=SCHEMA):
    out = StringIO()
    call_command('seed_demo', schema=schema, stdout=out)
    return out.getvalue()


class SeedDemoTests(TwoCollegeTestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = _make_college(SCHEMA, 'Seed test college', 'seedtest.testserver')  # committed, before the class transaction
        super().setUpClass()

    def snapshot(self, college):
        from academics.models import Enrolment, SubjectOffering
        from main_app.models import AttendanceReport, CustomUser, Guardian, Student, StudentGuardian
        with tenant_context(college):
            return {m.__name__: m.objects.count() for m in
                    (CustomUser, Student, Guardian, StudentGuardian, Enrolment, SubjectOffering, AttendanceReport)}

    def test_refuses_a_college_whose_schema_is_not_a_demo(self):
        before = self.snapshot(self.college_a)
        with self.assertRaises(CommandError):
            seed('college_a')
        self.assertEqual(self.snapshot(self.college_a), before)

    def test_refuses_an_unknown_demo_schema(self):
        with self.assertRaises(CommandError):
            seed('demo_does_not_exist')

    def test_creates_the_sample_college(self):
        from academics.models import Enrolment, SubjectOffering
        from main_app.models import Attendance, Guardian, Student, StudentGuardian
        seed()
        with tenant_context(self.demo):
            self.assertEqual(Student.objects.filter(section__isnull=False).count(), 10)
            self.assertEqual(Guardian.objects.count(), 9)
            self.assertEqual(StudentGuardian.objects.count(), 10)
            self.assertEqual(SubjectOffering.objects.count(), 2)
            self.assertEqual(Enrolment.objects.filter(status=Enrolment.ACTIVE).count(), 20)
            self.assertEqual(Attendance.objects.count(), 20)  # 2 subjects x 10 weekdays
            # the two siblings share one guardian
            kumar = Student.objects.filter(admin__last_name='Kumar')
            self.assertEqual(kumar.count(), 2)
            self.assertEqual(StudentGuardian.objects.filter(student__in=kumar).values('guardian').distinct().count(), 1)
            consents = set(Guardian.objects.values_list('whatsapp_consent', flat=True))
            self.assertEqual(consents, {'yes', 'no', 'unknown'})
            self.assertTrue(all(g.phone.startswith('+91900000') for g in Guardian.objects.all()))  # obviously fake

    def test_demo_consent_comes_with_evidence(self):
        from main_app.models import ConsentRecord, Guardian
        seed()
        with tenant_context(self.demo):
            for guardian in Guardian.objects.exclude(whatsapp_consent='unknown'):
                record = guardian.consent_records.get()
                self.assertEqual((record.method, record.value), ('paper_form', guardian.whatsapp_consent))
                self.assertEqual(record.recorded_by_email, 'admin@%s.in' % SCHEMA)
            self.assertEqual(ConsentRecord.objects.count(), 7)  # 9 guardians, 2 of them still "unknown"
        seed()
        with tenant_context(self.demo):
            self.assertEqual(ConsentRecord.objects.count(), 7)  # a re-run adds no evidence

    def test_every_role_has_something_to_see(self):
        from main_app.models import CustomUser
        from student360.services import visible_students
        seed()
        with tenant_context(self.demo):
            teacher = CustomUser.objects.get(email='staff@%s.in' % SCHEMA)
            self.assertEqual(visible_students(teacher).count(), 10)

    def test_one_student_has_a_shortage_for_the_dashboard(self):
        from attendance.services import shortage_students
        seed()
        with tenant_context(self.demo):
            names = {student.admin.first_name for student, _entry in shortage_students()}
            self.assertIn('Karthik', names)

    def test_running_twice_changes_nothing(self):
        seed()
        first = self.snapshot(self.demo)
        second_output = seed()
        self.assertEqual(self.snapshot(self.demo), first)
        self.assertIn('0 classes marked', second_output)

    def test_a_rerun_keeps_edits_made_through_the_app(self):
        from main_app.models import AttendanceReport, Guardian
        seed()
        with tenant_context(self.demo):
            Guardian.objects.filter(phone='+919000000001').update(whatsapp_consent='no')
            report = AttendanceReport.objects.filter(status='present').first()
            report.status = 'absent'
            report.save()
        seed()
        with tenant_context(self.demo):
            self.assertEqual(Guardian.objects.get(phone='+919000000001').whatsapp_consent, 'no')
            report.refresh_from_db()
            self.assertEqual(report.status, 'absent')

    def test_other_colleges_get_nothing(self):
        before_a, before_b = self.snapshot(self.college_a), self.snapshot(self.college_b)
        seed()
        self.assertEqual(self.snapshot(self.college_a), before_a)
        self.assertEqual(self.snapshot(self.college_b), before_b)
        connection.set_schema_to_public()
