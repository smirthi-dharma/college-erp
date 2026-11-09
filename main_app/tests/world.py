"""A small college with every role, used by the access-control tests.

Layout (all inside College A):
  admin               HoD
  staff1 / staff2     staff1 teaches Maths (offering sub1, section sec1); staff2 teaches Physics (offering sub2, section sec2)
  stud1, stud2        section sec1  (stud2 is the "other student" whose data stud1 must not see)
  stud3               section sec2
  (self.sub1 / self.sub2 are SubjectOffering ids; self.subject1 / subject2 are the catalogue Subject ids)
Marks are distinct numbers so a leak is detectable in a page body.
"""
from datetime import date

from django.db import transaction
from django_tenants.utils import tenant_context

from colleges.tests.base import HOST_A, TwoCollegeTestCase

SECRET_NOTE = 'SECRET-NOTE-FOR-STUD2'  # pragma: allowlist secret
STUD2_TEST, STUD2_EXAM = 777, 888


class WorldTestCase(TwoCollegeTestCase):
    def setUp(self):
        super().setUp()
        from academics.models import (
            AcademicTerm,
            Batch,
            Department,
            Programme,
            Regulation,
            Section,
            Subject,
            SubjectOffering,
        )
        from main_app.models import (
            Attendance,
            AttendanceReport,
            FeedbackStudent,
            NotificationStudent,
            StudentResult,
        )
        c = self.college_a
        self.admin = self.make_user(c, 'admin@a.in', '1', 'Ada', 'Admin')
        self.staff1 = self.make_user(c, 'staff1@a.in', '2', 'Sita', 'One')
        self.staff2 = self.make_user(c, 'staff2@a.in', '2', 'Raj', 'Two')
        self.stud1 = self.make_user(c, 'stud1@a.in', '3', 'Stu', 'One')
        self.stud2 = self.make_user(c, 'stud2@a.in', '3', 'Stu', 'Two')
        self.stud3 = self.make_user(c, 'stud3@a.in', '3', 'Stu', 'Three')
        with tenant_context(c), transaction.atomic():
            self.dept1 = Department.objects.create(name='CSE', code='CSE').pk
            self.dept2 = Department.objects.create(name='ECE', code='ECE').pk
            self.dept_spare = Department.objects.create(name='Spare', code='SPARE').pk
            progs, regs, batches, secs = [], [], [], []
            for dept, code in ((self.dept1, 'C1'), (self.dept2, 'C2')):
                prog = Programme.objects.create(department_id=dept, name=code, code=code, duration_semesters=8)
                reg = Regulation.objects.create(programme=prog, name='R2025', effective_from=date(2025, 6, 1))
                batch = Batch.objects.create(programme=prog, regulation=reg, start_year=2025)
                progs.append(prog), regs.append(reg), batches.append(batch)
                secs.append(Section.objects.create(batch=batch, name='A'))
            self.prog1, self.prog2 = progs[0].pk, progs[1].pk
            self.reg1, self.reg2 = regs[0].pk, regs[1].pk
            self.batch1, self.batch2 = batches[0].pk, batches[1].pk
            self.sec1, self.sec2 = secs[0].pk, secs[1].pk
            self.term = AcademicTerm.objects.create(name='2099 ODD', start_date=date(2025, 6, 1), end_date=date(2099, 12, 31)).pk
            for user, sec in ((self.stud1, self.sec1), (self.stud2, self.sec1), (self.stud3, self.sec2)):
                s = user.student
                s.section_id = sec
                s.save()
            cat1 = Subject.objects.create(programme=progs[0], regulation=regs[0], semester=1, code='MA101', name='Maths', credits=4)
            cat2 = Subject.objects.create(programme=progs[1], regulation=regs[1], semester=1, code='PH101', name='Physics', credits=3)
            self.subject1, self.subject2 = cat1.pk, cat2.pk
            # created after the students joined their sections, so the core offerings auto-enrol them
            self.sub1 = SubjectOffering.objects.create(subject=cat1, section=secs[0], term_id=self.term, faculty=self.staff1.staff).pk
            self.sub2 = SubjectOffering.objects.create(subject=cat2, section=secs[1], term_id=self.term, faculty=self.staff2.staff).pk
            self.att1 = Attendance.objects.create(offering_id=self.sub1, date=date(2026, 1, 5)).pk
            self.att2 = Attendance.objects.create(offering_id=self.sub2, date=date(2026, 1, 6)).pk
            AttendanceReport.objects.create(student=self.stud1.student, attendance_id=self.att1, status=True)
            AttendanceReport.objects.create(student=self.stud2.student, attendance_id=self.att1, status=False)
            AttendanceReport.objects.create(student=self.stud3.student, attendance_id=self.att2, status=True)
            StudentResult.objects.create(student=self.stud1.student, offering_id=self.sub1, test=11, exam=22)
            StudentResult.objects.create(student=self.stud2.student, offering_id=self.sub1,
                                         test=STUD2_TEST, exam=STUD2_EXAM)
            StudentResult.objects.create(student=self.stud3.student, offering_id=self.sub2, test=33, exam=44)
            NotificationStudent.objects.create(student=self.stud2.student, message=SECRET_NOTE)
            FeedbackStudent.objects.create(student=self.stud2.student, feedback='fb', reply='')
            self.stud_ids = {u.email: u.student.pk for u in (self.stud1, self.stud2, self.stud3)}
            self.staff_ids = {u.email: u.staff.pk for u in (self.staff1, self.staff2)}

    def as_user(self, user, **kwargs):
        from django.test import Client
        client = Client(HTTP_HOST=HOST_A, **kwargs)
        from colleges.tests.base import EMAIL_BACKEND
        with tenant_context(self.college_a):
            client.force_login(user, backend=EMAIL_BACKEND)
        return client

    def snapshot(self):
        """Row counts and the result values for every tenant model: detects any state change."""
        from django.apps import apps
        with tenant_context(self.college_a):
            snap = {m._meta.label: m.objects.count()
                    for app in ('main_app', 'academics') for m in apps.get_app_config(app).get_models()}
            snap['results'] = sorted(
                __import__('main_app.models', fromlist=['StudentResult']).StudentResult.objects
                .values_list('pk', 'test', 'exam'))
        return snap
