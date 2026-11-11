"""The menu each role sees: the right links, once, and none that lead to a 403."""
from django.urls import reverse

from .world import WorldTestCase


class SidebarTests(WorldTestCase):
    def sidebar(self, user, url_name):
        r = self.as_user(user).get(reverse(url_name))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_college_admin_menu_has_student_360_and_the_report_links(self):
        body = self.sidebar(self.admin, 'admin_home')
        self.assertEqual(body.count('>Student 360<'), 1)
        self.assertIn(reverse('student_list'), body)
        self.assertIn(reverse('shortage_report'), body)

    def test_teacher_menu_is_the_teacher_menu_only(self):
        body = self.sidebar(self.staff1, 'staff_home')
        self.assertEqual(body.count('>Student 360<'), 1)
        self.assertEqual(body.count('>Take Attendance<'), 1)
        for admin_only in (reverse('admin_notify_staff'), reverse('admin_notify_student'), reverse('view_staff_leave'),
                           reverse('student_feedback_message'), reverse('shortage_report')):
            self.assertNotIn(f'href="{admin_only}"', body)

    def test_every_link_in_each_menu_is_allowed_for_that_role(self):
        import re
        for user, home in ((self.admin, 'admin_home'), (self.staff1, 'staff_home'), (self.stud1, 'student_home')):
            body = self.sidebar(user, home)
            menu = body[body.index('sidebar-user'):]
            links = {h for h in re.findall(r'href="(/[^"#]*)"', menu) if not h.startswith(('/static', '/media'))}
            client = self.as_user(user)
            for link in sorted(links):
                if 'logout' in link:
                    continue
                status = client.get(link).status_code
                self.assertNotIn(status, (403, 404, 500), f'{user.email}: {link} -> {status}')

    def test_student_menu_labels_are_distinct(self):
        body = self.sidebar(self.stud1, 'student_home')
        self.assertEqual(body.count('>Attendance summary<'), 1)
        self.assertEqual(body.count('>Attendance by date<'), 1)
        self.assertNotIn('>View Attendance<', body)
        self.assertNotIn('>My attendance<', body)
        self.assertEqual(body.count('>My 360 profile<'), 1)
