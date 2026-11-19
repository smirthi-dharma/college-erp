"""Pages whose query count must not grow with the size of the college (so a 3,000-student college loads as fast as a tiny one)."""
import json

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import tenant_context

from .world import WorldTestCase


def real_queries(ctx):
    skip = ('SET search_path', 'SAVEPOINT', 'RELEASE SAVEPOINT')
    return [q['sql'] for q in ctx.captured_queries if not q['sql'].startswith(skip)]


class AdminHomeTests(WorldTestCase):
    def test_the_charts_show_the_right_numbers(self):
        context = self.as_user(self.admin).get('/admin/home/').context
        numbers = {name: json.loads(str(context[name])) for name in (
            'subject_list', 'attendance_list', 'student_count_list_in_subject', 'course_name_list',
            'student_count_list_in_course', 'student_attendance_present_list', 'student_attendance_leave_list',
            'student_name_list')}
        self.assertEqual(numbers['subject_list'], ['MA101', 'PH101'])
        self.assertEqual(numbers['attendance_list'], [1, 1])
        self.assertEqual(numbers['student_count_list_in_subject'], [2, 1])
        self.assertEqual(numbers['course_name_list'], ['C1', 'C2'])
        self.assertEqual(numbers['student_count_list_in_course'], [2, 1])
        self.assertEqual(numbers['student_attendance_present_list'], [1, 0, 1])  # stud1, stud2, stud3
        self.assertEqual(numbers['student_attendance_leave_list'], [0, 1, 0])
        self.assertEqual(numbers['student_name_list'], ['Stu', 'Stu', 'Stu'])

    def test_a_student_without_any_attendance_shows_zero(self):
        self.make_user(self.college_a, 'fresh@a.in', '3', 'Fresh', 'Face')
        context = self.as_user(self.admin).get('/admin/home/').context
        self.assertEqual(json.loads(str(context['student_attendance_present_list']))[-1], 0)

    def test_the_number_of_queries_does_not_depend_on_the_number_of_students(self):
        client = self.as_user(self.admin)
        with CaptureQueriesContext(connection) as small:
            client.get('/admin/home/')
        with tenant_context(self.college_a):
            from main_app.models import CustomUser
            for i in range(60):
                CustomUser.objects.create_user(f'bulk{i}@a.in', None, first_name='B', last_name=f'S{i}', user_type='3')
        with CaptureQueriesContext(connection) as large:
            response = client.get('/admin/home/')
        self.assertEqual(response.context['total_students'], 63)
        self.assertEqual(len(real_queries(large)), len(real_queries(small)))
        self.assertLessEqual(len(real_queries(large)), 20)
