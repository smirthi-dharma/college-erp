"""The teacher's and student's inherited pages: home, profile, marks, own attendance, login and logout."""
import json
from unittest import mock

from django.test import override_settings
from django_tenants.utils import tenant_context

from colleges.tests.base import PASSWORD

from .helpers import STRONG, UploadCleanupMixin, message_texts, picture
from .world import STUD2_EXAM, STUD2_TEST, WorldTestCase


class StaffViewsTests(UploadCleanupMixin, WorldTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = self.as_user(self.staff1)

    def profile(self, **extra):
        data = {'first_name': 'Sita2', 'last_name': 'One', 'email': 'staff1@a.in', 'gender': 'F', 'password': '',
                'address': 'Somewhere', 'phone': '9876543210', 'employee_id': '', 'department': ''}
        data.update(extra)
        return data

    def test_home_counts_only_own_classes(self):
        response = self.teacher.get('/staff/home/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context['total_subject'], response.context['total_students'],
                          response.context['total_attendance']), (1, 2, 1))

    def test_students_of_an_own_offering_are_listed_and_a_foreign_one_is_refused(self):
        rows = json.loads(json.loads(self.teacher.post('/staff/get_students/', {'offering': self.sub1}).content))
        self.assertEqual(sorted(r['name'] for r in rows), ['One Stu', 'Two Stu'])
        self.assertEqual(self.teacher.post('/staff/get_students/', {'offering': self.sub2}).status_code, 403)

    def test_profile_opens_and_saves_changes(self):
        from main_app.models import CustomUser
        self.assertEqual(self.teacher.get('/staff/view/profile/').status_code, 200)
        response = self.teacher.post('/staff/view/profile/', dict(self.profile(), profile_pic=picture()))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.staff1.pk)
            self.assertEqual((user.first_name, user.phone), ('Sita2', '+919876543210'))
            self.assertTrue(user.check_password(PASSWORD))

    def test_profile_without_a_new_picture_is_accepted_and_password_can_change(self):
        from main_app.models import CustomUser
        response = self.teacher.post('/staff/view/profile/', self.profile(password=STRONG))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            self.assertTrue(CustomUser.objects.get(pk=self.staff1.pk).check_password(STRONG))

    def test_profile_with_bad_data_shows_a_message_and_changes_nothing(self):
        from main_app.models import CustomUser
        response = self.teacher.post('/staff/view/profile/', self.profile(first_name='', phone='12'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid Data Provided', message_texts(response))
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.get(pk=self.staff1.pk).first_name, 'Sita')

    def test_profile_failure_while_saving_shows_a_message(self):
        with mock.patch('main_app.models.CustomUser.save', side_effect=RuntimeError('boom')):
            response = self.teacher.post('/staff/view/profile/', self.profile())
        self.assertIn('Could not update the profile.', message_texts(response))

    def test_add_result_creates_then_updates_and_rejects_text_marks(self):
        from main_app.models import StudentResult
        page = self.teacher.get('/staff/result/add/')
        self.assertContains(page, 'Maths')
        data = {'offering': self.sub1, 'student_list': self.stud_ids['stud1@a.in'], 'test': '15', 'exam': '45.5'}
        self.assertIn('Scores Updated', message_texts(self.teacher.post('/staff/result/add/', data)))
        brand_new = self.make_user(self.college_a, 'late@a.in', '3', 'Late', 'Joiner')
        with tenant_context(self.college_a):
            student = brand_new.student
            student.section_id = self.sec1
            student.save()
        data['student_list'] = student.pk
        self.assertIn('Scores Saved', message_texts(self.teacher.post('/staff/result/add/', data)))
        with tenant_context(self.college_a):
            self.assertEqual(StudentResult.objects.get(student=student, offering_id=self.sub1).exam, 45.5)
        data['test'] = 'abc'
        self.assertIn('Marks must be numbers', message_texts(self.teacher.post('/staff/result/add/', data)))

    def test_add_result_is_refused_for_other_teachers_classes_and_outsiders(self):
        base = {'test': '1', 'exam': '1'}
        self.assertEqual(self.teacher.post('/staff/result/add/', dict(base, offering=self.sub2, student_list=self.stud_ids['stud3@a.in'])).status_code, 403)
        self.assertEqual(self.teacher.post('/staff/result/add/', dict(base, offering=self.sub1, student_list=self.stud_ids['stud3@a.in'])).status_code, 403)
        self.assertEqual(self.teacher.post('/staff/result/add/', dict(base, offering=self.sub1, student_list=99999)).status_code, 404)

    def test_fetch_result_returns_marks_or_false(self):
        ok = self.teacher.post('/staff/result/fetch/', {'offering': self.sub1, 'student': self.stud_ids['stud1@a.in']})
        self.assertEqual(json.loads(ok.content), {'exam': 22.0, 'test': 11.0})
        from main_app.models import StudentResult
        with tenant_context(self.college_a):
            StudentResult.objects.filter(student_id=self.stud_ids['stud1@a.in']).delete()
        gone = self.teacher.post('/staff/result/fetch/', {'offering': self.sub1, 'student': self.stud_ids['stud1@a.in']})
        self.assertEqual(gone.content, b'False')

    def test_edit_result_page_changes_an_existing_result_only(self):
        from main_app.models import StudentResult
        self.assertEqual(self.teacher.get('/staff/result/edit/').status_code, 200)
        data = {'offering': self.sub1, 'student': self.stud_ids['stud1@a.in'], 'test': 19, 'exam': 77}
        self.assertEqual(self.teacher.post('/staff/result/edit/', data).status_code, 302)
        with tenant_context(self.college_a):
            result = StudentResult.objects.get(student_id=self.stud_ids['stud1@a.in'], offering_id=self.sub1)
            self.assertEqual((result.test, result.exam), (19, 77))
            StudentResult.objects.filter(pk=result.pk).delete()
        self.assertEqual(self.teacher.post('/staff/result/edit/', data).status_code, 200)  # nothing to edit
        other = dict(data, offering=self.sub2)  # not this teacher's offering: the form does not offer it
        self.assertEqual(self.teacher.post('/staff/result/edit/', other).status_code, 200)


class StudentViewsTests(UploadCleanupMixin, WorldTestCase):
    def setUp(self):
        super().setUp()
        self.pupil = self.as_user(self.stud1)

    def profile(self, **extra):
        data = {'first_name': 'Stu2', 'last_name': 'One', 'email': 'stud1@a.in', 'gender': 'M', 'password': '',
                'address': 'Home', 'phone': '', 'roll_number': '', 'date_of_birth': '', 'section': ''}
        data.update(extra)
        return data

    def test_home_computes_attendance_percentages(self):
        response = self.pupil.get('/student/home/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context['total_attendance'], response.context['percent_present'],
                          response.context['percent_absent']), (1, 100, 0))
        absent_one = self.as_user(self.stud2).get('/student/home/')
        self.assertEqual((absent_one.context['percent_present'], absent_one.context['percent_absent']), (0, 100))

    def test_home_with_no_attendance_does_not_divide_by_zero(self):
        newcomer = self.make_user(self.college_a, 'new@a.in', '3', 'New', 'Comer')
        response = self.as_user(newcomer).get('/student/home/')
        self.assertEqual((response.status_code, response.context['percent_present']), (200, 0))

    def test_attendance_page_and_query(self):
        self.assertContains(self.pupil.get('/student/view/attendance/'), 'Maths')
        data = {'offering': self.sub1, 'start_date': '2026-01-01', 'end_date': '2026-01-31'}
        rows = json.loads(json.loads(self.pupil.post('/student/view/attendance/', data).content))
        self.assertEqual(rows, [{'date': '2026-01-05', 'status': True}])
        empty = dict(data, start_date='2027-01-01', end_date='2027-01-31')
        self.assertEqual(json.loads(json.loads(self.pupil.post('/student/view/attendance/', empty).content)), [])

    def test_attendance_query_rejects_bad_dates_and_foreign_offerings(self):
        data = {'offering': self.sub1, 'start_date': 'yesterday', 'end_date': '2026-01-31'}
        self.assertEqual(self.pupil.post('/student/view/attendance/', data).status_code, 400)
        self.assertEqual(self.pupil.post('/student/view/attendance/', dict(data, start_date='')).status_code, 400)
        foreign = {'offering': self.sub2, 'start_date': '2026-01-01', 'end_date': '2026-01-31'}
        self.assertEqual(self.pupil.post('/student/view/attendance/', foreign).status_code, 404)

    def test_results_page_lists_own_marks_only(self):
        page = self.pupil.get('/student/view/result/')
        self.assertContains(page, '22')
        self.assertNotContains(page, str(STUD2_EXAM))
        self.assertNotContains(page, str(STUD2_TEST))

    def test_profile_opens_and_saves(self):
        from main_app.models import CustomUser
        self.assertEqual(self.pupil.get('/student/view/profile/').status_code, 200)
        response = self.pupil.post('/student/view/profile/', dict(self.profile(), profile_pic=picture()))
        self.assertEqual(response.status_code, 302)
        with tenant_context(self.college_a):
            self.assertEqual(CustomUser.objects.get(pk=self.stud1.pk).first_name, 'Stu2')

    def test_profile_password_change_works_without_a_picture(self):
        from main_app.models import CustomUser
        self.assertEqual(self.pupil.post('/student/view/profile/', self.profile(password=STRONG)).status_code, 302)
        with tenant_context(self.college_a):
            self.assertTrue(CustomUser.objects.get(pk=self.stud1.pk).check_password(STRONG))

    def test_profile_bad_data_and_failures_show_messages(self):
        bad = self.pupil.post('/student/view/profile/', self.profile(first_name=''))
        self.assertIn('Invalid Data Provided', message_texts(bad))
        with mock.patch('main_app.models.CustomUser.save', side_effect=RuntimeError('boom')):
            failed = self.pupil.post('/student/view/profile/', self.profile())
        self.assertIn('Could not update the profile.', message_texts(failed))

    def test_a_student_cannot_change_their_section_or_roll_number_from_their_profile(self):
        from main_app.models import Student
        self.pupil.post('/student/view/profile/', self.profile(section=self.sec2, roll_number='HACK-1'))
        with tenant_context(self.college_a):
            student = Student.objects.get(pk=self.stud_ids['stud1@a.in'])
            self.assertEqual((student.section_id, student.roll_number), (self.sec1, None))


