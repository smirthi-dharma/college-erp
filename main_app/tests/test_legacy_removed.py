"""Leave, feedback, in-app notifications and Firebase push were removed (WhatsApp covers parent alerts).

These tests keep them gone: the old URLs answer 404 for every role, no page of any role links to them, the tables
and the push token column are gone, and the dead templates and root-level scratch files stay deleted.
"""
import re
from pathlib import Path

from django.apps import apps
from django.db import connection
from django_tenants.utils import tenant_context

from .world import WorldTestCase

BASE_DIR = Path(__file__).resolve().parents[2]

REMOVED_PATHS = [
    '/firebase-messaging-sw.js',
    '/send_student_notification/', '/send_staff_notification/', '/admin_notify_student', '/admin_notify_staff',
    '/student/view/feedback/', '/staff/view/feedback/', '/student/view/leave/', '/staff/view/leave/',
    '/staff/apply/leave/', '/staff/feedback/', '/staff/fcmtoken/', '/staff/view/notification/',
    '/student/apply/leave/', '/student/feedback/', '/student/fcmtoken/', '/student/view/notification/',
]


class RemovedUrlsTests(WorldTestCase):
    def test_every_removed_url_is_404_for_every_role_and_for_nobody_logged_in(self):
        from django.test import Client

        from colleges.tests.base import HOST_A
        clients = {'anonymous': Client(HTTP_HOST=HOST_A)}
        for name, user in (('admin', self.admin), ('staff', self.staff1), ('student', self.stud1)):
            clients[name] = self.as_user(user)
        for who, client in clients.items():
            for path in REMOVED_PATHS:
                for method in ('get', 'post'):
                    response = getattr(client, method)(path)
                    self.assertEqual(response.status_code, 404, f'{who} {method.upper()} {path}')

    def test_no_page_links_to_a_removed_url(self):
        pages = {'admin': (self.admin, ['/admin/home/', '/student/manage/', '/staff/manage/', '/attendance/view/',
                                        '/admin_view_profile', '/structure/']),
                 'staff': (self.staff1, ['/staff/home/', '/staff/view/profile/', '/staff/result/add/']),
                 'student': (self.stud1, ['/student/home/', '/student/view/profile/', '/student/view/result/'])}
        for who, (user, paths) in pages.items():
            client = self.as_user(user)
            for path in paths:
                response = client.get(path)
                self.assertEqual(response.status_code, 200, f'{who} {path}')
                html = response.content.decode()
                links = set(re.findall(r'(?:href|src|action)="(/[^"#?]*)', html))
                links |= set(re.findall(r'''url:\s*["'](/[^"']+)''', html))
                for removed in REMOVED_PATHS:
                    self.assertNotIn(removed.rstrip('/'), {link.rstrip('/') for link in links}, f'{who} {path} links to {removed}')
                for word in ('Leave', 'Feedback', 'firebase', 'fcmtoken'):
                    self.assertNotIn(word, html, f'{who} {path} still mentions {word}')


class RemovedDataTests(WorldTestCase):
    def test_the_tables_and_the_push_token_column_are_gone(self):
        names = {m.__name__ for m in apps.get_app_config('main_app').get_models()}
        for gone in ('LeaveReportStudent', 'LeaveReportStaff', 'FeedbackStudent', 'FeedbackStaff',
                     'NotificationStudent', 'NotificationStaff'):
            self.assertNotIn(gone, names)
        with tenant_context(self.college_a), connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            columns = {c.name for c in connection.introspection.get_table_description(cursor, 'main_app_customuser')}
        self.assertFalse({t for t in tables if 'leavereport' in t or 'feedback' in t or 'notification' in t})
        self.assertNotIn('fcm_token', columns)


class RemovedFilesTests(WorldTestCase):
    def test_dead_templates_and_scratch_files_stay_deleted(self):
        templates = BASE_DIR / 'main_app' / 'templates'
        self.assertEqual(sorted(p.name for p in templates.rglob('*erpnext*')), [])
        for dead in ('preview.html', 'upload.html', 'main_app/notifications.py', 'main_app/context_processors.py'):
            self.assertFalse((BASE_DIR / dead).exists(), dead)
        for folder in ('hod_template', 'staff_template', 'student_template'):
            names = {p.name for p in (templates / folder).glob('*.html')}
            self.assertFalse({n for n in names if re.search('leave|feedback|notification', n)}, folder)

    def test_no_python_code_mentions_firebase_or_the_push_settings(self):
        offenders = []
        for path in BASE_DIR.rglob('*.py'):
            if '.venv' in path.parts or 'migrations' in path.parts or path.name == 'test_legacy_removed.py':
                continue
            text = path.read_text(errors='ignore')
            if re.search(r'firebase|FCM_|fcm_token', text, re.I):
                offenders.append(str(path.relative_to(BASE_DIR)))
        self.assertEqual(offenders, [])
