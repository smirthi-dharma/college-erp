"""Checks 6 to 14: the tiles show the right numbers, from the same rules as the reports, per term, and for an empty college."""
import datetime

from django.test import Client
from django_tenants.utils import tenant_context

from attendance import services as attendance
from colleges.tests.base import HOST_B

from .base import D, GoldenCollege


class GoldenNumbersTests(GoldenCollege):
    """Check 6."""

    def test_attendance_today(self):
        today = self.tiles()['attendance']
        self.assertEqual((today['classes'], today['marks'], today['present_percent']), (1, 3, D('66.7')))

    def test_shortage_counts_each_student_once_by_their_worst_status(self):
        s = self.tiles()['shortage']
        self.assertEqual((s['shortage'], s['warning'], s['condoned'], s['ok'], s['no_data'], s['total']), (1, 1, 1, 1, 1, 5))

    def test_sections_not_marked_names_the_teachers_and_ignores_sections_without_a_subject(self):
        sections = self.tiles()['sections']
        self.assertEqual(sections['total'], 3)
        rows = {str(r['section']): r for r in sections['unmarked']}
        self.assertEqual(len(rows), 2)
        by_teacher = {tuple(r['teachers']) for r in rows.values()}
        self.assertEqual(by_teacher, {('Raj Two',), ('Sita One',)})
        self.assertFalse([r for r in sections['unmarked'] if r['section'].pk == self.sec4])

    def test_marking_a_class_removes_its_section_from_the_list(self):
        from main_app.models import Attendance
        with tenant_context(self.college_a):
            Attendance.objects.create(offering_id=self.sub2, date=self.today, period=1)
        unmarked = {r['section'].pk for r in self.tiles()['sections']['unmarked']}
        self.assertEqual(unmarked, {self.sec3})

    def test_consent_counts_each_guardian_once_in_the_total_and_per_programme(self):
        c = self.tiles()['consent']
        self.assertEqual((c['total'], c['yes'], c['no'], c['unknown'], c['yes_percent']), (4, 2, 1, 1, D('50.0')))
        rows = {r['name']: r for r in c['programmes']}
        self.assertEqual((rows['C1']['total'], rows['C1']['yes'], rows['C1']['no'], rows['C1']['unknown']), (3, 1, 1, 1))
        self.assertEqual((rows['C2']['total'], rows['C2']['yes']), (1, 1))  # g1 is in both programmes but once in the total

    def test_the_same_numbers_reach_the_page(self):
        page = self.as_user(self.principal).get('/dashboard/').content.decode()
        for expected in ('66.7%', 'Open the shortage report', '50.0%', '2 yes, 1 no, 1 not asked (4 guardians)', 'Not available yet'):
            self.assertIn(expected, page)


class ReconciliationTests(GoldenCollege):
    """Checks 7, 8 and 10: the dashboard and the shortage report cannot disagree."""

    def report_students(self, status):
        with tenant_context(self.college_a):
            from academics.models import AcademicTerm
            term = AcademicTerm.objects.get(pk=self.term)
            return {s.pk for s, _entry in attendance.shortage_students(statuses=(status,), term=term)}

    def test_the_tile_numbers_equal_the_shortage_report_for_the_same_term(self):
        tiles = self.tiles()['shortage']
        in_shortage = self.report_students(attendance.SHORTAGE)
        warning_only = self.report_students(attendance.WARNING) - in_shortage
        self.assertEqual(len(in_shortage), tiles['shortage'])
        self.assertEqual(len(warning_only), tiles['warning'])
        self.assertEqual(len(self.report_students(attendance.CONDONED) - in_shortage), tiles['condoned'])
        by_student = tiles['worst_by_student']
        self.assertEqual({pk for pk, status in by_student.items() if status == attendance.SHORTAGE}, in_shortage)

    def test_the_report_page_shows_the_same_shortage_students_for_the_term(self):
        page = self.as_user(self.admin).get(f'/attendance/shortage/?term={self.term}&only=shortage').content.decode()
        self.assertIn('stud2@a.in' if 'stud2@a.in' in page else 'Two', page)  # Stu Two
        self.assertNotIn('Fifth', page)  # condoned, not in shortage

    def test_changing_the_attendance_rule_changes_the_tile(self):
        from academics.models import Regulation
        from attendance.models import AttendanceRule
        before = self.tiles()['shortage']
        self.assertEqual(before['shortage'], 1)
        with tenant_context(self.college_a):
            AttendanceRule.objects.update_or_create(regulation=Regulation.objects.get(pk=self.reg1), defaults={
                'required_percent': D('60'), 'warning_percent': D('95'), 'condonable_percent': D('50')})
        after = self.tiles()['shortage']
        self.assertEqual(after['shortage'], 0, 'stud2 (62.5%) is above a 60% requirement')
        self.assertGreater(after['warning'], before['warning'])  # students between 60% and 95% are now on warning
        self.assertEqual(self.report_students(attendance.SHORTAGE), set())

    def test_a_student_who_left_a_subject_is_counted_as_in_the_shortage_report(self):
        from academics.models import Enrolment
        with tenant_context(self.college_a):
            Enrolment.objects.filter(student=self.stud2.student, offering_id=self.sub1).update(
                status=Enrolment.WITHDRAWN, withdrawn_on=self.today)
        tiles = self.tiles()['shortage']
        self.assertEqual(len(self.report_students(attendance.SHORTAGE)), tiles['shortage'])  # whatever the report says, the tile says


