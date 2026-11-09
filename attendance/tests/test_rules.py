"""Checks 9, 10, A1 (basis), A2 (condonation), A3 (on-duty review), reports and the student's view."""
import datetime
import io
from decimal import Decimal

from django_tenants.utils import tenant_context
from openpyxl import load_workbook

from attendance import services
from attendance.models import AttendanceRule, Condonation
from audit_trail.models import AuditLog
from main_app.models import AttendanceReport, Student

from .base import TODAY, AttendanceTestBase, iso


class Rules:
    required_percent = Decimal('75')
    warning_percent = Decimal('80')
    condonable_percent = Decimal('65')


class GoldenTableTests(AttendanceTestBase):
    def test_band_boundaries_use_exact_arithmetic(self):
        table = [  # attended, held, percent, status
            (0, 0, None, None),
            (299, 400, '74.75', 'shortage'), (74, 100, '74.00', 'shortage'),
            (3, 4, '75.00', 'warning'), (75, 100, '75.00', 'warning'),
            (799, 1000, '79.90', 'warning'), (79, 100, '79.00', 'warning'),
            (4, 5, '80.00', 'ok'), (80, 100, '80.00', 'ok'), (100, 100, '100.00', 'ok'),
        ]
        for attended, held, percent, status in table:
            got = services.evaluate(attended, held, Rules)
            self.assertEqual((got[0] and str(got[0]), got[1]), (percent, status), (attended, held))

    def test_can_still_miss_is_exact_and_never_negative(self):
        self.assertEqual(services.evaluate(90, 100, Rules)[2], 20)      # 90/0.75 = 120 classes total -> 20 more
        self.assertEqual(services.evaluate(75, 100, Rules)[2], 0)
        self.assertEqual(services.evaluate(74, 100, Rules)[2], 0)       # already short: never negative
        self.assertEqual(services.evaluate(0, 0, Rules)[2], 0)
        self.assertEqual(services.evaluate(3, 4, Rules)[2], 0)          # exactly on the line

    def test_the_condonable_edge(self):
        for attended, held, condoned_ok in ((65, 100, True), (64, 100, False), (649, 1000, False), (13, 20, True)):
            ok = attended * 100 >= Rules.condonable_percent * held
            self.assertEqual(ok, condoned_ok, (attended, held))

    def test_changing_the_rule_changes_the_result_with_no_code_change(self):
        stud = self.stud1.student
        self.add_history(stud, self.sub1, present=69, absent=30)     # 70 of 100 with the world's class
        with tenant_context(self.college_a):
            self.assertEqual(services.summarize([Student.objects.get(pk=stud.pk)])[stud.pk].status, 'shortage')
            AttendanceRule.objects.create(regulation=stud.section.batch.regulation, required_percent=65, warning_percent=70,
                                          condonable_percent=50)
            self.assertEqual(services.summarize([Student.objects.get(pk=stud.pk)])[stud.pk].status, 'ok')


class BasisTests(AttendanceTestBase):
    """A1: per-subject (default) vs overall."""

    def setUp(self):
        super().setUp()
        self.stud = self.stud1.student
        self.enrol(self.stud, self.sub2)
        self.add_history(self.stud, self.sub1, present=95, absent=4)     # 96 of 100: fine
        self.add_history(self.stud, self.sub2, present=5, absent=5)      # 6 of 11 incl. the world's class
        # note: stud1 is not in sub2's world class (stud3 is), so sub2 has exactly 10 held for stud1

    def summary(self):
        with tenant_context(self.college_a):
            return services.summarize([Student.objects.get(pk=self.stud.pk)])[self.stud.pk]

    def test_per_subject_is_the_default_and_one_short_subject_makes_the_student_short(self):
        summary = self.summary()
        self.assertEqual(summary.rule.basis, 'per_subject')
        self.assertEqual({e.offering.subject.code: e.status for e in summary.entries}, {'MA101': 'ok', 'PH101': 'shortage'})
        self.assertEqual(summary.status, 'shortage')

    def test_overall_counts_everything_together(self):
        with tenant_context(self.college_a):
            AttendanceRule.objects.create(regulation=self.stud.section.batch.regulation, basis='overall')
        summary = self.summary()
        self.assertEqual(len(summary.entries), 1)
        self.assertIsNone(summary.entries[0].offering)
        self.assertEqual((summary.entries[0].held, summary.entries[0].attended), (110, 101))
        self.assertEqual(summary.status, 'ok')

    def test_the_report_follows_the_setting(self):
        with tenant_context(self.college_a):
            rows = services.shortage_students()
            self.assertEqual([(s.pk, e.offering.subject.code) for s, e in rows if s.pk == self.stud.pk], [(self.stud.pk, 'PH101')])
            AttendanceRule.objects.create(regulation=self.stud.section.batch.regulation, basis='overall')
            self.assertEqual([r for r in services.shortage_students() if r[0].pk == self.stud.pk], [])


