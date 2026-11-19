"""Checks 15 to 19: speed with college size, nothing external, phone and print layout, the Excel download and its audit entry."""
import io
import re
import time
from pathlib import Path

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import tenant_context
from openpyxl import load_workbook

from audit_trail.models import AuditLog

from .base import GoldenCollege

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates' / 'dashboard'


def real_queries(ctx):
    return [q['sql'] for q in ctx.captured_queries if not q['sql'].startswith(('SET search_path', 'SAVEPOINT', 'RELEASE SAVEPOINT'))]


class SizeTests(GoldenCollege):
    """Check 15."""

    def add_students(self, n):
        from main_app.models import Attendance, AttendanceReport, CustomUser, Student
        with tenant_context(self.college_a):
            users = CustomUser.objects.bulk_create([CustomUser(email=f'bulk{i}@a.in', password='!', user_type='3', first_name='B',
                                                               last_name=f'S{i}') for i in range(n)])
            students = Student.objects.bulk_create([Student(admin=u, section_id=self.sec1, roll_number=f'B{i:05d}') for i, u in enumerate(users)])
            held = Attendance.objects.create(offering_id=self.sub1, date=self.today.replace(year=self.today.year - 1), period=1)
            AttendanceReport.objects.bulk_create([AttendanceReport(student=s, attendance=held, status='absent' if i % 4 == 0 else 'present')
                                                  for i, s in enumerate(students)])

    def test_the_number_of_queries_does_not_depend_on_the_number_of_students_or_guardians(self):
        client = self.as_user(self.principal)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(client.get('/dashboard/').status_code, 200)
        self.add_students(400)
        with CaptureQueriesContext(connection) as large:
            page = client.get('/dashboard/')
        self.assertEqual(page.context['shortage']['total'], 405)
        self.assertEqual(len(real_queries(large)), len(real_queries(small)))
        self.assertLessEqual(len(real_queries(large)), 30)

    def test_a_college_of_3000_students_renders_in_about_a_second(self):
        self.add_students(3000)
        client = self.as_user(self.principal)
        client.get('/dashboard/')  # warm up (templates, connection)
        start = time.monotonic()
        page = client.get('/dashboard/')
        seconds = time.monotonic() - start
        self.assertEqual(page.context['shortage']['total'], 3005)
        self.assertLess(seconds, 1.0, f'took {seconds:.2f}s')  # the acceptance budget; it takes about 0.1 s here
        print(f'\n[3,005 students] dashboard {seconds:.2f}s')


class LayoutTests(GoldenCollege):
    """Checks 16, 17 and 18 (the parts a test can see; the eyes-on part is in the PR notes)."""

    def test_the_templates_load_nothing_from_outside(self):
        for path in TEMPLATES.glob('*.html'):
            self.assertEqual(re.findall(r'https?://', path.read_text()), [], path.name)

    def test_tiles_appear_in_the_order_for_a_phone(self):
        body = self.as_user(self.principal).get('/dashboard/').content.decode()
        order = [body.index(f'id="tile-{name}"') for name in ('sections', 'attendance', 'shortage', 'consent', 'fees')]
        self.assertEqual(order, sorted(order))

    def test_it_is_built_for_a_360_px_phone_and_for_a_clean_a4_print(self):
        body = self.as_user(self.principal).get('/dashboard/').content.decode()
        self.assertIn('width=device-width, initial-scale=1', body)
        self.assertIn('@media (max-width: 576px)', body)
        self.assertIn('grid-template-columns: 1fr;', body)  # one column on a narrow screen: no sideways scrolling
        self.assertIn('@media print', body)
        self.assertIn('size: A4', body)
        self.assertIn('.erpnext-sidebar, .erpnext-navbar', body)  # the menu is hidden on paper
        self.assertIn('window.print()', body)
        self.assertIn('id="tile-fees"', body)

    def test_fees_is_a_plain_placeholder_never_a_number(self):
        body = self.as_user(self.principal).get('/dashboard/').content.decode()
        tile = body[body.index('id="tile-fees"'):]
        tile = re.sub(r'<[^>]+>', ' ', tile[:tile.index('</div>\n  </div>')])  # the words only, not tag names like h2
        self.assertIn('Not available yet', tile)
        self.assertNotRegex(tile, r'\d')