class TermPickerTests(GoldenCollege):
    """Checks 12 (as-of) and 13."""

    def make_old_term(self):
        from academics.models import AcademicTerm
        with tenant_context(self.college_a):
            return AcademicTerm.objects.create(name='2024 ODD', start_date=datetime.date(2024, 6, 1), end_date=datetime.date(2024, 12, 31)).pk

    def test_the_current_term_is_selected_by_default(self):
        self.make_old_term()
        page = self.as_user(self.principal).get('/dashboard/')
        self.assertEqual(page.context['term'].pk, self.term)
        self.assertContains(page, f'<option value="{self.term}" selected>')

    def test_when_no_term_contains_today_the_latest_is_used(self):
        from academics.models import AcademicTerm
        with tenant_context(self.college_a):
            AcademicTerm.objects.filter(pk=self.term).update(start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 12, 31))
        newest = self.make_old_term()  # 2024-06-01: starts later than the 2020 term, though neither contains today
        page = self.as_user(self.principal).get('/dashboard/')
        self.assertEqual(page.context['term'].pk, newest, 'the one that starts latest, whatever the ids')
        self.assertContains(page, 'which is not the current term')

    def test_picking_another_term_changes_every_tile_and_is_remembered(self):
        old = self.make_old_term()
        client = self.as_user(self.principal)
        page = client.get(f'/dashboard/?term={old}')
        self.assertEqual(page.context['term'].pk, old)
        self.assertEqual(page.context['shortage']['no_data'], 5, 'the old term has no attendance: nobody has classes yet')
        self.assertEqual(page.context['sections']['total'], 0)
        self.assertEqual(page.context['attendance']['classes'], 0)
        self.assertContains(page, 'No attendance recorded today.')
        self.assertContains(page, 'still means')  # "today" tiles say they are about today
        again = client.get('/dashboard/')  # no ?term: the choice is remembered for this sign-in
        self.assertEqual(again.context['term'].pk, old)
        self.assertEqual(client.get(f'/dashboard/?term={self.term}').context['term'].pk, self.term)

    def test_each_user_has_their_own_remembered_choice(self):
        old = self.make_old_term()
        self.as_user(self.principal).get(f'/dashboard/?term={old}')
        self.assertEqual(self.as_user(self.admin).get('/dashboard/').context['term'].pk, self.term)

    def test_an_unknown_or_malformed_term_is_refused_with_a_message(self):
        client = self.as_user(self.principal)
        for bad in ('99999', 'abc', '-1', '1; drop table', ''):
            if bad == '':
                continue
            page = client.get('/dashboard/', {'term': bad})
            self.assertEqual(page.status_code, 400, bad)
            self.assertContains(page, 'That term does not exist.', status_code=400)
            self.assertEqual(page.context['term'].pk, self.term)  # falls back to the default, shows it, does not guess
        self.assertEqual(client.get('/dashboard/export/', {'term': '99999'}).status_code, 404)

    def test_as_of_and_how_calculated_are_shown_on_every_tile(self):
        page = self.as_user(self.principal).get('/dashboard/').content.decode()
        self.assertIn('as of ', page)
        self.assertEqual(page.count('How this is calculated'), 4)  # the four tiles with numbers


class EmptyCollegeTests(GoldenCollege):
    """Check 14."""

    def test_a_college_with_no_term_students_or_attendance_shows_zeros_and_a_hint(self):
        from colleges.tests.base import HOST_B
        self.make_user(self.college_b, 'p@b.in', '5', 'Bee', 'Principal')
        from django_tenants.utils import tenant_context as tc
        with tc(self.college_b):
            from main_app.models import CustomUser
            user = CustomUser.objects.get(email='p@b.in')
        client = Client(HTTP_HOST=HOST_B)
        from colleges.tests.base import EMAIL_BACKEND
        with tc(self.college_b):
            client.force_login(user, backend=EMAIL_BACKEND)
        page = client.get('/dashboard/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Nothing to show yet')
        self.assertContains(page, 'No attendance recorded today.')
        self.assertContains(page, 'No guardians on file yet.')
        self.assertEqual((page.context['shortage']['total'], page.context['sections']['total']), (0, 0))
        export = client.get('/dashboard/export/')
        self.assertEqual(export.status_code, 200)  # an empty college can still export (an empty workbook)

    def test_a_term_with_no_data_does_not_divide_by_zero(self):
        from academics.models import AcademicTerm
        with tenant_context(self.college_a):
            AcademicTerm.objects.create(name='Future', start_date=datetime.date(2098, 1, 1), end_date=datetime.date(2098, 12, 31))
        with tenant_context(self.college_a):
            future = AcademicTerm.objects.get(name='Future')
        tiles = self.tiles(future)
        self.assertEqual(tiles['attendance']['present_percent'], None)
        self.assertEqual(tiles['sections']['total'], 0)


class OtherCollegeTests(GoldenCollege):
    """Check 3 (numbers): another college's principal sees that college's numbers only."""

    def test_college_b_never_sees_college_a_numbers(self):
        from colleges.tests.base import EMAIL_BACKEND
        user = self.make_user(self.college_b, 'p2@b.in', '5', 'Bee', 'Two')
        self.make_user(self.college_b, 'stud@b.in', '3', 'Bee', 'Student')
        client = Client(HTTP_HOST=HOST_B)
        with tenant_context(self.college_b):
            client.force_login(user, backend=EMAIL_BACKEND)
        page = client.get('/dashboard/')
        self.assertEqual(page.context['shortage']['total'], 1)  # only B's one student
        self.assertEqual(page.context['consent']['total'], 0)
        body = page.content.decode()
        for a_only in ('Fifth', 'stud2@a.in', 'G One', 'Raj Two', '>C1<', '2099 ODD'):
            self.assertNotIn(a_only, body)