class CondonationTests(AttendanceTestBase):
    def setUp(self):
        super().setUp()
        self.stud = self.stud1.student

    def post(self, user, offering=None, reason='Medical certificate'):
        return self.as_user(user).post('/attendance/condonation/', {
            'student': self.stud.pk, 'offering': offering or '', 'reason': reason})

    def active(self):
        with tenant_context(self.college_a):
            return Condonation.objects.filter(revoked_at__isnull=True).count()

    def status(self):
        with tenant_context(self.college_a):
            return services.summarize([Student.objects.get(pk=self.stud.pk)])[self.stud.pk]

    def test_inside_the_band_the_college_admin_can_condone_and_it_is_audited(self):
        self.add_history(self.stud, self.sub1, present=69, absent=30)     # 70%: inside 65-75
        with tenant_context(self.college_a):
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first()
        self.assertEqual(self.post(self.admin, self.sub1).status_code, 302)
        self.assertEqual(self.active(), 1)
        self.assertEqual(self.status().entries[0].status, 'condoned')
        with tenant_context(self.college_a):
            c = Condonation.objects.get()
            self.assertEqual((c.percentage_at_approval, c.approved_by_id, c.reason), (Decimal('70.00'), self.admin.pk, 'Medical certificate'))
            entry = AuditLog.objects.get(id__gt=mark, model_label='attendance.Condonation')
        self.assertEqual((entry.action, entry.actor_email), ('create', 'admin@a.in'))

    def test_below_the_band_or_above_the_required_percentage_cannot_be_condoned(self):
        self.add_history(self.stud, self.sub1, present=59, absent=40)     # 60%: below 65
        self.assertContains(self.post(self.admin, self.sub1), 'cannot be condoned')
        self.assertEqual(self.active(), 0)
        self.add_history(self.stud, self.sub1, present=100, absent=0)    # now well above 75
        self.assertContains(self.post(self.admin, self.sub1), 'not below the required')
        self.assertEqual(self.active(), 0)

    def test_the_band_is_a_setting(self):
        self.add_history(self.stud, self.sub1, present=59, absent=40)     # 60%
        with tenant_context(self.college_a):
            AttendanceRule.objects.create(regulation=self.stud.section.batch.regulation, condonable_percent=55)
        self.assertEqual(self.post(self.admin, self.sub1).status_code, 302)
        self.assertEqual(self.active(), 1)

    def test_one_active_condonation_per_scope_and_revoking_restores_the_shortage(self):
        self.add_history(self.stud, self.sub1, present=69, absent=30)
        self.post(self.admin, self.sub1)
        self.assertContains(self.post(self.admin, self.sub1), 'already has an active condonation')
        with tenant_context(self.college_a):
            pk = Condonation.objects.get().pk
        self.assertEqual(self.as_user(self.admin).post(f'/attendance/condonation/{pk}/revoke/').status_code, 302)
        self.assertEqual(self.status().entries[0].status, 'shortage')
        with tenant_context(self.college_a):
            revoked = Condonation.objects.get()
            self.assertEqual((revoked.revoked_by_id, revoked.revoked_at is not None), (self.admin.pk, True))
            self.assertTrue(AuditLog.objects.filter(model_label='attendance.Condonation', action='update').exists())
        self.assertEqual(self.post(self.admin, self.sub1).status_code, 302)  # a new one is allowed after a revoke

    def test_only_the_college_admin_can_condone_or_revoke_and_teachers_cannot_see_the_page(self):
        self.add_history(self.stud, self.sub1, present=69, absent=30)
        for user in (self.staff1, self.stud1):
            self.assertEqual(self.post(user, self.sub1).status_code, 403)
            self.assertEqual(self.as_user(user).get('/attendance/condonation/').status_code, 403)
        self.assertEqual(self.active(), 0)
        with tenant_context(self.college_a):
            with self.assertRaises(Exception):  # noqa: B017 - the service itself refuses a non-admin
                services.condone(self.staff1, self.stud, None, 'x')

    def test_a_reason_is_required_and_the_basis_must_match(self):
        self.add_history(self.stud, self.sub1, present=69, absent=30)
        self.assertContains(self.post(self.admin, self.sub1, reason='  '), 'required')
        self.assertContains(self.post(self.admin, offering=None), 'per subject')
        with tenant_context(self.college_a):
            AttendanceRule.objects.create(regulation=self.stud.section.batch.regulation, basis='overall')
        self.assertContains(self.post(self.admin, self.sub1), 'overall')
        self.assertEqual(self.post(self.admin, offering=None).status_code, 302)  # overall condonation
        self.assertEqual(self.status().entries[0].status, 'condoned')

    def test_the_student_sees_that_a_shortage_was_condoned(self):
        self.add_history(self.stud, self.sub1, present=69, absent=30)
        self.post(self.admin, self.sub1)
        self.assertContains(self.as_user(self.stud1).get('/attendance/mine/'), 'condoned')


