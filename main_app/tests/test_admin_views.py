"""The college admin's inherited pages: add, edit, list and delete staff and students, own profile, home, attendance view."""
import json
from unittest import mock

from django_tenants.utils import tenant_context

from colleges.tests.base import PASSWORD

from .helpers import STRONG, UploadCleanupMixin, message_texts, picture
from .world import WorldTestCase


class AdminTestCase(UploadCleanupMixin, WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client_admin = self.as_user(self.admin)

    def texts(self, response):
        return message_texts(response)

    def staff_data(self, **extra):
        data = {'first_name': 'New', 'last_name': 'Teacher', 'email': 'New.Teacher@A.in', 'gender': 'F', 'password': STRONG,
                'address': '1 Road', 'phone': '98765 43210', 'employee_id': 'E100', 'department': self.dept1,
                'profile_pic': picture()}
        data.update(extra)
        return data

    def student_data(self, **extra):
        data = {'first_name': 'New', 'last_name': 'Pupil', 'email': 'pupil@a.in', 'gender': 'M', 'password': STRONG,
                'address': '2 Road', 'phone': '', 'roll_number': 'R-9', 'date_of_birth': '2006-05-04', 'section': self.sec1,
                'profile_pic': picture()}
        data.update(extra)
        return data

    def count(self, model):
        with tenant_context(self.college_a):
            return model.objects.count()


class AddStaffTests(AdminTestCase):
    def test_form_page_opens(self):
        self.assertEqual(self.client_admin.get('/staff/add').status_code, 200)

    def test_a_valid_form_creates_a_teacher_with_the_right_role_and_details(self):
        from main_app.models import CustomUser
        response = self.client_admin.post('/staff/add', self.staff_data())
        self.assertRedirects(response, '/staff/add', fetch_redirect_response=False)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='new.teacher@a.in')
            self.assertEqual(user.user_type, '2')
            self.assertEqual(user.phone, '+919876543210')
            self.assertEqual(user.staff.employee_id, 'E100')
            self.assertEqual(user.staff.department_id, self.dept1)
            self.assertTrue(user.check_password(STRONG))

    def test_missing_picture_is_a_form_error_not_a_crash(self):
        data = self.staff_data()
        del data['profile_pic']
        response = self.client_admin.post('/staff/add', data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Please fulfil all requirements', self.texts(response))

    def test_duplicate_email_and_weak_password_are_refused(self):
        before = self.count(self.admin.__class__)
        for bad in ({'email': 'STAFF1@a.in'}, {'password': '1234'}, {'phone': '12345'}):
            response = self.client_admin.post('/staff/add', self.staff_data(**bad))
            self.assertEqual(response.status_code, 200, bad)
            self.assertIn('Please fulfil all requirements', self.texts(response), bad)
        self.assertEqual(self.count(self.admin.__class__), before)

    def test_a_failure_while_saving_shows_a_message(self):
        with mock.patch('main_app.hod_views.CustomUser.objects.create_user', side_effect=RuntimeError('boom')):
            response = self.client_admin.post('/staff/add', self.staff_data())
        self.assertIn('Could Not Add ', self.texts(response))


class AddStudentTests(AdminTestCase):
    def test_form_page_opens(self):
        self.assertEqual(self.client_admin.get('/student/add/').status_code, 200)

    def test_a_valid_form_creates_a_student_who_joins_the_sections_core_subjects(self):
        from academics.models import Enrolment
        from main_app.models import CustomUser
        response = self.client_admin.post('/student/add/', self.student_data())
        self.assertRedirects(response, '/student/add/', fetch_redirect_response=False)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='pupil@a.in')
            self.assertEqual(user.user_type, '3')
            self.assertEqual(user.student.roll_number, 'R-9')
            self.assertEqual(user.student.section_id, self.sec1)
            self.assertTrue(Enrolment.objects.filter(student=user.student, offering_id=self.sub1, status='active').exists())

    def test_bad_forms_create_nothing(self):
        from main_app.models import Student
        before = self.count(Student)
        for bad in ({'email': 'stud1@a.in'}, {'password': 'abc'}, {'date_of_birth': 'not-a-date'}):
            response = self.client_admin.post('/student/add/', self.student_data(**bad))
            self.assertEqual(response.status_code, 200, bad)
            self.assertIn('Could Not Add: ', self.texts(response), bad)
        self.assertEqual(self.count(Student), before)

    def test_a_failure_while_saving_shows_a_message(self):
        with mock.patch('main_app.hod_views.CustomUser.objects.create_user', side_effect=RuntimeError('boom')):
            response = self.client_admin.post('/student/add/', self.student_data())
        self.assertIn('Could Not Add: ', self.texts(response))


