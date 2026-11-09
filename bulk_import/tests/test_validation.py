"""Checks 1-5, 16, C7: file safety, row validation, cleaners (nothing is saved at this stage)."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django_tenants.utils import tenant_context

from bulk_import import parsing
from main_app.models import CustomUser, Student

from .helpers import STUDENT_HEADERS, ImportTestBase, student_row, upload_file, xlsx_bytes, zip_with


class FileSafetyTests(ImportTestBase):
    def refused(self, upload, text):
        before = self.count(CustomUser)
        resp = self.client_admin.post('/import/students/', {'file': upload}, follow=True)
        self.assertContains(resp, text)
        self.assertEqual(self.count(CustomUser), before)

    def test_only_xlsx_is_accepted(self):
        data = xlsx_bytes(STUDENT_HEADERS, [student_row(1)])
        for name in ('list.xls', 'list.xlsm', 'list.csv', 'list.xlsx.exe'):
            self.refused(SimpleUploadedFile(name, data), 'Only .xlsx')
        self.refused(SimpleUploadedFile('fake.xlsx', b'name,roll\nx,1\n'), 'not a valid .xlsx')

    def test_macro_workbooks_and_zip_bombs_are_refused(self):
        base = {'[Content_Types].xml': '<x/>', 'xl/workbook.xml': '<x/>'}
        self.refused(SimpleUploadedFile('m.xlsx', zip_with({**base, 'xl/vbaProject.bin': 'x'})), 'macros')
        self.refused(SimpleUploadedFile('b.xlsx', zip_with({**base, 'xl/big.xml': 'A' * (70 * 1024 * 1024)})), 'unsafe size')

    def test_size_and_row_limits(self):
        self.assertEqual(parsing.MAX_ROWS, 2000)
        rows = [student_row(n, roll_number=f'R{n}', email=f'e{n}@a.in') for n in range(1, 2002)]
        self.refused(upload_file(STUDENT_HEADERS, rows), 'limit is 2000')
        big = SimpleUploadedFile('big.xlsx', b'PK' + b'0' * (5 * 1024 * 1024 + 1))
        self.refused(big, 'larger than 5 MB')

    def test_empty_and_header_only_sheets(self):
        self.refused(upload_file(STUDENT_HEADERS, []), 'no data rows')


class RowValidationTests(ImportTestBase):
    def test_a_clean_file_has_no_problems_and_saves_nothing(self):
        before = self.count(CustomUser)
        resp = self.students([student_row(1), student_row(2)])
        self.assertTrue(resp.context['plan'].ok)
        self.assertEqual(resp.context['counts'], (2, 0, 0))
        self.assertEqual(self.count(CustomUser), before)

    def test_each_kind_of_mistake_is_reported_with_row_column_and_reason(self):
        rows = [
            student_row(1, email='not-an-email'),
            student_row(2, gender='X'),
            student_row(3, phone='12345'),
            student_row(4, first_name='x' * 200),
            student_row(5, section='Z'),
            student_row(6, programme_code='NOPE'),
            student_row(7, batch_start_year='abc'),
            student_row(8, date_of_birth='2999-01-01'),
            student_row(9, roll_number=''),
            student_row(10, first_name=''),
            student_row(11, email='stud1@a.in'),        # belongs to an existing user
        ]
        found = {(r, c) for r, c, _ in self.problems(self.students(rows))}
        expected = {(2, 'email'), (3, 'gender'), (4, 'phone'), (5, 'first_name'), (6, 'section'), (7, 'section'),
                    (8, 'batch_start_year'), (9, 'date_of_birth'), (10, 'roll_number'), (11, 'first_name'), (12, 'email')}
        self.assertTrue(expected <= found, expected - found)

    def test_missing_required_column_is_reported(self):
        headers = [h for h in STUDENT_HEADERS if h != 'email']
        rows = [[v for h, v in zip(STUDENT_HEADERS, student_row(1), strict=True) if h != 'email']]
        self.assertIn((1, 'email', 'column is missing'), self.problems(self.students(rows, headers=headers)))

    def test_duplicates_inside_the_file_are_reported_case_insensitively(self):
        rows = [student_row(1), student_row(2, roll_number=' 21cs001 '), student_row(3, email='S1@A.IN')]
        found = {(r, c) for r, c, _ in self.problems(self.students(rows))}
        self.assertIn((3, 'roll_number'), found)
        self.assertIn((4, 'email'), found)

    def test_existing_records_are_errors_in_create_mode(self):
        self.run_import('students', STUDENT_HEADERS, [student_row(1)])
        again = self.students([student_row(1)])
        self.assertEqual([(r, c) for r, c, _ in self.problems(again)], [(2, 'roll_number')])
        self.assertFalse(again.context['plan'].ok)

    def test_unknown_columns_are_ignored_with_a_warning(self):
        resp = self.students([student_row(1) + ['x']], headers=STUDENT_HEADERS + ['shoe_size'])
        self.assertTrue(resp.context['plan'].ok)
        self.assertIn('shoe_size', [c for _, c, _ in resp.context['plan'].warnings])


class CleanerTests(ImportTestBase):
    def test_phone_normalisation(self):
        for raw, good in (('9876543210', '+919876543210'), ('98765 43210', '+919876543210'), ('+91 98765-43210', '+919876543210'),
                          ('919876543210', '+919876543210'), ('09876543210', '+919876543210'), ('(98765) 43210', '+919876543210')):
            self.assertEqual(parsing.normalize_phone(raw), good, raw)
        for bad in ('12345', '5876543210', '+44 7911 123456', 'abcdefghij', '98765432101'):
            self.assertIsNone(parsing.normalize_phone(bad), bad)

    def test_dates_numbers_and_text_cells(self):
        import datetime
        self.assertEqual(parsing.clean_dob('23/04/2006')[0], datetime.date(2006, 4, 23))
        self.assertEqual(parsing.clean_dob('23-04-2006')[0], datetime.date(2006, 4, 23))
        self.assertIsNotNone(parsing.clean_dob('31/02/2006')[1])
        self.assertEqual(parsing._text(2101234.0), '2101234')   # Excel's number cell
        self.assertEqual(parsing._text(datetime.datetime(2006, 4, 23)), '2006-04-23')
        self.assertEqual(parsing.clean_email('  A@B.in ')[0], 'a@b.in')

    def test_formula_looking_text_is_kept_as_plain_text_and_never_evaluated(self):
        rows = [student_row(1, first_name='=1+1', last_name='@SUM(A1)', address='-cmd|calc')]
        self.run_import('students', STUDENT_HEADERS, rows)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='s1@a.in')
            self.assertEqual((user.first_name, user.last_name, user.address), ('=1+1', '@SUM(A1)', '-cmd|calc'))

    def test_real_formula_cells_are_never_evaluated(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(STUDENT_HEADERS)
        ws.append(student_row(1, email='=HYPERLINK("http://evil.example","x")'))  # a true formula cell (no cached value)
        buf = io.BytesIO()
        wb.save(buf)
        resp = self.client_admin.post('/import/students/', {'file': SimpleUploadedFile('f.xlsx', buf.getvalue())})
        self.assertIn((2, 'email', 'is required'), self.problems(resp))  # no cached value, so nothing to import
        self.assertEqual(self.count(CustomUser), 6)

    def test_numeric_roll_numbers_are_read_as_text(self):
        self.run_import('students', STUDENT_HEADERS, [student_row(1, roll_number=2101234)])
        with tenant_context(self.college_a):
            self.assertTrue(Student.objects.filter(roll_number='2101234').exists())


class RollNumberTests(ImportTestBase):
    """C7: trimmed and unique ignoring case, enforced by the database."""

    def test_the_database_refuses_case_variants(self):
        with tenant_context(self.college_a):
            first, second = Student.objects.filter(admin__email__startswith='stud')[:2]
            first.roll_number = ' 21A001 '
            first.save()
            self.assertEqual(Student.objects.get(pk=first.pk).roll_number, '21A001')  # trimmed
            second.roll_number = '21a001'
            with self.assertRaises(IntegrityError), transaction.atomic():
                second.save()

    def test_blank_roll_numbers_never_collide(self):
        with tenant_context(self.college_a):
            for student in Student.objects.all():
                student.roll_number = '   '
                student.save()
            self.assertEqual(Student.objects.filter(roll_number__isnull=True).count(), Student.objects.count())

    def test_the_single_add_form_rules_match_the_database(self):
        from django.core.exceptions import ValidationError
        with tenant_context(self.college_a):
            a, b = Student.objects.all()[:2]
            a.roll_number = '21A001'
            a.save()
            b.roll_number = '21a001'
            with self.assertRaises(ValidationError):
                b.validate_constraints()

    def test_employee_ids_follow_the_same_rule(self):
        from main_app.models import Staff
        with tenant_context(self.college_a):
            s1, s2 = Staff.objects.all()[:2]
            s1.employee_id = 'E100'
            s1.save()
            s2.employee_id = 'e100'
            with self.assertRaises(IntegrityError), transaction.atomic():
                s2.save()
