"""Checks 1-8, 11, 13-15 and A4, A5 (taking attendance)."""
import datetime
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import tenant_context

from academics import services as acad
from academics.models import Enrolment
from attendance import services
from attendance.models import AttendanceRule, SubstituteAssignment
from audit_trail.models import AuditLog
from main_app.models import Attendance, AttendanceReport, CustomUser, Student

from .base import TODAY, AttendanceTestBase, iso


class SpeedTests(AttendanceTestBase):
    def test_everyone_starts_present_and_an_all_present_class_needs_no_typing(self):
        page = self.as_user(self.staff1).get(f'/attendance/class/{self.sub1}/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertEqual(html.count('<option value="present" selected>'), 2)  # stud1, stud2
        self.assertNotIn('<option value="absent" selected>', html)
        resp = self.take(self.staff1, self.sub1)  # no st_ fields at all: the 2-tap path
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(set(self.reports(self.sub1).values()), {'present'})
        self.assertEqual(len(self.reports(self.sub1)), 2)

    def test_each_absentee_is_one_field(self):
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'absent'})
        self.assertEqual(sorted(self.reports(self.sub1).values()), ['absent', 'present'])

    def make_class_of(self, n):
        with tenant_context(self.college_a):
            from academics.models import SubjectOffering
            offering = SubjectOffering.objects.get(pk=self.sub1)
            for i in range(n):
                user = CustomUser.objects.create_user(f'bulk{i}@a.in', None, first_name='B', last_name=f'S{i}', user_type='3')
                student = Student.objects.get(admin=user)
                student.roll_number = f'B{i:04d}'
                student.save()
                acad.enrol(student, offering, Enrolment.MANUAL, since=datetime.date(2025, 6, 1))

    def test_the_class_page_uses_a_constant_number_of_queries_and_stays_small(self):
        client = self.as_user(self.staff1)
        with CaptureQueriesContext(connection) as small:
            client.get(f'/attendance/class/{self.sub1}/')
        self.make_class_of(148)
        with CaptureQueriesContext(connection) as large:
            page = client.get(f'/attendance/class/{self.sub1}/')
        real = lambda ctx: [q for q in ctx.captured_queries if not q['sql'].startswith('SET search_path')]  # noqa: E731
        self.assertLessEqual(len(real(large)), 14)  # session, user, class, roster, reports and the rest: fixed
        self.assertLessEqual(len(real(large)) - len(real(small)), 1)  # does not grow with the class size
        self.assertEqual(page.content.decode().count('class="att-row"'), 150)
        self.assertLess(len(page.content), 100_000)
        body = page.content.decode().split('id="attform"')[1].split('</script>')[0]  # this page's own markup and script
        self.assertNotIn('$.ajax', body)
        self.assertNotIn('$(', body)

    def test_submitting_a_class_of_150_is_fast_and_does_no_per_student_reads(self):
        import time
        self.make_class_of(148)
        client = self.as_user(self.staff1)
        with CaptureQueriesContext(connection) as ctx:
            start = time.monotonic()
            resp = self.take(client, self.sub1)
            seconds = time.monotonic() - start
        self.assertEqual(resp.status_code, 302)
        self.assertLess(seconds, 5)  # CI budget; locally well under 2 s
        selects = [q for q in ctx.captured_queries if q['sql'].lstrip().upper().startswith('SELECT')]
        self.assertLess(len(selects), 40, 'reads must not grow with the class size')
        print(f'\n[150 students] submit {seconds:.2f}s, {len(ctx)} queries ({len(selects)} reads)')


