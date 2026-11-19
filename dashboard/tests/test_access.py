"""Checks 1 to 5: who may open what, read-only for Management, Student 360 with its access log, and managing Management users."""
from django.test import Client, override_settings
from django_tenants.utils import tenant_context

from audit_trail.models import AuditLog
from colleges.tests.base import HOST_A, PASSWORD
from main_app.tests.helpers import STRONG
from main_app.tests.test_access_control import all_routes
from student360.models import StudentAccessLog

from .base import GoldenCollege

# Everything Management may open. A new page is closed to Management until it is added here on purpose.
MAY_OPEN = {'principal_dashboard', 'principal_export', 'student_list', 'student_detail', 'shortage_report', 'profile_photo', 'user_logout'}


class ReadOnlyTests(GoldenCollege):
    """Check 1."""

    def test_management_can_open_what_it_should(self):
        client = self.as_user(self.principal)
        pk = self.stud_ids['stud1@a.in']
        for path in ('/dashboard/', '/dashboard/export/', '/students/', f'/students/{pk}/', '/attendance/shortage/',
                     '/attendance/shortage/?format=xlsx', f'/attendance/shortage/?term={self.term}'):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_every_other_page_of_the_application_is_403_for_management_and_changes_nothing(self):
        client = self.as_user(self.principal)
        before = self.snapshot_all()
        checked, wrong = 0, []
        for path, view, name in all_routes():
            if getattr(view, 'is_public', False) or name in MAY_OPEN:
                continue
            for method in ('get', 'post'):
                response = getattr(client, method)(path)
                checked += 1
                if response.status_code != 403:
                    wrong.append((method.upper(), path, name, response.status_code))
        self.assertGreater(checked, 120, 'the walk must cover the whole application')
        self.assertEqual(wrong, [], 'Management must get 403 on every page that is not in MAY_OPEN')
        self.assertEqual(self.snapshot_all(), before, 'nothing may change')

    def test_the_list_of_pages_management_may_open_is_real(self):
        names = {name for _path, _view, name in all_routes()}
        self.assertTrue(MAY_OPEN <= names, MAY_OPEN - names)

    def test_posting_to_the_pages_management_may_read_is_refused_or_harmless(self):
        client = self.as_user(self.principal)
        before = self.snapshot_all()
        for path in ('/dashboard/', '/dashboard/export/', '/students/', f'/students/{self.stud_ids["stud1@a.in"]}/'):
            self.assertEqual(client.post(path, {'x': '1'}).status_code, 405, path)
        self.assertEqual(self.snapshot_all(), before)

    def test_management_can_sign_out(self):
        client = self.as_user(self.principal)
        self.assertEqual(client.post('/logout_user/').status_code, 302)
        self.assertEqual(client.get('/dashboard/').status_code, 302)  # signed out: back to the login page


class WhoElseTests(GoldenCollege):
    """Check 2."""

    def test_teacher_and_student_get_403_and_a_visitor_goes_to_the_login_page(self):
        for user in (self.staff1, self.stud1):
            client = self.as_user(user)
            for path in ('/dashboard/', '/dashboard/export/', '/management/users/', '/management/users/add/'):
                self.assertEqual(client.get(path).status_code, 403, (user.email, path))
        visitor = Client(HTTP_HOST=HOST_A)
        for path in ('/dashboard/', '/dashboard/export/', '/management/users/'):
            response = visitor.get(path)
            self.assertEqual((response.status_code, response['Location']), (302, '/'), path)

    def test_the_college_admin_can_open_the_dashboard_and_export(self):
        client = self.as_user(self.admin)
        self.assertEqual(client.get('/dashboard/').status_code, 200)
        self.assertEqual(client.get('/dashboard/export/').status_code, 200)

    def test_management_lands_on_the_dashboard_after_signing_in(self):
        with override_settings(DEBUG=True, RECAPTCHA_SITE_KEY='', RECAPTCHA_SECRET_KEY=''):
            response = Client(HTTP_HOST=HOST_A).post('/doLogin/', {'email': 'principal@a.in', 'password': PASSWORD})
        self.assertEqual((response.status_code, response['Location']), (302, '/dashboard/'))
        self.assertEqual(self.as_user(self.principal).get('/').status_code, 302)  # the login page sends a signed-in user home
        self.assertEqual(self.as_user(self.principal).get('/')['Location'], '/dashboard/')

    def test_the_menu_has_exactly_the_pages_management_may_open(self):
        import re
        body = self.as_user(self.principal).get('/dashboard/').content.decode()
        menu = body[body.index('sidebar-user'):]
        links = {h for h in re.findall(r'href="(/[^"#?]*)"', menu) if not h.startswith(('/static', '/media'))}
        self.assertEqual(links, {'/dashboard/', '/students/', '/attendance/shortage/'})
        client = self.as_user(self.principal)
        for link in sorted(links):
            if 'logout' in link:
                continue
            self.assertEqual(client.get(link).status_code, 200, link)
        for admin_only in ('/management/users/', '/structure/', '/import/', '/messaging/', '/staff/manage/'):
            self.assertNotIn(f'href="{admin_only}"', menu)