class OnDutyReviewTests(AttendanceTestBase):
    def od(self, user, status='on_duty'):
        return self.take(user, self.sub1, {self.student_pk(self.stud1): status})

    def report(self):
        with tenant_context(self.college_a):
            return AttendanceReport.objects.get(attendance__offering_id=self.sub1, attendance__date=TODAY, student=self.stud1.student)

    def test_a_teachers_on_duty_is_flagged_and_an_admins_is_not(self):
        self.od(self.staff1)
        self.assertEqual((self.report().status, self.report().needs_review), ('on_duty', True))
        self.od(self.admin, 'on_duty')
        self.assertEqual((self.report().status, self.report().needs_review), ('on_duty', True))  # unchanged, still pending
        with tenant_context(self.college_a):
            AttendanceReport.objects.all().delete()
            from main_app.models import Attendance
            Attendance.objects.filter(offering_id=self.sub1, date=TODAY).delete()
        self.od(self.admin)
        self.assertEqual((self.report().status, self.report().needs_review), ('on_duty', False))

    def test_pending_on_duty_counts_as_present_for_now_and_is_flagged(self):
        self.od(self.staff1)
        with tenant_context(self.college_a):
            entry = services.summarize([Student.objects.get(pk=self.stud1.student.pk)])[self.stud1.student.pk].entries[0]
        self.assertEqual((entry.held, entry.attended, entry.pending_on_duty), (2, 2, 1))
        self.assertContains(self.as_user(self.stud1).get('/attendance/mine/'), 'awaiting review')

    def test_the_queue_is_for_the_college_admin_who_can_approve_or_reject_with_audit(self):
        self.od(self.staff1)
        report = self.report()
        self.assertContains(self.as_user(self.admin).get('/attendance/on-duty/'), 'Approve')
        for user in (self.staff1, self.stud1):
            self.assertEqual(self.as_user(user).post(f'/attendance/on-duty/{report.pk}/', {'decision': 'approve'}).status_code, 403)
        with tenant_context(self.college_a):
            mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first()
        self.as_user(self.admin).post(f'/attendance/on-duty/{report.pk}/', {'decision': 'approve'})
        self.assertEqual((self.report().status, self.report().needs_review), ('on_duty', False))
        with tenant_context(self.college_a):
            entry = AuditLog.objects.get(id__gt=mark, model_label='main_app.AttendanceReport')
        self.assertEqual((entry.action, entry.actor_email), ('update', 'admin@a.in'))

    def test_a_rejected_on_duty_becomes_absent_and_counts_as_absent(self):
        self.od(self.staff1)
        self.as_user(self.admin).post(f'/attendance/on-duty/{self.report().pk}/', {'decision': 'reject'})
        self.assertEqual((self.report().status, self.report().needs_review), ('absent', False))
        with tenant_context(self.college_a):
            entry = services.summarize([Student.objects.get(pk=self.stud1.student.pk)])[self.stud1.student.pk].entries[0]
        self.assertEqual((entry.held, entry.attended), (2, 1))

    def test_an_edited_mark_is_no_longer_pending(self):
        self.od(self.staff1)
        self.take(self.staff1, self.sub1, {self.student_pk(self.stud1): 'present'}, key='fix')
        self.assertEqual((self.report().status, self.report().needs_review), ('present', False))