class CorrectnessTests(AttendanceTestBase):
    def test_a_class_is_saved_once_and_resubmitting_updates_it(self):
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'absent'})
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'present'}, key='other')
        self.assertEqual(self.classes(self.sub1), 2)  # the world's own class (2026-01-05) + today's one
        self.assertEqual(self.reports(self.sub1)[self.student_pk(self.stud1)], 'present')

    def test_the_database_itself_refuses_duplicates(self):
        self.take(self.staff1, self.sub1)
        with tenant_context(self.college_a):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Attendance.objects.create(offering_id=self.sub1, date=TODAY, period=1)
            att = Attendance.objects.get(offering_id=self.sub1, date=TODAY)
            with self.assertRaises(IntegrityError), transaction.atomic():
                AttendanceReport.objects.create(student=self.stud1.student, attendance=att, status='absent')

    def test_two_periods_of_one_offering_on_a_day_are_two_classes(self):
        self.take(self.staff1, self.sub1, period=1)
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud2): 'absent'}, period=2, key='k2')
        self.assertEqual(self.reports(self.sub1, period=1)[self.student_pk(self.stud2)], 'present')
        self.assertEqual(self.reports(self.sub1, period=2)[self.student_pk(self.stud2)], 'absent')

    def test_the_class_list_is_the_students_enrolled_on_that_date(self):
        yesterday = TODAY - datetime.timedelta(days=1)
        with tenant_context(self.college_a):
            from academics.models import SubjectOffering
            offering = SubjectOffering.objects.get(pk=self.sub1)
            Enrolment.objects.filter(offering=offering, student=self.stud2.student).update(withdrawn_on=yesterday, status='withdrawn')
            self.enrol(self.stud3.student, self.sub1)                 # arrear student from another section
            late = Enrolment.objects.get(offering=offering, student=self.stud3.student)
            late.enrolled_on = TODAY
            late.save()
            on = lambda day: sorted(s.admin.email for s in services.roster(offering, day))  # noqa: E731
            self.assertEqual(on(TODAY), ['stud1@a.in', 'stud3@a.in'])           # withdrawn stud2 gone, arrear stud3 in
            self.assertEqual(on(yesterday), ['stud1@a.in'])                      # stud2 left "on or before" yesterday; stud3 not yet in
            self.assertEqual(on(yesterday - datetime.timedelta(days=1)), ['stud1@a.in', 'stud2@a.in'])

    def test_students_not_on_the_list_are_refused_and_nothing_is_saved(self):
        before = self.classes()
        resp = self.take(self.staff1, self.sub1, {self.student_pk(self.stud3): 'absent'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.classes(), before)

    def test_unknown_statuses_are_refused(self):
        resp = self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'maybe'})
        self.assertEqual(resp.status_code, 200)  # page shown again with the message
        self.assertEqual(self.classes(self.sub1), 1)

    def test_future_dates_and_dates_outside_the_term_are_refused_and_taps_are_kept(self):
        tomorrow = TODAY + datetime.timedelta(days=1)
        resp = self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'absent'}, day=tomorrow)
        self.assertContains(resp, 'future date')
        self.assertContains(resp, 'taps are kept')
        self.assertContains(resp, '<option value="absent" selected>')
        resp = self.take(self.admin, self.sub1, day=datetime.date(2020, 1, 1))
        self.assertContains(resp, 'outside the term')
        self.assertEqual(self.classes(self.sub1), 1)  # only the world's own class

    def test_the_teacher_edit_window_then_only_the_college_admin(self):
        old = TODAY - datetime.timedelta(days=10)
        inside = TODAY - datetime.timedelta(days=2)
        self.assertEqual(self.take(self.staff1, self.sub1, day=inside).status_code, 302)
        self.assertEqual(self.take(self.staff1, self.sub1, day=old, key='o').status_code, 403)
        self.assertEqual(self.take(self.admin, self.sub1, day=old, key='a').status_code, 302)
        with tenant_context(self.college_a):  # the window is a setting
            reg = Enrolment.objects.get(offering_id=self.sub1, student=self.stud1.student).offering.section.batch.regulation
            AttendanceRule.objects.create(regulation=reg, edit_window_days=30)
        self.assertEqual(self.take(self.staff1, self.sub1, day=old - datetime.timedelta(days=5), key='b').status_code, 302)
        self.assertEqual(self.take(self.staff1, self.sub1, day=old - datetime.timedelta(days=40), key='c').status_code, 403)

    def test_on_duty_counts_as_present_by_default_and_not_when_the_rule_says_so(self):
        stud = self.stud1.student
        self.add_history(stud, self.sub1, present=7, absent=0, on_duty=3)
        with tenant_context(self.college_a):
            entry = services.summarize([Student.objects.get(pk=stud.pk)])[stud.pk].entries[0]
            self.assertEqual((entry.held, entry.attended), (11, 11))  # 10 added + the world's own class
            reg = Enrolment.objects.get(offering_id=self.sub1, student=stud).offering.section.batch.regulation
            AttendanceRule.objects.create(regulation=reg, on_duty_counts_as_present=False)
            entry = services.summarize([Student.objects.get(pk=stud.pk)])[stud.pk].entries[0]
            self.assertEqual(entry.attended, 8)  # on-duty no longer counted