class ExportTests(GoldenCollege):
    """Check 19."""

    def workbook(self, user=None, query=''):
        response = self.as_user(user or self.principal).get('/dashboard/export/' + query)
        self.assertEqual(response.status_code, 200)
        return response, load_workbook(io.BytesIO(response.content))

    def test_the_workbook_has_the_tiles_and_the_lists(self):
        response, book = self.workbook()
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('principal_dashboard.xlsx', response['Content-Disposition'])
        self.assertEqual(book.sheetnames, ['Summary', 'Sections not marked', 'Shortage', 'Consent coverage'])
        summary = {row[0]: row[1] for row in book['Summary'].iter_rows(values_only=True) if row[0]}
        self.assertEqual(summary['Classes recorded today'], 1)
        self.assertEqual(summary['Present % today'], 66.7)
        self.assertEqual((summary['Students in shortage'], summary['Students on warning'], summary['Students condoned'],
                          summary['Students OK'], summary['Students with no classes yet']), (1, 1, 1, 1, 1))
        self.assertEqual(summary['Sections not marked today'], 2)
        self.assertEqual(summary['Fees'], 'Not available yet')
        self.assertEqual(len(list(book['Sections not marked'].iter_rows(min_row=2))), 2)
        shortage_rows = list(book['Shortage'].iter_rows(min_row=2, values_only=True))
        self.assertEqual({r[7] for r in shortage_rows}, {'shortage', 'warning', 'condoned'})
        consent = {r[0]: r for r in book['Consent coverage'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual((consent['C1'][1], consent['C1'][2]), (3, 1))

    def test_the_chosen_term_is_the_one_exported(self):
        import datetime

        from academics.models import AcademicTerm
        with tenant_context(self.college_a):
            old = AcademicTerm.objects.create(name='2024 ODD', start_date=datetime.date(2024, 6, 1), end_date=datetime.date(2024, 12, 31))
        _response, book = self.workbook(query=f'?term={old.pk}')
        self.assertEqual(book['Summary']['B1'].value, '2024 ODD')
        self.assertEqual(book['Summary']['B6'].value, 0)  # no sections with a subject in that term

    def test_text_that_looks_like_a_formula_is_shown_as_text(self):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            CustomUser.objects.filter(pk=self.stud2.pk).update(last_name='=HYPERLINK("http://evil","x")')  # shown as the name Last, First
        _response, book = self.workbook()
        from main_app.models import Student
        with tenant_context(self.college_a):
            Student.objects.filter(pk=self.stud2.student.pk).update(roll_number='=1+1')  # a roll number that is a formula
        _response, book = self.workbook()
        rolls = [r[0] for r in book['Shortage'].iter_rows(min_row=2, values_only=True)]
        self.assertIn("'=1+1", rolls)
        for sheet in book:
            for row in sheet.iter_rows(values_only=True):
                self.assertFalse([c for c in row if isinstance(c, str) and c.startswith(('=', '+', '-', '@'))], (sheet.title, row))

    def test_every_download_is_in_the_audit_log_with_who_and_which_term(self):
        self.workbook()
        self.workbook(self.admin)
        with tenant_context(self.college_a):
            entries = list(AuditLog.objects.filter(action='export').order_by('id'))
        self.assertEqual([(e.actor_email, e.actor_role) for e in entries], [('principal@a.in', '5'), ('admin@a.in', '1')])
        entry = entries[0]
        self.assertEqual((entry.model_label, entry.source, entry.request_path), ('dashboard.PrincipalDashboard', 'web', '/dashboard/export/'))
        self.assertIn('2099 ODD', entry.object_repr)
        self.assertEqual(entry.changes, {'term': [None, self.term]})

    def test_a_refused_download_leaves_no_audit_entry_and_teachers_cannot_download(self):
        for user in (self.staff1, self.stud1):
            self.assertEqual(self.as_user(user).get('/dashboard/export/').status_code, 403)
        self.assertEqual(self.as_user(self.principal).get('/dashboard/export/?term=99999').status_code, 404)
        with tenant_context(self.college_a):
            self.assertEqual(AuditLog.objects.filter(action='export').count(), 0)