class Student360Tests(GoldenCollege):
    """Check 4."""

    def page(self, viewer, user, **kw):
        return self.as_user(viewer).get(f'/students/{self.stud_ids[user.email]}/', **kw)

    def test_management_sees_the_full_student_page_like_the_college_admin(self):
        with tenant_context(self.college_a):
            from main_app.models import CustomUser
            CustomUser.objects.filter(pk=self.stud1.pk).update(address='9 Secret Street')
        mine = self.page(self.principal, self.stud1).content.decode()
        theirs = self.page(self.admin, self.stud1).content.decode()
        for private in ('9 Secret Street', '+919000000001', 'G One', 'Date of birth', 'WhatsApp consent'):
            self.assertIn(private, mine, private)
        for needed in ('Attendance', 'Subjects this term', 'Guardians'):
            self.assertIn(needed, mine)
        self.assertEqual(self.page(self.principal, self.stud1).status_code, 200)
        for section in ('Identity', 'Academics', 'Attendance', 'Guardians'):  # the same sections the college admin sees
            self.assertEqual(section in mine, section in theirs, section)

    def test_it_is_read_only(self):
        body = self.page(self.principal, self.stud2).content.decode()
        for admin_link in ('/student/edit/', '/attendance/condonation', '/attendance/on-duty', '/student/delete/'):
            self.assertNotIn(admin_link, body)
        self.assertEqual(self.as_user(self.principal).post(f'/students/{self.stud_ids["stud1@a.in"]}/').status_code, 405)

    def test_every_view_is_in_the_access_log_with_the_role(self):
        pk = self.stud_ids['stud3@a.in']
        with tenant_context(self.college_a):
            before = StudentAccessLog.objects.count()
        self.page(self.principal, self.stud3)
        self.page(self.principal, self.stud3)
        with tenant_context(self.college_a):
            entries = list(StudentAccessLog.objects.filter(student_id=pk, viewer_email='principal@a.in'))
            self.assertEqual(StudentAccessLog.objects.count(), before + 2)
        self.assertEqual({(e.viewer_role, e.outcome) for e in entries}, {('5', 'allowed')})

    def test_the_search_list_works_and_an_unknown_student_is_404(self):
        client = self.as_user(self.principal)
        page = client.get('/students/?q=Fifth')
        self.assertContains(page, 'Fifth')
        self.assertNotContains(page, 'Fourth')
        self.assertEqual(client.get('/students/999999/').status_code, 404)

    def test_other_roles_are_unchanged(self):
        self.assertEqual(self.page(self.staff2, self.stud1).status_code, 403)  # a teacher still cannot open other teachers' students
        with tenant_context(self.college_a):
            refused = StudentAccessLog.objects.filter(viewer_email='staff2@a.in', outcome='refused').count()
        self.assertEqual(refused, 1)
        self.assertEqual(self.page(self.stud1, self.stud2).status_code, 403)