class PermissionAndAuditTests(AttendanceTestBase):
    def test_only_the_offerings_teacher_and_the_college_admin_can_take_a_class(self):
        head = self.make_user(self.college_a, 'head@a.in', '4', 'H', 'D')
        before = self.classes()
        for user in (self.staff2, self.stud1, head):
            self.assertIn(self.take(user, self.sub1).status_code, (302, 403))
            self.assertIn(self.as_user(user).get(f'/attendance/class/{self.sub1}/').status_code, (302, 403))
        self.assertEqual(self.classes(), before)
        self.assertEqual(self.take(self.admin, self.sub1).status_code, 302)

    def test_students_and_the_head_of_department_get_403_on_the_staff_pages(self):
        head = self.make_user(self.college_a, 'head@a.in', '4', 'H', 'D')
        for user in (self.stud1, head):
            client = self.as_user(user)
            for url in ('/attendance/', f'/attendance/class/{self.sub1}/', '/attendance/shortage/', '/attendance/on-duty/',
                        '/attendance/condonation/', '/attendance/substitutes/', '/attendance/rules/'):
                self.assertEqual(client.get(url).status_code, 403, (user.email, url))

    def test_admin_pages_are_403_for_teachers(self):
        client = self.as_user(self.staff1)
        for url in ('/attendance/shortage/', '/attendance/on-duty/', '/attendance/condonation/', '/attendance/substitutes/', '/attendance/rules/'):
            self.assertEqual(client.get(url).status_code, 403, url)

    def test_a_student_sees_only_their_own_figures(self):
        self.add_history(self.stud1.student, self.sub1, present=3, absent=1)
        self.add_history(self.stud2.student, self.sub1, present=0, absent=1)
        page = self.as_user(self.stud1).get('/attendance/mine/')
        self.assertContains(page, 'Maths')
        self.assertContains(page, '4')
        self.assertEqual(self.as_user(self.staff1).get('/attendance/mine/').status_code, 403)
        # student 2's single absence is not in student 1's table: held is 5 (3+1 plus the world's class), not 6
        self.assertContains(page, '<td>5</td>')

    def last_audit_id(self):
        with tenant_context(self.college_a):
            return AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0

    def audit_since(self, mark):
        with tenant_context(self.college_a):
            return [e for e in AuditLog.objects.filter(id__gt=mark) if e.model_label != 'main_app.CustomUser']  # skip login events

    def test_every_change_is_audited_and_a_no_change_resubmit_writes_nothing(self):
        mark = self.last_audit_id()
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'absent'})
        first = self.audit_since(mark)
        self.assertEqual(sorted(e.model_label for e in first), ['main_app.Attendance'] + ['main_app.AttendanceReport'] * 2)
        self.assertTrue(all(e.actor_email == 'staff1@a.in' for e in first))
        mark2 = self.last_audit_id()
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'absent'}, key='again')
        self.assertEqual(self.audit_since(mark2), [])
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'present'}, key='edit')
        self.assertEqual([(e.model_label, e.action) for e in self.audit_since(mark2)], [('main_app.AttendanceReport', 'update')])

    def test_college_b_sees_nothing_of_college_a(self):
        self.take(self.staff1, self.sub1)
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'B', 'Admin')
        from colleges.tests.base import EMAIL_BACKEND, HOST_B
        client = Client(HTTP_HOST=HOST_B)
        with tenant_context(self.college_b):
            client.force_login(admin_b, backend=EMAIL_BACKEND)
            self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(client.get(f'/attendance/class/{self.sub1}/').status_code, 404)
        self.assertNotContains(client.get('/attendance/shortage/'), 'Maths')

    def test_posts_need_csrf_and_gets_change_nothing(self):
        csrf = Client(HTTP_HOST='a.testserver', enforce_csrf_checks=True)
        with tenant_context(self.college_a):
            csrf.force_login(self.staff1, backend='main_app.EmailBackend.EmailBackend')
        self.assertEqual(csrf.post(f'/attendance/class/{self.sub1}/', {'date': iso(TODAY)}).status_code, 403)
        before = self.snapshot()
        admin = self.as_user(self.admin)
        for url in ('/attendance/on-duty/1/', '/attendance/condonation/1/revoke/', '/attendance/substitutes/1/delete/'):
            self.assertEqual(admin.get(url).status_code, 405, url)
        self.assertEqual(self.snapshot(), before)


