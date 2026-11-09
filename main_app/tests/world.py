"""A small college with every role, used by the access-control tests.

Layout (all inside College A):
  admin               HoD
  staff1 / staff2     staff1 teaches Maths (course C1); staff2 teaches Physics (course C2)
  stud1, stud2        course C1  (stud2 is the "other student" whose data stud1 must not see)
  stud3               course C2
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
        from main_app.models import (
            Attendance,
            AttendanceReport,
            Course,
            FeedbackStudent,
            NotificationStudent,
            Session,
            StudentResult,
            Subject,
        )
        c = self.college_a
        self.admin = self.make_user(c, 'admin@a.in', '1', 'Ada', 'Admin')
        self.staff1 = self.make_user(c, 'staff1@a.in', '2', 'Sita', 'One')
        self.staff2 = self.make_user(c, 'staff2@a.in', '2', 'Raj', 'Two')
        self.stud1 = self.make_user(c, 'stud1@a.in', '3', 'Stu', 'One')
        self.stud2 = self.make_user(c, 'stud2@a.in', '3', 'Stu', 'Two')
        self.stud3 = self.make_user(c, 'stud3@a.in', '3', 'Stu', 'Three')
        with tenant_context(c), transaction.atomic():
            self.course1 = Course.objects.create(name='C1').pk
            self.course2 = Course.objects.create(name='C2').pk
            self.session = Session.objects.create(start_year=date(2025, 6, 1), end_year=date(2026, 5, 31)).pk
            for user, course in ((self.stud1, self.course1), (self.stud2, self.course1), (self.stud3, self.course2)):
                s = user.student
                s.course_id, s.session_id = course, self.session
                s.save()
            self.sub1 = Subject.objects.create(name='Maths', staff=self.staff1.staff, course_id=self.course1).pk
            self.sub2 = Subject.objects.create(name='Physics', staff=self.staff2.staff, course_id=self.course2).pk
            self.att1 = Attendance.objects.create(session_id=self.session, subject_id=self.sub1, date=date(2026, 1, 5)).pk
            self.att2 = Attendance.objects.create(session_id=self.session, subject_id=self.sub2, date=date(2026, 1, 6)).pk
            AttendanceReport.objects.create(student=self.stud1.student, attendance_id=self.att1, status=True)
            AttendanceReport.objects.create(student=self.stud2.student, attendance_id=self.att1, status=False)
            AttendanceReport.objects.create(student=self.stud3.student, attendance_id=self.att2, status=True)
            StudentResult.objects.create(student=self.stud1.student, subject_id=self.sub1, test=11, exam=22)
            StudentResult.objects.create(student=self.stud2.student, subject_id=self.sub1,
                                         test=STUD2_TEST, exam=STUD2_EXAM)
            StudentResult.objects.create(student=self.stud3.student, subject_id=self.sub2, test=33, exam=44)
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
                    for m in apps.get_app_config('main_app').get_models()}
            snap['results'] = sorted(
                __import__('main_app.models', fromlist=['StudentResult']).StudentResult.objects
                .values_list('pk', 'test', 'exam'))
        return snap