class ListTests(AdminTestCase):
    def test_lists_show_this_colleges_people_only(self):
        self.make_user(self.college_b, 'bstaff@b.in', '2', 'Bee', 'Staffer')
        self.make_user(self.college_b, 'bstud@b.in', '3', 'Bee', 'Pupil')
        staff_page = self.client_admin.get('/staff/manage/').content.decode()
        student_page = self.client_admin.get('/student/manage/').content.decode()
        self.assertIn('staff1@a.in', staff_page)
        self.assertIn('stud2@a.in', student_page)
        self.assertNotIn('bstaff@b.in', staff_page + student_page)
        self.assertNotIn('bstud@b.in', staff_page + student_page)


class EditStaffTests(AdminTestCase):
    def url(self, user=None):
        return '/staff/edit/%d' % (user or self.staff1).staff.pk

    def edit_data(self, **extra):
        data = self.staff_data(email='staff1@a.in', first_name='Renamed', password='', profile_pic='')
        data.pop('profile_pic')
        data.update(extra)
        return data

    def test_page_opens_with_current_values_and_404_for_unknown(self):
        response = self.client_admin.get(self.url())
        self.assertContains(response, 'Sita')
        self.assertEqual(self.client_admin.get('/staff/edit/99999').status_code, 404)

    def test_editing_without_a_password_keeps_the_password(self):
        from main_app.models import CustomUser
        response = self.client_admin.post(self.url(), self.edit_data(department=self.dept2))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.staff1.pk)
            self.assertEqual(user.first_name, 'Renamed')
            self.assertEqual(user.staff.department_id, self.dept2)
            self.assertTrue(user.check_password(PASSWORD), 'password must be unchanged')

    def test_a_new_password_and_a_new_picture_are_saved(self):
        from main_app.models import CustomUser
        self.client_admin.post(self.url(), dict(self.edit_data(password=STRONG), profile_pic=picture()))
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.staff1.pk)
            self.assertTrue(user.check_password(STRONG))
            self.assertIn('me', user.profile_pic.name if hasattr(user.profile_pic, 'name') else str(user.profile_pic))

    def test_taking_someone_elses_email_is_refused(self):
        response = self.client_admin.post(self.url(), self.edit_data(email='staff2@a.in'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Please fil form properly', self.texts(response))

    def test_a_failure_while_saving_shows_a_message(self):
        with mock.patch('main_app.models.CustomUser.save', side_effect=RuntimeError('boom')):
            response = self.client_admin.post(self.url(), self.edit_data())
        self.assertIn('Could Not Update ', self.texts(response))


class EditStudentTests(AdminTestCase):
    def url(self, user=None):
        return '/student/edit/%d' % (user or self.stud1).student.pk

    def edit_data(self, **extra):
        data = self.student_data(email='stud1@a.in', first_name='Changed', password='')
        data.pop('profile_pic')
        data.update(extra)
        return data

    def test_page_opens_and_404_for_unknown(self):
        self.assertContains(self.client_admin.get(self.url()), 'Stu')
        self.assertEqual(self.client_admin.get('/student/edit/99999').status_code, 404)

    def test_editing_changes_details_and_moves_the_section(self):
        from main_app.models import CustomUser
        response = self.client_admin.post(self.url(), self.edit_data(section=self.sec2, roll_number='Z-1'))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.stud1.pk)
            self.assertEqual((user.first_name, user.student.section_id, user.student.roll_number), ('Changed', self.sec2, 'Z-1'))
            self.assertTrue(user.check_password(PASSWORD))

    def test_password_and_picture_can_be_replaced(self):
        from main_app.models import CustomUser
        self.client_admin.post(self.url(), dict(self.edit_data(password=STRONG), profile_pic=picture()))
        with tenant_context(self.college_a):
            self.assertTrue(CustomUser.objects.get(pk=self.stud1.pk).check_password(STRONG))

    def test_taking_someone_elses_email_is_refused(self):
        response = self.client_admin.post(self.url(), self.edit_data(email='stud2@a.in'))
        self.assertIn('Please Fill Form Properly!', self.texts(response))

    def test_a_failure_while_saving_shows_a_message(self):
        with mock.patch('main_app.models.CustomUser.save', side_effect=RuntimeError('boom')):
            response = self.client_admin.post(self.url(), self.edit_data())
        self.assertIn('Could Not Update ', self.texts(response))


class DeleteTests(AdminTestCase):
    def test_deleting_a_student_removes_them_and_their_records(self):
        from main_app.models import AttendanceReport, CustomUser
        response = self.client_admin.post('/student/delete/%d' % self.stud2.student.pk)
        self.assertRedirects(response, '/student/manage/', fetch_redirect_response=False)
        with tenant_context(self.college_a):
            self.assertFalse(CustomUser.objects.filter(pk=self.stud2.pk).exists())
            self.assertFalse(AttendanceReport.objects.filter(student_id=self.stud2.student.pk).exists())

    def test_deleting_a_teacher_with_no_classes_works(self):
        from main_app.models import CustomUser
        spare = self.make_user(self.college_a, 'spare@a.in', '2', 'Spare', 'Teacher')
        response = self.client_admin.post('/staff/delete/%d' % spare.staff.pk)
        self.assertRedirects(response, '/staff/manage/', fetch_redirect_response=False)
        with tenant_context(self.college_a):
            self.assertFalse(CustomUser.objects.filter(pk=spare.pk).exists())

    def test_unknown_ids_are_404_and_get_is_refused(self):
        self.assertEqual(self.client_admin.post('/student/delete/99999').status_code, 404)
        self.assertEqual(self.client_admin.post('/staff/delete/99999').status_code, 404)
        self.assertEqual(self.client_admin.get('/student/delete/%d' % self.stud1.student.pk).status_code, 405)

    def test_a_teacher_who_teaches_a_subject_is_not_deleted_and_the_admin_is_told_why(self):
        from main_app.models import CustomUser
        response = self.client_admin.post('/staff/delete/%d' % self.staff1.staff.pk, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('This teacher still teaches subjects. Give those subjects to another teacher first.', message_texts(response))
        with tenant_context(self.college_a):
            self.assertTrue(CustomUser.objects.filter(pk=self.staff1.pk).exists())


class SmallEndpointsTests(AdminTestCase):
    def test_email_availability(self):
        post = self.client_admin.post
        self.assertEqual(post('/check_email_availability', {'email': 'stud1@a.in'}).content, b'True')
        self.assertEqual(post('/check_email_availability', {'email': 'nobody@a.in'}).content, b'False')
        self.assertEqual(self.client_admin.get('/check_email_availability').status_code, 405)

    def test_email_availability_does_not_see_the_other_college(self):
        self.make_user(self.college_b, 'only@b.in', '3', 'B', 'Only')
        self.assertEqual(self.client_admin.post('/check_email_availability', {'email': 'only@b.in'}).content, b'False')


class AttendanceViewTests(AdminTestCase):
    def test_page_lists_offerings(self):
        response = self.client_admin.get('/attendance/view/')
        self.assertContains(response, 'Maths')

    def test_fetch_returns_each_students_status(self):
        response = self.client_admin.post('/attendance/fetch/', {'attendance_date_id': self.att1, 'offering': self.sub1})
        rows = json.loads(json.loads(response.content))
        self.assertEqual(sorted(r['status'] for r in rows), ['absent', 'present'])

    def test_a_mismatched_pair_is_404(self):
        response = self.client_admin.post('/attendance/fetch/', {'attendance_date_id': self.att1, 'offering': self.sub2})
        self.assertEqual(response.status_code, 404)

    def test_get_attendance_lists_dates_of_an_offering(self):
        response = self.client_admin.post('/get_attendance', {'offering': self.sub1})
        self.assertEqual(response.status_code, 200)
        self.assertIn('2026-01-05', response.content.decode())


class AdminProfileAndHomeTests(AdminTestCase):
    def profile(self, **extra):
        data = {'first_name': 'Ada2', 'last_name': 'Admin2', 'email': 'admin@a.in', 'gender': 'F', 'password': '',
                'address': 'x', 'phone': ''}
        data.update(extra)
        return data

    def test_profile_opens_and_updates_name(self):
        from main_app.models import CustomUser
        self.assertEqual(self.client_admin.get('/admin_view_profile').status_code, 200)
        response = self.client_admin.post('/admin_view_profile', dict(self.profile(), profile_pic=picture()))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.get(pk=self.admin.pk).first_name, 'Ada2')

    def test_profile_password_change_and_bad_data(self):
        from main_app.models import CustomUser
        self.client_admin.post('/admin_view_profile', dict(self.profile(password=STRONG), profile_pic=picture()))
        with tenant_context(self.college_a):
            fresh = CustomUser.objects.get(pk=self.admin.pk)  # the password changed, so log in again with the new hash
            self.assertTrue(fresh.check_password(STRONG))
        response = self.as_user(fresh).post('/admin_view_profile', self.profile(email='not-an-email'))
        self.assertIn('Invalid Data Provided', self.texts(response))

    def test_home_counts_and_chart_data_are_for_this_college(self):
        response = self.client_admin.get('/admin/home/')
        self.assertEqual(response.context['total_students'], 3)
        self.assertEqual(response.context['total_staff'], 2)
        self.assertEqual(response.context['total_subject'], 2)