class RetrySafetyTests(AttendanceTestBase):
    """A4: a failed submit loses nothing and a retry cannot create duplicates."""

    def test_a_failure_part_way_saves_nothing_and_the_retry_saves_exactly_once(self):
        client = self.as_user(self.staff1)
        client.raise_request_exception = False
        real = AttendanceReport.save
        calls = []

        def flaky(self_, *a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('network or server failure')
            return real(self_, *a, **k)
        with mock.patch.object(AttendanceReport, 'save', flaky):
            failed = self.take(client, self.sub1, {self.student_pk(self.stud1): 'absent'}, key='same-key')
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(self.classes(self.sub1), 1)               # nothing half-saved (only the world's own class)
        self.assertEqual(self.reports(self.sub1), {})
        for _ in range(2):                                           # the retry, and an impatient second tap
            self.assertEqual(self.take(client, self.sub1, {self.student_pk(self.stud1): 'absent'}, key='same-key').status_code, 302)
        self.assertEqual(self.classes(self.sub1), 2)
        self.assertEqual(sorted(self.reports(self.sub1).values()), ['absent', 'present'])
        with tenant_context(self.college_a):
            self.assertEqual(AttendanceReport.objects.filter(attendance__offering_id=self.sub1, attendance__date=TODAY).count(), 2)

    def test_concurrent_creation_falls_back_to_the_existing_class(self):
        with tenant_context(self.college_a):
            from academics.models import SubjectOffering
            offering = SubjectOffering.objects.get(pk=self.sub1)
            first = Attendance.objects.create(offering=offering, date=TODAY, period=1)
            real = Attendance.objects.select_for_update
            state = {'n': 0}

            class Hidden:  # the first lookup does not see the row (a race), so the create hits the unique constraint
                def filter(self_, **kw):
                    state['n'] += 1
                    return real().filter(pk=0) if state['n'] == 1 else real().filter(**kw)

                def get(self_, **kw):
                    return real().get(**kw)
            with mock.patch.object(Attendance.objects, 'select_for_update', lambda: Hidden()):
                att, created = services._get_or_create_class(offering, TODAY, 1, self.admin, 'k')
            self.assertEqual((att.pk, created), (first.pk, False))


class SubstituteTests(AttendanceTestBase):
    """A5."""

    def assign(self, day=None, period=1, substitute=None, client=None):
        staff = (substitute or self.staff2).staff
        return (client or self.as_user(self.admin)).post('/attendance/substitutes/', {
            'offering': self.sub1, 'date': iso(day or TODAY), 'period': period, 'substitute': staff.pk})

    def test_only_the_college_admin_can_assign_one_and_it_is_audited(self):
        self.assertEqual(self.assign(client=self.as_user(self.staff1)).status_code, 403)
        self.assertEqual(self.assign(client=self.as_user(self.staff2)).status_code, 403)
        with tenant_context(self.college_a):
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0
        self.assertEqual(self.assign().status_code, 302)
        with tenant_context(self.college_a):
            (entry,) = AuditLog.objects.filter(id__gt=mark, model_label='attendance.SubstituteAssignment')
        self.assertEqual((entry.action, entry.actor_email), ('create', 'admin@a.in'))

    def test_only_the_substitute_can_mark_that_class_on_that_date(self):
        self.assign()
        self.assertEqual(self.take(self.staff1, self.sub1).status_code, 403)                    # regular teacher: blocked
        self.assertEqual(self.as_user(self.staff1).get(f'/attendance/class/{self.sub1}/').status_code, 403)
        self.assertEqual(self.classes(self.sub1), 1)
        self.assertEqual(self.take(self.staff2, self.sub1, {self.student_pk(self.stud1): 'absent'}).status_code, 302)  # substitute
        self.assertEqual(self.reports(self.sub1)[self.student_pk(self.stud1)], 'absent')
        self.assertEqual(self.take(self.admin, self.sub1, key='adm').status_code, 302)          # admin can still edit

    def test_the_substitute_has_no_other_access_to_the_offering(self):
        self.assign()
        yesterday = TODAY - datetime.timedelta(days=1)
        self.assertEqual(self.take(self.staff2, self.sub1, day=yesterday).status_code, 403)     # another date
        self.assertEqual(self.take(self.staff2, self.sub1, period=2, key='p2').status_code, 403)  # another period
        self.assertEqual(self.as_user(self.staff2).post('/staff/result/fetch/', {
            'offering': self.sub1, 'student': self.student_pk(self.stud1)}).status_code, 403)  # not the marks

    def test_the_regular_teacher_is_unaffected_on_other_dates_and_periods(self):
        self.assign()
        yesterday = TODAY - datetime.timedelta(days=1)
        self.assertEqual(self.take(self.staff1, self.sub1, day=yesterday).status_code, 302)
        self.assertEqual(self.take(self.staff1, self.sub1, period=2, key='p2').status_code, 302)

    def test_the_home_page_shows_the_covered_class_to_the_substitute_only(self):
        self.assign()
        self.assertContains(self.as_user(self.staff2).get('/attendance/'), 'you are covering')
        self.assertNotContains(self.as_user(self.staff1).get('/attendance/'), 'Maths')

    def test_removing_the_substitute_gives_the_class_back(self):
        self.assign()
        with tenant_context(self.college_a):
            pk = SubstituteAssignment.objects.get().pk
        self.as_user(self.admin).post(f'/attendance/substitutes/{pk}/delete/')
        self.assertEqual(self.take(self.staff1, self.sub1).status_code, 302)

    def test_the_regular_teacher_cannot_be_their_own_substitute_and_one_per_class(self):
        resp = self.assign(substitute=self.staff1)
        self.assertContains(resp, 'regular teacher')
        self.assign()
        self.assign(substitute=self.admin if False else self.staff2)
        with tenant_context(self.college_a):
            self.assertEqual(SubstituteAssignment.objects.count(), 1)
