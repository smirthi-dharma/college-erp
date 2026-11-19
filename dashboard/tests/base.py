"""A small college with exactly known numbers, shared by the dashboard tests.

Students (all in section 1 unless stated; subject 1 = Maths with staff1, subject 2 = Physics with staff2, section 2):
  stud1  Maths: held 11, attended 10 (90.9%)            -> OK
  stud2  Maths: held 8,  attended 5  (62.5%)            -> SHORTAGE
  stud3  Physics (section 2): held 4, attended 3 (75%)  -> WARNING
  stud4  no attendance at all                           -> no classes yet
  stud5  Maths: held 11, attended 8 (72.7%, condoned)   -> CONDONED
Today: one Maths class, stud1 present, stud2 absent, stud5 on duty -> 3 marks, 2 present-like (66.7%).
Sections with a subject this term: 1 (marked today), 2 (Physics, not marked), 3 (Maths again, not marked); section 4 has no subject.
Guardians: g1 yes (stud1 in C1, stud3 in C2), g2 no (stud2 and stud4, two children in C1: counted once), g3 unknown (stud1),
g4 yes (no child).
"""
import datetime
from decimal import Decimal

from django.utils import timezone
from django_tenants.utils import tenant_context

from attendance.tests.base import AttendanceTestBase


class GoldenCollege(AttendanceTestBase):
    def setUp(self):
        super().setUp()
        from academics.models import Section, Subject, SubjectOffering
        from attendance import services as attendance
        from main_app.models import Attendance, AttendanceReport, Guardian, StudentGuardian
        self.stud4 = self.make_user(self.college_a, 'stud4@a.in', '3', 'Fourth', 'Pupil')
        self.stud5 = self.make_user(self.college_a, 'stud5@a.in', '3', 'Fifth', 'Pupil')
        self.principal = self.make_user(self.college_a, 'principal@a.in', '5', 'Prin', 'Cipal')
        self.today = timezone.localdate()
        with tenant_context(self.college_a):
            for user in (self.stud4, self.stud5):
                student = user.student
                student.section_id = self.sec1  # joining the section enrols them in its core subjects
                student.save()
            self.sec3 = Section.objects.create(batch_id=self.batch1, name='B').pk
            self.sec4 = Section.objects.create(batch_id=self.batch1, name='C').pk  # no subject: never listed
            self.sub3 = SubjectOffering.objects.create(subject=Subject.objects.get(pk=self.subject1), section_id=self.sec3,
                                                       term_id=self.term, faculty=self.staff1.staff).pk
        self.add_history(self.stud1.student, self.sub1, present=8, absent=1)
        self.add_history(self.stud2.student, self.sub1, present=5, absent=1)
        self.add_history(self.stud3.student, self.sub2, present=2, absent=1)
        self.add_history(self.stud5.student, self.sub1, present=7, absent=3)
        with tenant_context(self.college_a):
            today_class = Attendance.objects.create(offering_id=self.sub1, date=self.today, period=1)
            for user, status in ((self.stud1, 'present'), (self.stud2, 'absent'), (self.stud5, 'on_duty')):
                AttendanceReport.objects.create(student=user.student, attendance=today_class, status=status)
            from academics.models import SubjectOffering as Offering
            attendance.condone(self.admin, self.stud5.student, Offering.objects.get(pk=self.sub1), 'medical certificate')
            g1 = Guardian.objects.create(full_name='G One', phone='+919000000001', whatsapp_consent='yes')
            g2 = Guardian.objects.create(full_name='G Two', phone='+919000000002', whatsapp_consent='no')
            g3 = Guardian.objects.create(full_name='G Three', phone='+919000000003', whatsapp_consent='unknown')
            Guardian.objects.create(full_name='G Four', phone='+919000000004', whatsapp_consent='yes')
            for student, guardian in ((self.stud1, g1), (self.stud3, g1), (self.stud2, g2), (self.stud4, g2), (self.stud1, g3)):  # g2 has two children in C1
                StudentGuardian.objects.create(student=student.student, guardian=guardian, relation='father')

    def tiles(self, term=None):
        from academics.models import AcademicTerm
        from dashboard import services
        with tenant_context(self.college_a):
            return services.tiles(term or AcademicTerm.objects.get(pk=self.term))

    def snapshot_all(self):
        """Row counts of every tenant model: any change shows up."""
        from django.apps import apps
        with tenant_context(self.college_a):
            return {m._meta.label: m.objects.count() for app in ('main_app', 'academics', 'attendance', 'messaging', 'audit_trail')
                    for m in apps.get_app_config(app).get_models() if m._meta.label not in ('audit_trail.AuditLog',)}


D = Decimal
__all__ = ['GoldenCollege', 'D', 'datetime']
