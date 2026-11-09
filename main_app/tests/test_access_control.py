"""Access control for every inherited view (see docs/SECURITY_AUDIT.md).

Covers: an explicit role policy on every endpoint, role separation inside one
college, object-level access (a student cannot read another student's data, a
staff member cannot act on another teacher's subject), CSRF, and no state
changes over GET.
"""
import json
import re

from django.urls import URLPattern, URLResolver, get_resolver
from django_tenants.utils import tenant_context

from colleges.tests.base import PASSWORD

from .world import SECRET_NOTE, STUD2_EXAM, STUD2_TEST, WorldTestCase

# Views that belong to Django itself and carry their own checks (same list as LoginCheckMiddleWare).
FRAMEWORK_MODULES = ('django.contrib.admin', 'django.contrib.auth.views', 'django.views.static')

# The ONLY pages reachable without logging in. Adding a public page means adding it here on purpose.
EXPECTED_PUBLIC_VIEWS = {'login_page', 'user_login', 'showFirebaseJS', 'accounts/login/'}


ADMIN_PREFIX = '/platform-admin/'  # the 2FA, platform-staff-only Django admin (tested in test_admin_2fa.py)


def is_framework_view(view, path=''):
    return path.startswith(ADMIN_PREFIX) or (getattr(view, '__module__', '') or '').startswith(FRAMEWORK_MODULES)


def all_routes():
    """(path with dummy ids, view callable, name) for every URL in the tenant URLconf."""
    def walk(patterns, prefix=''):
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, route)
            elif isinstance(p, URLPattern):
                yield '/' + re.sub(r'<[^>]+>', '1', route), p.callback, p.name
    return [r for r in walk(get_resolver().url_patterns) if not is_framework_view(r[1], r[0])]