class ReportAndStudentViewTests(AttendanceTestBase):
    def test_the_shortage_report_lists_students_and_downloads_as_a_safe_excel_file(self):
        self.add_history(self.stud1.student, self.sub1, present=60, absent=39)    # 61%
        page = self.as_user(self.admin).get('/attendance/shortage/')
        self.assertContains(page, 'SHORTAGE')
        self.assertContains(page, 'Maths')
        xlsx = self.as_user(self.admin).get('/attendance/shortage/?format=xlsx')
        sheet = load_workbook(io.BytesIO(xlsx.content)).active
        values = [[c.value for c in row] for row in sheet.iter_rows()]
        self.assertEqual(values[0][:2], ['Roll number', 'Name'])
        self.assertTrue(any(row[7] == 'shortage' for row in values[1:]))
        for row in values:
            for cell in row:
                self.assertFalse(isinstance(cell, str) and cell.startswith(('=', '+', '-', '@')), cell)

    def test_the_shortage_report_filters_by_section_and_subject(self):
        self.add_history(self.stud1.student, self.sub1, present=60, absent=39)
        self.add_history(self.stud3.student, self.sub2, present=60, absent=39)
        with tenant_context(self.college_a):
            other = Student.objects.get(pk=self.stud3.student.pk)
        page = self.as_user(self.admin).get(f'/attendance/shortage/?section={other.section_id}')
        self.assertContains(page, 'Physics')
        self.assertNotContains(page, 'Maths')

    def test_a_student_below_the_line_sees_the_banner_on_the_home_page(self):
        self.add_history(self.stud1.student, self.sub1, present=50, absent=49)
        self.assertContains(self.as_user(self.stud1).get('/student/home/'), 'below the required')

    def test_the_rule_pages_save_and_validate(self):
        with tenant_context(self.college_a):
            reg = self.stud1.student.section.batch.regulation
        client = self.as_user(self.admin)
        page = client.get('/attendance/rules/')
        self.assertContains(page, '(defaults)')
        bad = client.post(f'/attendance/rules/{reg.pk}/', {'required_percent': 70, 'warning_percent': 60, 'condonable_percent': 50,
                                                          'basis': 'per_subject', 'on_duty_counts_as_present': 'on', 'edit_window_days': 3})
        self.assertContains(bad, 'Percentages must satisfy')
        ok = client.post(f'/attendance/rules/{reg.pk}/', {'required_percent': 70, 'warning_percent': 75, 'condonable_percent': 50,
                                                         'basis': 'overall', 'on_duty_counts_as_present': 'on', 'edit_window_days': 5})
        self.assertEqual(ok.status_code, 302)
        with tenant_context(self.college_a):
            rule = AttendanceRule.objects.get(regulation=reg)
            self.assertEqual((rule.required_percent, rule.basis, rule.edit_window_days), (Decimal('70'), 'overall', 5))
        self.assertEqual(self.as_user(self.staff1).post(f'/attendance/rules/{reg.pk}/', {}).status_code, 403)

    def test_the_home_page_lists_todays_classes_for_a_teacher_and_all_for_the_admin(self):
        self.assertContains(self.as_user(self.staff1).get('/attendance/'), 'Maths')
        self.assertNotContains(self.as_user(self.staff1).get('/attendance/'), 'Physics')
        page = self.as_user(self.admin).get('/attendance/')
        self.assertContains(page, 'Maths')
        self.assertContains(page, 'Physics')
        self.assertContains(self.as_user(self.staff1).get(f'/attendance/?date={iso(TODAY - datetime.timedelta(days=1))}'), 'Maths')