@override_settings(DEBUG=True, RECAPTCHA_SITE_KEY='', RECAPTCHA_SECRET_KEY='')
class LoginViewTests(WorldTestCase):
    def visitor(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        return Client(HTTP_HOST=HOST_A)

    def test_login_page_for_a_visitor_and_redirect_for_a_member(self):
        self.assertEqual(self.visitor().get('/').status_code, 200)
        for user, home in ((self.admin, '/admin/home/'), (self.staff1, '/staff/home/'), (self.stud1, '/student/home/')):
            self.assertRedirects(self.as_user(user).get('/'), home, fetch_redirect_response=False)

    def test_a_logged_in_role_without_pages_is_logged_out_from_the_login_page(self):
        hod = self.make_user(self.college_a, 'hod2@a.in', '4', 'Head', 'Two')
        response = self.as_user(hod).get('/', follow=True)
        self.assertIn('Your role has no pages yet. Please contact your college administrator.', message_texts(response))
        self.assertEqual(response.redirect_chain, [])

    def test_each_role_lands_on_its_own_home(self):
        for email, home in (('admin@a.in', '/admin/home/'), ('staff1@a.in', '/staff/home/'), ('stud1@a.in', '/student/home/')):
            response = self.visitor().post('/doLogin/', {'email': email, 'password': PASSWORD})
            self.assertRedirects(response, home, fetch_redirect_response=False)

    def test_get_is_denied_and_wrong_password_says_invalid(self):
        self.assertContains(self.visitor().get('/doLogin/'), 'Denied')
        client = self.visitor()
        response = client.post('/doLogin/', {'email': 'stud1@a.in', 'password': 'nope-nope-nope'}, follow=True)
        self.assertIn('Invalid details', message_texts(response))

    def test_remember_me_keeps_the_session_30_days_otherwise_it_ends_with_the_browser(self):
        remembered = self.visitor()
        remembered.post('/doLogin/', {'email': 'stud1@a.in', 'password': PASSWORD, 'remember': 'on'})
        self.assertEqual(remembered.session.get_expiry_age(), 30 * 24 * 60 * 60)
        plain = self.visitor()
        plain.post('/doLogin/', {'email': 'stud1@a.in', 'password': PASSWORD})
        self.assertTrue(plain.session.get_expire_at_browser_close())

    def test_a_role_without_pages_is_turned_away_at_login_and_on_the_login_page(self):
        self.make_user(self.college_a, 'hod@a.in', '4', 'Head', 'Dept')
        response = self.visitor().post('/doLogin/', {'email': 'hod@a.in', 'password': PASSWORD}, follow=True)
        self.assertIn('Your role has no pages yet. Please contact your college administrator.', message_texts(response))

    def test_logout_needs_post_and_ends_the_session(self):
        client = self.as_user(self.stud1)
        self.assertEqual(client.get('/logout_user/').status_code, 405)
        self.assertRedirects(client.post('/logout_user/'), '/', fetch_redirect_response=False)
        self.assertEqual(client.get('/student/home/').status_code, 302)

    @override_settings(RECAPTCHA_SITE_KEY='site', RECAPTCHA_SECRET_KEY='secret')  # pragma: allowlist secret
    def test_captcha_is_checked_when_configured(self):
        good = mock.Mock(json=lambda: {'success': True})
        bad = mock.Mock(json=lambda: {'success': False})
        creds = {'email': 'stud1@a.in', 'password': PASSWORD, 'g-recaptcha-response': 'x'}
        with mock.patch('main_app.views.requests.post', return_value=good):
            self.assertRedirects(self.visitor().post('/doLogin/', creds), '/student/home/', fetch_redirect_response=False)
        with mock.patch('main_app.views.requests.post', return_value=bad):
            response = self.visitor().post('/doLogin/', creds, follow=True)
            self.assertIn('Invalid Captcha. Try Again', message_texts(response))
        with mock.patch('main_app.views.requests.post', side_effect=OSError('down')):
            response = self.visitor().post('/doLogin/', creds, follow=True)
            self.assertIn('Captcha could not be verified. Try Again', message_texts(response))

    @override_settings(DEBUG=False, STORAGES={
        'default': {'BACKEND': 'django_tenants.files.storage.TenantFileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_login_is_refused_in_production_when_the_captcha_is_not_configured(self):
        response = self.visitor().post('/doLogin/', {'email': 'stud1@a.in', 'password': PASSWORD}, follow=True)
        self.assertIn('Login is unavailable: captcha is not configured.', message_texts(response))