class ManagementUsersTests(GoldenCollege):
    """Check 5."""

    def data(self, **extra):
        data = {'first_name': 'Vice', 'last_name': 'Principal', 'email': 'Vice.Principal@A.in', 'phone': '98765 43210', 'password': STRONG}
        data.update(extra)
        return data

    def user(self, email):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            return CustomUser.objects.get(email=email)

    def test_the_college_admin_can_add_several_management_users(self):
        client = self.as_user(self.admin)
        for email in ('vp1@a.in', 'vp2@a.in', 'vp3@a.in'):
            response = client.post('/management/users/add/', self.data(email=email))
            self.assertEqual((response.status_code, response['Location']), (302, '/management/users/'))
        created = self.user('vp1@a.in')
        self.assertEqual((created.user_type, created.phone, created.is_active), ('5', '+919876543210', True))
        self.assertTrue(created.check_password(STRONG))
        listing = client.get('/management/users/').content.decode()
        for email in ('vp1@a.in', 'vp2@a.in', 'vp3@a.in', 'principal@a.in'):
            self.assertIn(email, listing)
        self.assertNotIn('staff1@a.in', listing)  # only Management users

    def test_bad_input_is_refused(self):
        client = self.as_user(self.admin)
        for bad in ({'password': '1234'}, {'email': 'principal@a.in'}, {'email': 'PRINCIPAL@a.in'}, {'email': 'staff1@a.in'},
                    {'phone': '12345'}, {'first_name': ''}, {'password': ''}):
            response = client.post('/management/users/add/', self.data(**bad))
            self.assertEqual(response.status_code, 200, bad)
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            self.assertFalse(CustomUser.objects.filter(email='vice.principal@a.in').exists())

    def test_editing_keeps_the_password_unless_a_new_one_is_given(self):
        client = self.as_user(self.admin)
        client.post('/management/users/add/', self.data(email='vp@a.in'))
        pk = self.user('vp@a.in').pk
        client.post(f'/management/users/{pk}/edit/', self.data(email='vp@a.in', first_name='Renamed', password='', phone=''))
        edited = self.user('vp@a.in')
        self.assertEqual((edited.first_name, edited.phone), ('Renamed', ''))
        self.assertTrue(edited.check_password(STRONG))
        client.post(f'/management/users/{pk}/edit/', self.data(email='vp@a.in', password='Another-Strong-pw-77'))  # pragma: allowlist secret
        self.assertTrue(self.user('vp@a.in').check_password('Another-Strong-pw-77'))
        self.assertEqual(client.post(f'/management/users/{pk}/edit/', self.data(email='staff1@a.in')).status_code, 200)  # taken

    def test_only_management_users_can_be_edited_here(self):
        client = self.as_user(self.admin)
        for other in (self.staff1, self.stud1, self.admin):
            self.assertEqual(client.get(f'/management/users/{other.pk}/edit/').status_code, 404)
            self.assertEqual(client.post(f'/management/users/{other.pk}/active/', {'active': '0'}).status_code, 404)

    def test_a_deactivated_user_cannot_sign_in_and_loses_an_open_session(self):
        client = self.as_user(self.admin)
        client.post('/management/users/add/', self.data(email='vp@a.in'))
        vp = self.user('vp@a.in')
        sitting = self.as_user(vp)
        self.assertEqual(sitting.get('/dashboard/').status_code, 200)
        client.post(f'/management/users/{vp.pk}/active/', {'active': '0'})
        self.assertFalse(self.user('vp@a.in').is_active)
        with override_settings(DEBUG=True, RECAPTCHA_SITE_KEY='', RECAPTCHA_SECRET_KEY=''):
            fresh = Client(HTTP_HOST=HOST_A)
            fresh.post('/doLogin/', {'email': 'vp@a.in', 'password': STRONG})
            self.assertEqual(fresh.get('/dashboard/').status_code, 302)  # still not signed in
        self.assertEqual(sitting.get('/dashboard/').status_code, 302, 'the session that was already open stops working too')
        client.post(f'/management/users/{vp.pk}/active/', {'active': '1'})
        self.assertTrue(self.user('vp@a.in').is_active)

    def test_management_cannot_create_or_change_users(self):
        client = self.as_user(self.principal)
        before = self.snapshot_all()
        pk = self.principal.pk
        for method, path in (('get', '/management/users/'), ('post', '/management/users/add/'), ('post', f'/management/users/{pk}/edit/'),
                             ('post', f'/management/users/{pk}/active/')):
            self.assertEqual(getattr(client, method)(path, self.data(email='x@a.in', active='0')).status_code, 403, path)
        self.assertEqual(self.snapshot_all(), before)
        self.assertTrue(self.user('principal@a.in').is_active)

    def test_every_change_is_in_the_audit_log_with_who_did_it(self):
        client = self.as_user(self.admin)
        client.post('/management/users/add/', self.data(email='vp@a.in'))
        pk = self.user('vp@a.in').pk
        client.post(f'/management/users/{pk}/edit/', self.data(email='vp@a.in', first_name='Changed', password=''))
        client.post(f'/management/users/{pk}/active/', {'active': '0'})
        with tenant_context(self.college_a):
            entries = list(AuditLog.objects.filter(model_label='main_app.CustomUser', object_pk=str(pk), actor_email='admin@a.in')
                           .order_by('id'))
        self.assertEqual([e.action for e in entries], ['create', 'update', 'update'])
        self.assertIn('is_active', entries[2].changes)
        dumped = str([e.changes for e in entries])
        self.assertNotIn(STRONG, dumped)
        self.assertNotIn('vp@a.in', dumped)  # personal data is masked in the log
