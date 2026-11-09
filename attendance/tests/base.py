import datetime

from django.test import Client
from django_tenants.utils import tenant_context

from academics.models import Enrolment, SubjectOffering
from colleges.tests.base import EMAIL_BACKEND, HOST_A
from main_app.models import Attendance, AttendanceReport
from main_app.tests.world import WorldTestCase

TODAY = datetime.date.today()


def iso(day):
    return day.isoformat()


class AttendanceTestBase(WorldTestCase):
    """World: sub1 = Maths, staff1, section sec1 (stud1, stud2); sub2 = Physics, staff2, sec2 (stud3). Term runs to 2099."""

    def take(self, user, offering, states=None, day=None, period=1, key='k1', follow=False, **extra):
        data = {'date': iso(day or TODAY), 'period': period, 'key': key}
        for student_pk, status in (states or {}).items():
            data[f'st_{student_pk}'] = status
        client = user if isinstance(user, Client) else self.as_user(user, **extra)
        return client.post(f'/attendance/class/{offering}/', data, follow=follow)

    def reports(self, offering, day=None, period=1):
        with tenant_context(self.college_a):
            return {r.student_id: r.status for r in AttendanceReport.objects.filter(
                attendance__offering_id=offering, attendance__date=day or TODAY, attendance__period=period)}

    def classes(self, offering=None):
        with tenant_context(self.college_a):
            qs = Attendance.objects.all()
            return qs.filter(offering_id=offering).count() if offering else qs.count()

    def student_pk(self, user):
        return self.stud_ids[user.email]

    def add_history(self, student, offering, present, absent, on_duty=0, start=None):
        """Create past classes directly (fast): `present` present, `absent` absent, `on_duty` on duty for one student."""
        start = start or datetime.date(2020, 1, 1)  # far from the world's own dates, so histories never collide
        with tenant_context(self.college_a):
            existing = Attendance.objects.filter(offering_id=offering).count()
            statuses = ['present'] * present + ['absent'] * absent + ['on_duty'] * on_duty
            for i, status in enumerate(statuses):
                att = Attendance.objects.create(offering_id=offering, date=start + datetime.timedelta(days=existing + i))
                AttendanceReport.objects.create(student=student, attendance=att, status=status)

    def client_for(self, user):
        client = Client(HTTP_HOST=HOST_A)
        with tenant_context(self.college_a):
            client.force_login(user, backend=EMAIL_BACKEND)
        return client

    def enrol(self, student, offering):
        from academics import services
        with tenant_context(self.college_a):
            services.enrol(student, SubjectOffering.objects.get(pk=offering), Enrolment.MANUAL,
                           since=datetime.date(2025, 6, 1))
