import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from openpyxl import Workbook

from main_app.tests.world import WorldTestCase

STUDENT_HEADERS = ['roll_number', 'first_name', 'last_name', 'email', 'gender', 'phone', 'programme_code',
                   'batch_start_year', 'section', 'date_of_birth', 'address']
PARENT_HEADERS = ['student_roll_number', 'name', 'relation', 'phone', 'email', 'is_primary']
STAFF_HEADERS = ['employee_id', 'first_name', 'last_name', 'email', 'gender', 'phone', 'department_code']


def xlsx_bytes(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for cells in ws.iter_rows():
        for cell in cells:
            if isinstance(cell.value, str) and cell.value.startswith(('=', '+', '-', '@')) and cell.data_type == 'f':
                cell.data_type = 's'  # a literal text cell that merely looks like a formula
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload_file(headers, rows, name='import.xlsx'):
    return SimpleUploadedFile(name, xlsx_bytes(headers, rows),
                              content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def student_row(n, **over):
    row = {'roll_number': f'21CS{n:03d}', 'first_name': f'First{n}', 'last_name': f'Last{n}', 'email': f's{n}@a.in',
           'gender': 'F', 'phone': '9876543210', 'programme_code': 'C1', 'batch_start_year': 2025, 'section': 'A',
           'date_of_birth': '2006-04-23', 'address': '1 Main Road'}
    row.update(over)
    return [row[h] for h in STUDENT_HEADERS]


class ImportTestBase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client_admin = self.as_user(self.admin)

    def students(self, rows, update=False, client=None, headers=STUDENT_HEADERS, name='import.xlsx'):
        return self.preview('students', headers, rows, update, client, name)

    def preview(self, kind, headers, rows, update=False, client=None, name='import.xlsx'):
        data = {'file': upload_file(headers, rows, name)}
        if update:
            data['update_existing'] = 'on'
        return (client or self.client_admin).post(f'/import/{kind}/', data)

    def confirm(self, resp, client=None):
        return (client or self.client_admin).post('/import/confirm/', {'token': resp.context['token']})

    def run_import(self, kind, headers, rows, update=False, client=None):
        resp = self.preview(kind, headers, rows, update, client)
        self.assertTrue(resp.context['plan'].ok, resp.context['plan'].problems)
        return self.confirm(resp, client)

    def problems(self, resp):
        return resp.context['plan'].problems

    def count(self, model):
        with tenant_context(self.college_a):
            return model.objects.count()


def zip_with(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()