class PolicyTests(WorldTestCase):
    def test_the_walk_really_covers_the_application_pages(self):
        # guards the guard: the HoD/college-admin dashboard lives under /admin/ and used to be skipped by a path prefix
        paths = {path for path, _view, _name in all_routes()}
        for expected in ('/admin/home/', '/staff/home/', '/student/home/', '/structure/', '/doLogin/'):
            self.assertIn(expected, paths)
        self.assertGreater(len(paths), 50)

    def test_only_the_expected_pages_are_public(self):
        public = {name or path.strip('/') + '/' if False else (name or path.lstrip('/'))
                  for path, view, name in all_routes() if getattr(view, 'is_public', False)}
        self.assertEqual(public, EXPECTED_PUBLIC_VIEWS)

    def test_every_endpoint_declares_its_access_policy(self):
        missing = [name or path for path, view, name in all_routes()
                   if not (getattr(view, 'allowed_roles', None) or getattr(view, 'is_public', False))]
        self.assertEqual(missing, [], 'views with no explicit @role_required / @public_view')

    def test_anonymous_users_are_sent_to_login_everywhere_except_public_pages(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        client = Client(HTTP_HOST=HOST_A)
        before = self.snapshot()
        bad = []
        for path, view, _name in all_routes():
            if getattr(view, 'is_public', False):
                continue
            for method in (client.get, client.post):
                resp = method(path)
                if resp.status_code not in (302, 403, 405):
                    bad.append((method.__name__, path, resp.status_code))
        self.assertEqual(bad, [])
        self.assertEqual(self.snapshot(), before)


class RoleSeparationTests(WorldTestCase):
    """Inside one college: staff cannot do admin things, students cannot do staff or admin things."""

    def assertDenied(self, resp):
        self.assertIn(resp.status_code, (302, 403), 'expected a redirect or 403, got %s' % resp.status_code)
        if resp.status_code == 302:
            self.assertNotIn('/add', resp['Location'])

    def test_staff_cannot_use_admin_actions(self):
        client = self.as_user(self.staff1)
        before = self.snapshot()
        attempts = [
            ('post', '/structure/departments/add/', {'name': 'Hacked', 'code': 'HX'}),
            ('post', '/staff/add', {'first_name': 'x', 'last_name': 'y', 'email': 'evil@a.in'}),
            ('post', '/student/add/', {'first_name': 'x', 'last_name': 'y', 'email': 'evil2@a.in'}),
            ('post', '/structure/subjects/add/', {'name': 'Evil', 'code': 'EV1', 'programme': self.prog1, 'regulation': self.reg1, 'semester': 1, 'credits': 3, 'kind': 'theory'}),
            ('post', '/structure/terms/add/', {'name': 'Evil term', 'start_date': '2030-01-01', 'end_date': '2031-01-01'}),
            ('get', '/structure/', None), ('get', '/structure/programmes/', None),
            ('post', '/student/delete/%d' % self.stud_ids['stud1@a.in'], {}),
            ('post', '/staff/delete/%d' % self.staff_ids['staff2@a.in'], {}),
            ('post', '/structure/programmes/%d/delete/' % self.prog1, {}),
            ('post', '/structure/offerings/%d/delete/' % self.sub2, {}),
            ('post', '/structure/departments/%d/edit/' % self.dept1, {'name': 'renamed', 'code': 'CSE'}),
            ('post', '/structure/offerings/%d/enrol/' % self.sub1, {'student': self.stud_ids['stud3@a.in']}),
            ('post', '/student/view/leave/', {'id': 1, 'status': 1}),
            ('post', '/staff/view/leave/', {'id': 1, 'status': 1}),
            ('post', '/student/view/feedback/', {'id': 1, 'reply': 'x'}),
            ('post', '/send_student_notification/', {'id': self.stud1.pk, 'message': 'x'}),
            ('post', '/send_staff_notification/', {'id': self.staff2.pk, 'message': 'x'}),
            ('get', '/student/manage/', None), ('get', '/staff/manage/', None), ('get', '/admin/home/', None),
            ('get', '/attendance/view/', None),
        ]
        for method, path, data in attempts:
            resp = getattr(client, method)(path, data) if data is not None else getattr(client, method)(path)
            self.assertDenied(resp)
        self.assertEqual(self.snapshot(), before)

    def test_students_cannot_use_staff_or_admin_actions(self):
        client = self.as_user(self.stud1)
        before = self.snapshot()
        attempts = [
            ('post', '/structure/departments/add/', {'name': 'Hacked', 'code': 'HX'}),
            ('post', '/student/delete/%d' % self.stud_ids['stud2@a.in'], {}),
            ('post', '/student/view/leave/', {'id': 1, 'status': 1}),
            ('post', '/staff/result/add/', {'student_list': self.stud_ids['stud1@a.in'], 'offering': self.sub1,
                                            'test': 100, 'exam': 100}),
            ('post', '/staff/attendance/save/', {'student_ids': json.dumps([{'id': self.stud_ids['stud1@a.in'], 'status': 1}]),
                                                 'date': '2026-02-02', 'offering': self.sub1}),
            ('get', '/staff/home/', None), ('get', '/admin/home/', None),
            ('get', '/staff/attendance/take/', None), ('get', '/student/manage/', None),
        ]
        for method, path, data in attempts:
            resp = getattr(client, method)(path, data) if data is not None else getattr(client, method)(path)
            self.assertDenied(resp)
        self.assertEqual(self.snapshot(), before)

    def test_student_cannot_edit_marks_through_the_edit_result_form(self):
        # /staff/result/edit/ lives outside the modules the old middleware guarded.
        client = self.as_user(self.stud1)
        before = self.snapshot()
        resp = client.post('/staff/result/edit/', {
            'offering': self.sub1,
            'student': self.stud_ids['stud1@a.in'], 'test': 100, 'exam': 100})
        self.assertDenied(resp)
        self.assertEqual(self.snapshot(), before)

    def test_student_cannot_list_attendance_dates_via_get_attendance(self):
        resp = self.as_user(self.stud1).post('/get_attendance', {'offering': self.sub2})
        self.assertDenied(resp)

    def test_positive_controls_each_role_reaches_its_own_pages(self):
        self.assertEqual(self.as_user(self.admin).get('/admin/home/').status_code, 200)
        self.assertEqual(self.as_user(self.staff1).get('/staff/home/').status_code, 200)
        self.assertEqual(self.as_user(self.stud1).get('/student/home/').status_code, 200)
        before = self.snapshot()
        self.as_user(self.admin).post('/structure/departments/add/', {'name': 'Real new', 'code': 'RN'})
        self.assertEqual(self.snapshot()['academics.Department'], before['academics.Department'] + 1)


class StudentDataIsolationTests(WorldTestCase):
    """A student must not read another student's data."""

    def test_result_page_shows_only_own_marks(self):
        resp = self.as_user(self.stud1).get('/student/view/result/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, str(STUD2_TEST))
        self.assertNotContains(resp, str(STUD2_EXAM))

    def test_notifications_page_shows_only_own_notifications(self):
        resp = self.as_user(self.stud1).get('/student/view/notification/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, SECRET_NOTE)
        self.assertContains(self.as_user(self.stud2).get('/student/view/notification/'), SECRET_NOTE)

    def test_attendance_query_returns_only_own_records(self):
        resp = self.as_user(self.stud1).post('/student/view/attendance/', {
            'offering': self.sub1, 'start_date': '2026-01-01', 'end_date': '2026-01-31'})
        self.assertEqual(resp.status_code, 200)
        rows = json.loads(json.loads(resp.content))
        self.assertEqual(rows, [{'date': '2026-01-05', 'status': True}])  # stud2 was absent that day

    def test_student_cannot_query_staff_data_endpoints(self):
        client = self.as_user(self.stud1)
        for path, data in (
            ('/staff/result/fetch/', {'offering': self.sub1, 'student': self.stud_ids['stud2@a.in']}),
            ('/staff/get_students/', {'offering': self.sub1}),
            ('/staff/attendance/fetch/', {'attendance_date_id': self.att1}),
        ):
            resp = client.post(path, data)
            self.assertIn(resp.status_code, (302, 403))
            self.assertNotContains(resp, 'Stu', status_code=resp.status_code)


class StaffObjectAccessTests(WorldTestCase):
    """A staff member works only on their own subjects, and only with students of that subject's course."""

    def _save_attendance(self, client, subject, student_pk, day='2026-02-02'):
        return client.post('/staff/attendance/save/', {
            'student_ids': json.dumps([{'id': student_pk, 'status': 1}]),
            'date': day, 'offering': subject})

    def test_staff_can_take_attendance_for_own_subject(self):
        before = self.snapshot()
        resp = self._save_attendance(self.as_user(self.staff1), self.sub1, self.stud_ids['stud1@a.in'])
        self.assertEqual((resp.status_code, resp.content), (200, b'OK'))
        self.assertEqual(self.snapshot()['main_app.Attendance'], before['main_app.Attendance'] + 1)

    def test_staff_cannot_take_attendance_for_another_teachers_subject(self):
        before = self.snapshot()
        resp = self._save_attendance(self.as_user(self.staff1), self.sub2, self.stud_ids['stud3@a.in'])
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_staff_cannot_mark_students_outside_the_subjects_course(self):
        before = self.snapshot()
        resp = self._save_attendance(self.as_user(self.staff1), self.sub1, self.stud_ids['stud3@a.in'])
        self.assertIn(resp.status_code, (400, 403))
        self.assertEqual(self.snapshot(), before)

    def test_staff_cannot_list_students_of_another_teachers_subject(self):
        resp = self.as_user(self.staff1).post('/staff/get_students/', {'offering': self.sub2})
        self.assertEqual(resp.status_code, 403)
        self.assertNotContains(resp, 'Three', status_code=403)

    def test_staff_cannot_read_or_change_attendance_of_another_teachers_subject(self):
        client = self.as_user(self.staff1)
        self.assertEqual(client.post('/staff/attendance/fetch/', {'attendance_date_id': self.att2}).status_code, 403)
        before = self.snapshot()
        resp = client.post('/staff/attendance/update_save/', {
            'student_ids': json.dumps([{'id': self.stud3.pk, 'status': 0}]), 'date': self.att2})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_staff_cannot_read_results_for_another_teachers_subject(self):
        resp = self.as_user(self.staff1).post('/staff/result/fetch/', {
            'offering': self.sub2, 'student': self.stud_ids['stud3@a.in']})
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_write_results_for_another_teachers_subject(self):
        before = self.snapshot()
        self.as_user(self.staff1).post('/staff/result/add/', {
            'student_list': self.stud_ids['stud3@a.in'], 'offering': self.sub2, 'test': 1, 'exam': 1})
        self.assertEqual(self.snapshot(), before)

    def test_staff_cannot_edit_results_for_another_teachers_subject(self):
        before = self.snapshot()
        self.as_user(self.staff1).post('/staff/result/edit/', {
            'offering': self.sub2,
            'student': self.stud_ids['stud3@a.in'], 'test': 1, 'exam': 1})
        self.assertEqual(self.snapshot(), before)

    def test_staff_can_edit_results_for_own_subject(self):
        self.as_user(self.staff1).post('/staff/result/edit/', {
            'offering': self.sub1,
            'student': self.stud_ids['stud1@a.in'], 'test': 51, 'exam': 52})
        self.assertIn((self.snapshot()['results'][0][0], 51, 52), [tuple(r) for r in self.snapshot()['results']])

    def test_get_attendance_is_limited_to_own_subjects_for_staff_but_open_for_the_hod(self):
        self.assertEqual(self.as_user(self.staff1).post('/get_attendance', {
            'offering': self.sub2}).status_code, 403)
        self.assertEqual(self.as_user(self.staff1).post('/get_attendance', {
            'offering': self.sub1}).status_code, 200)
        self.assertEqual(self.as_user(self.admin).post('/get_attendance', {
            'offering': self.sub2}).status_code, 200)


class CsrfTests(WorldTestCase):
    def test_every_post_without_a_csrf_token_is_rejected_for_every_role(self):
        bad = []
        for user in (self.admin, self.staff1, self.stud1):
            client = self.as_user(user, enforce_csrf_checks=True)
            for path, _view, _name in all_routes():
                if '/logout' in path:
                    continue
                resp = client.post(path, {})
                if resp.status_code != 403:
                    bad.append((user.email, path, resp.status_code))
        self.assertEqual(bad, [], 'POST accepted without a CSRF token')

    def test_login_form_requires_a_csrf_token(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        client = Client(HTTP_HOST=HOST_A, enforce_csrf_checks=True)
        self.assertEqual(client.post('/doLogin/', {'email': 'admin@a.in', 'password': 'x'}).status_code, 403)  # pragma: allowlist secret


class NoStateChangeOverGetTests(WorldTestCase):
    def test_delete_endpoints_reject_get_and_delete_nothing(self):
        before = self.snapshot()
        client = self.as_user(self.admin)
        for path in ('/student/delete/%d' % self.stud_ids['stud1@a.in'], '/staff/delete/%d' % self.staff_ids['staff1@a.in'],
                     '/structure/departments/%d/delete/' % self.dept_spare, '/structure/offerings/%d/delete/' % self.sub2):
            self.assertEqual(client.get(path).status_code, 405, path)
        self.assertEqual(self.snapshot(), before)

    def test_delete_endpoints_work_with_post(self):
        client = self.as_user(self.admin)
        before = self.snapshot()
        self.assertEqual(client.post('/structure/departments/%d/delete/' % self.dept_spare).status_code, 302)
        self.assertEqual(self.snapshot()['academics.Department'], before['academics.Department'] - 1)

    def test_logout_requires_post(self):
        client = self.as_user(self.admin)
        self.assertEqual(client.get('/logout_user/').status_code, 405)
        self.assertEqual(client.get('/admin/home/').status_code, 200)  # still logged in
        self.assertEqual(client.post('/logout_user/').status_code, 302)
        self.assertEqual(client.get('/admin/home/').status_code, 302)  # now logged out

    def test_no_get_request_changes_data_for_any_role(self):
        for user in (self.admin, self.staff1, self.stud1):
            client = self.as_user(user)  # every remaining page must render without raising
            before = self.snapshot()
            for path, _view, _name in all_routes():
                if '/logout' in path:
                    continue
                client.get(path)
            self.assertEqual(self.snapshot(), before, 'GET changed data for %s' % user.email)


class LoginTests(WorldTestCase):
    def _login(self, email, password=PASSWORD):
        from django.test import Client

        from colleges.tests.base import HOST_A
        client = Client(HTTP_HOST=HOST_A)
        client.post('/doLogin/', {'email': email, 'password': password})
        return client.get('/student/home/').status_code, client.get('/admin/home/').status_code

    def test_valid_login_reaches_own_home_only(self):
        self.assertEqual(self._login('stud1@a.in'), (200, 302))

    def test_wrong_password_is_refused(self):
        self.assertEqual(self._login('stud1@a.in', 'not-the-password'), (302, 302))

    def test_deactivated_user_cannot_log_in(self):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.stud1.pk)
            user.is_active = False
            user.save()
        self.assertEqual(self._login('stud1@a.in'), (302, 302))


class InjectionTests(WorldTestCase):
    PAYLOAD = "</script><script>window.pwned=1</script>"

    def test_student_chosen_name_cannot_inject_script_into_the_hod_dashboard(self):
        # students can edit their own first name; it is printed into the principal's dashboard script
        self.make_user(self.college_a, 'xss@a.in', '3', self.PAYLOAD, 'X')
        resp = self.as_user(self.admin).get('/admin/home/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self.PAYLOAD)
        self.assertContains(resp, '\\u003c/script\\u003e')

    def test_uploads_use_the_per_college_storage_not_a_shared_folder(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = [p.name for p in root.glob('*.py') if 'FileSystemStorage()' in p.read_text()]
        self.assertEqual(offenders, [])


class LibraryRemovedTests(WorldTestCase):
    """The library module is out of pilot scope: no URLs, no menu entries."""

    PATHS = ('/staff/addbook/', '/staff/issue_book/', '/staff/view_issued_book/', '/student/viewbooks/')

    def test_library_urls_no_longer_exist(self):
        from django.urls import NoReverseMatch, reverse
        for name in ('add_book', 'issue_book', 'view_issued_book', 'view_books'):
            with self.assertRaises(NoReverseMatch):
                reverse(name)
        for user in (self.staff1, self.stud1, self.admin):
            client = self.as_user(user)
            for path in self.PATHS:
                self.assertEqual(client.get(path).status_code, 404, (user.email, path))

    def test_no_menu_or_dashboard_mentions_the_library(self):
        for user, home in ((self.admin, '/admin/home/'), (self.staff1, '/staff/home/'), (self.stud1, '/student/home/')):
            resp = self.as_user(user).get(home)
            self.assertEqual(resp.status_code, 200)
            for word in ('Library', 'Add Books', 'Available Books'):
                self.assertNotContains(resp, word)
