"""Acceptance checks 1-18 of docs/ACADEMICS_ACCEPTANCE.md."""
import io
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django_tenants.utils import tenant_context
from openpyxl import Workbook

from academics import services
from academics.models import (
    AcademicTerm,
    Batch,
    Department,
    Enrolment,
    Programme,
    Regulation,
    Section,
    Subject,
    SubjectOffering,
)
from audit_trail.models import AuditLog
from main_app.models import AttendanceReport, StudentResult
from main_app.tests.world import WorldTestCase


def xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class ModelRuleTests(WorldTestCase):
    """1-4: database-level uniqueness, cross-link validation, protection, ranges."""

    def test_unique_rules_are_enforced_by_the_database(self):
        with tenant_context(self.college_a):
            cases = [
                lambda: Department.objects.create(name='Dup', code='CSE'),
                lambda: Programme.objects.create(department_id=self.dept1, name='x', code='C1', duration_semesters=8),
                lambda: Regulation.objects.create(programme_id=self.prog1, name='R2025', effective_from=date(2025, 1, 1)),
                lambda: Batch.objects.create(programme_id=self.prog1, regulation_id=self.reg1, start_year=2025),
                lambda: Section.objects.create(batch_id=self.batch1, name='A'),
                lambda: Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, semester=2,
                                               code='MA101', name='dup', credits=1),
                lambda: AcademicTerm.objects.create(name='2099 ODD', start_date=date(2025, 1, 1), end_date=date(2025, 2, 1)),
                lambda: SubjectOffering.objects.create(subject_id=self.subject1, section_id=self.sec1,
                                                       term_id=self.term, faculty=self.staff2.staff),
                lambda: Enrolment.objects.create(offering_id=self.sub1, student=self.stud1.student),
            ]
            for number, make in enumerate(cases):
                with self.assertRaises(IntegrityError, msg=f'case {number}'), transaction.atomic():
                    make()

    def test_cross_links_are_validated(self):
        with tenant_context(self.college_a):
            bad_batch = Batch(programme_id=self.prog1, regulation_id=self.reg2, start_year=2030)
            with self.assertRaises(ValidationError):
                bad_batch.full_clean()
            late = Subject(programme_id=self.prog1, regulation_id=self.reg1, semester=9, code='X9', name='x', credits=1)
            with self.assertRaises(ValidationError) as ctx:
                late.full_clean()
            self.assertIn('semester', ctx.exception.message_dict)
            wrong_reg = SubjectOffering(subject=Subject.objects.get(pk=self.subject1), section_id=self.sec2,
                                        term_id=self.term, faculty=self.staff1.staff)
            with self.assertRaises(ValidationError):
                wrong_reg.full_clean()

    def test_numbers_are_range_checked_by_the_database(self):
        with tenant_context(self.college_a):
            for kwargs in ({'credits': -1, 'semester': 1}, {'credits': 1, 'semester': 0}, {'credits': 1, 'semester': 13}):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, code='Z' + str(kwargs['semester']),
                                           name='z', **kwargs)

    def test_referenced_records_cannot_be_deleted(self):
        with tenant_context(self.college_a):
            for model, pk in ((Department, self.dept1), (Programme, self.prog1), (Regulation, self.reg1),
                              (Batch, self.batch1), (Section, self.sec1), (Subject, self.subject1),
                              (SubjectOffering, self.sub1)):
                with self.assertRaises(ProtectedError, msg=model.__name__), transaction.atomic():
                    model.objects.get(pk=pk).delete()

    def test_forms_show_cross_link_errors_and_save_nothing(self):
        before = self.snapshot()['academics.Batch']
        resp = self.as_user(self.admin).post('/structure/batches/add/', {
            'programme': self.prog1, 'regulation': self.reg2, 'start_year': 2031})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'different programme')
        self.assertEqual(self.snapshot()['academics.Batch'], before)


class EnrolmentRuleTests(WorldTestCase):
    """13-18."""

    def enrolments(self, offering_pk, **filters):
        with tenant_context(self.college_a):
            return list(Enrolment.objects.filter(offering_id=offering_pk, **filters).values_list('student__admin__email', flat=True))

    def test_core_offering_enrols_the_whole_section_automatically(self):
        self.assertCountEqual(self.enrolments(self.sub1), ['stud1@a.in', 'stud2@a.in'])
        self.assertCountEqual(self.enrolments(self.sub2), ['stud3@a.in'])
        with tenant_context(self.college_a):
            self.assertEqual(set(Enrolment.objects.values_list('source', flat=True)), {'auto'})

    def test_elective_offering_enrols_nobody(self):
        with tenant_context(self.college_a):
            elective = SubjectOffering.objects.create(
                subject=Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, semester=1,
                                               code='EL1', name='Elective', credits=3),
                section_id=self.sec1, term_id=self.term, faculty=self.staff1.staff, kind='elective')
        self.assertEqual(self.enrolments(elective.pk), [])

    def test_hod_can_assign_an_elective_to_a_student(self):
        with tenant_context(self.college_a):
            elective = SubjectOffering.objects.create(
                subject=Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, semester=1,
                                               code='EL2', name='Elective 2', credits=3),
                section_id=self.sec1, term_id=self.term, faculty=self.staff1.staff, kind='elective')
        resp = self.as_user(self.admin).post(f'/structure/offerings/{elective.pk}/enrol/',
                                             {'student': self.stud_ids['stud1@a.in']})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.enrolments(elective.pk), ['stud1@a.in'])

    def test_a_student_joining_a_section_later_is_enrolled_in_its_core_offerings(self):
        late = self.make_user(self.college_a, 'late@a.in', '3', 'Lat', 'Entry')
        with tenant_context(self.college_a):
            student = late.student
            student.section_id = self.sec1
            student.save()
        self.assertIn('late@a.in', self.enrolments(self.sub1, status='active'))

    def test_a_section_transfer_moves_enrolments_and_keeps_history(self):
        with tenant_context(self.college_a):
            before_reports = AttendanceReport.objects.filter(student=self.stud2.student).count()
            before_results = list(StudentResult.objects.filter(student=self.stud2.student).values_list('pk', 'test', 'exam'))
            student = self.stud2.student
            student.section_id = self.sec2
            student.save()
            old = Enrolment.objects.get(offering_id=self.sub1, student=student)
            new = Enrolment.objects.get(offering_id=self.sub2, student=student)
            self.assertEqual((old.status, old.withdrawn_on is not None), ('withdrawn', True))
            self.assertEqual((new.status, new.source), ('active', 'transfer'))
            self.assertEqual(AttendanceReport.objects.filter(student=student).count(), before_reports)
            self.assertEqual(list(StudentResult.objects.filter(student=student).values_list('pk', 'test', 'exam')), before_results)
        # the student still reads their old marks and attendance history
        client = self.as_user(self.stud2)
        self.assertEqual(client.get('/student/view/result/').status_code, 200)
        resp = client.post('/student/view/attendance/', {'offering': self.sub1, 'start_date': '2026-01-01', 'end_date': '2026-01-31'})
        self.assertEqual(resp.status_code, 200)

    def test_moving_back_reactivates_the_old_enrolment(self):
        with tenant_context(self.college_a):
            student = self.stud2.student
            for section in (self.sec2, self.sec1):
                student.section_id = section
                student.save()
            self.assertEqual(Enrolment.objects.get(offering_id=self.sub1, student=student).status, 'active')
            self.assertEqual(Enrolment.objects.get(offering_id=self.sub2, student=student).status, 'withdrawn')

    def test_students_outside_the_section_can_be_enrolled(self):
        with tenant_context(self.college_a):
            enrolment, changed = services.enrol(self.stud3.student, SubjectOffering.objects.get(pk=self.sub1))
            self.assertTrue(changed)
            self.assertTrue(enrolment.outside_section)
            self.assertFalse(Enrolment.objects.get(offering_id=self.sub1, student=self.stud1.student).outside_section)

    def test_inactive_students_are_not_auto_enrolled(self):
        gone = self.make_user(self.college_a, 'gone@a.in', '3', 'Gone', 'Away')
        with tenant_context(self.college_a):
            gone.is_active = False
            gone.save()
            student = gone.student
            student.section_id = self.sec1
            student.save()
        self.assertNotIn('gone@a.in', self.enrolments(self.sub1))


class ExcelImportTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        with tenant_context(self.college_a):
            self.elective = SubjectOffering.objects.create(
                subject=Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, semester=1,
                                               code='EL3', name='Elective 3', credits=3),
                section_id=self.sec1, term_id=self.term, faculty=self.staff1.staff, kind='elective').pk

    def upload(self, rows, user=None, **kwargs):
        return self.as_user(user or self.admin).post(f'/structure/offerings/{self.elective}/import/',
                                                     {'file': _upload(xlsx(rows))}, **kwargs)

    def active(self):
        with tenant_context(self.college_a):
            return sorted(Enrolment.objects.filter(offering_id=self.elective).values_list('student__admin__email', flat=True))

    def test_valid_list_enrols_everyone_and_is_idempotent(self):
        rows = [['Email', 'Name'], ['stud1@a.in', 'x'], [' STUD3@a.in ', 'y'], [None, None]]
        self.upload(rows)
        self.assertEqual(self.active(), ['stud1@a.in', 'stud3@a.in'])
        self.upload(rows)
        self.assertEqual(self.active(), ['stud1@a.in', 'stud3@a.in'])

    def test_one_bad_row_rejects_the_whole_file(self):
        resp = self.upload([['email'], ['stud1@a.in'], ['nobody@a.in']], follow=True)
        self.assertEqual(self.active(), [])
        self.assertContains(resp, 'nobody@a.in')
        self.assertContains(resp, 'Nothing was imported')

    def test_missing_header_and_non_xlsx_files_are_refused(self):
        self.upload([['name'], ['x']])
        self.assertEqual(self.active(), [])
        resp = self.as_user(self.admin).post(f'/structure/offerings/{self.elective}/import/',
                                             {'file': _upload(io.BytesIO(b'not a workbook'))})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.active(), [])

    def test_only_the_hod_can_import(self):
        self.upload([['email'], ['stud1@a.in']], user=self.staff1)
        self.upload([['email'], ['stud1@a.in']], user=self.stud1)
        self.assertEqual(self.active(), [])


def _upload(buf):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile('list.xlsx', buf.read(), content_type='application/vnd.ms-excel')


class AuditTests(WorldTestCase):
    """11 and 18."""

    def setUp(self):
        super().setUp()
        with tenant_context(self.college_a):
            self.mark = AuditLog.objects.order_by('-id').values_list('id', flat=True).first() or 0

    def new(self, **filters):
        with tenant_context(self.college_a):
            return list(AuditLog.objects.filter(id__gt=self.mark, **filters).order_by('id'))

    def test_every_structure_model_is_audited_on_create_update_delete(self):
        client = self.as_user(self.admin)
        client.post('/structure/departments/add/', {'name': 'New', 'code': 'NEW'})
        with tenant_context(self.college_a):
            pk = Department.objects.get(code='NEW').pk
        client.post(f'/structure/departments/{pk}/edit/', {'name': 'Renamed', 'code': 'NEW'})
        client.post(f'/structure/departments/{pk}/delete/')
        entries = self.new(model_label='academics.Department')
        self.assertEqual([e.action for e in entries], ['create', 'update', 'delete'])
        self.assertTrue(all(e.actor_email == 'admin@a.in' for e in entries))

    def test_enrolment_changes_are_audited(self):
        with tenant_context(self.college_a):
            student = self.stud3.student
            enrolment, _ = services.enrol(student, SubjectOffering.objects.get(pk=self.sub1))
            services.withdraw(enrolment)
        actions = [e.action for e in self.new(model_label='academics.Enrolment')]
        self.assertEqual(actions, ['create', 'update'])

    def test_a_section_transfer_is_audited(self):
        with tenant_context(self.college_a):
            student = self.stud2.student
            student.section_id = self.sec2
            student.save()
        entries = self.new(model_label='academics.Enrolment')
        self.assertEqual(sorted(e.action for e in entries), ['create', 'update'])

    def test_import_is_audited_per_enrolment(self):
        with tenant_context(self.college_a):
            elective = SubjectOffering.objects.create(
                subject=Subject.objects.create(programme_id=self.prog1, regulation_id=self.reg1, semester=1,
                                               code='EL4', name='E4', credits=1),
                section_id=self.sec1, term_id=self.term, faculty=self.staff1.staff, kind='elective')
        self.mark = self.new()[-1].id if self.new() else self.mark
        self.as_user(self.admin).post(f'/structure/offerings/{elective.pk}/import/',
                                      {'file': _upload(xlsx([['email'], ['stud1@a.in'], ['stud2@a.in']]))})
        entries = self.new(model_label='academics.Enrolment')
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e.actor_email == 'admin@a.in' for e in entries))


class PermissionTests(WorldTestCase):
    """8-10 for the new pages."""

    def test_hod_reaches_every_page_staff_and_students_reach_none(self):
        urls = ['/structure/'] + [f'/structure/{k}/' for k in
                                  ('departments', 'programmes', 'regulations', 'batches', 'sections', 'subjects', 'terms', 'offerings')]
        urls.append(f'/structure/offerings/{self.sub1}/enrolments/')
        for url in urls:
            self.assertEqual(self.as_user(self.admin).get(url).status_code, 200, url)
            for user in (self.staff1, self.stud1):
                resp = self.as_user(user).get(url)
                self.assertIn(resp.status_code, (302, 403), url)
                if resp.status_code == 302:
                    self.assertNotIn('/structure', resp['Location'])

    def test_unknown_lists_are_404(self):
        self.assertEqual(self.as_user(self.admin).get('/structure/passwords/').status_code, 404)

    def test_deletes_are_post_only(self):
        for url in (f'/structure/departments/{self.dept_spare}/delete/', f'/structure/offerings/{self.sub1}/withdraw/1/'):
            self.assertEqual(self.as_user(self.admin).get(url).status_code, 405)

    def test_posts_need_the_csrf_token(self):
        from django.test import Client

        from colleges.tests.base import EMAIL_BACKEND, HOST_A
        client = Client(HTTP_HOST=HOST_A, enforce_csrf_checks=True)
        with tenant_context(self.college_a):
            client.force_login(self.admin, backend=EMAIL_BACKEND)
        self.assertEqual(client.post('/structure/departments/add/', {'name': 'x', 'code': 'x'}).status_code, 403)

    def test_deleting_something_in_use_is_refused_with_a_message(self):
        client = self.as_user(self.admin)
        resp = client.post(f'/structure/programmes/{self.prog1}/delete/', follow=True)
        self.assertContains(resp, 'Cannot delete')
        with tenant_context(self.college_a):
            self.assertTrue(Programme.objects.filter(pk=self.prog1).exists())


class TenantIsolationTests(WorldTestCase):
    """7."""

    def test_college_b_sees_nothing_of_college_a_and_codes_can_repeat(self):
        with tenant_context(self.college_b):
            for model in (Department, Programme, Regulation, Batch, Section, Subject, AcademicTerm, SubjectOffering, Enrolment):
                self.assertEqual(model.objects.count(), 0, model.__name__)
            Department.objects.create(name='CSE', code='CSE')  # same code as A, no clash
        with tenant_context(self.college_a):
            self.assertEqual(Department.objects.filter(code='CSE').count(), 1)

    def test_pages_of_college_b_do_not_list_a_records(self):
        from django.test import Client

        from colleges.tests.base import EMAIL_BACKEND, HOST_B
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        client = Client(HTTP_HOST=HOST_B)
        with tenant_context(self.college_b):
            client.force_login(admin_b, backend=EMAIL_BACKEND)
        resp = client.get('/structure/departments/')
        self.assertNotContains(resp, 'CSE')
        self.assertEqual(client.get(f'/structure/offerings/{self.sub1}/enrolments/').status_code, 404)


class StaffAndStudentScopeTests(WorldTestCase):
    """9: scope follows enrolment, not the section."""

    def test_staff_can_only_use_students_enrolled_in_their_offering(self):
        import datetime
        client = self.as_user(self.staff1)
        stud3 = self.stud_ids['stud3@a.in']
        post = {'date': datetime.date.today().isoformat(), 'period': 1, 'key': 'k', 'st_%d' % stud3: 'absent'}
        self.assertEqual(client.post('/attendance/class/%d/' % self.sub1, post).status_code, 403)
        with tenant_context(self.college_a):  # once enrolled (arrear student) it works
            services.enrol(self.stud3.student, SubjectOffering.objects.get(pk=self.sub1))
        self.assertEqual(client.post('/attendance/class/%d/' % self.sub1, post).status_code, 302)

    def test_a_withdrawn_student_is_no_longer_offered_to_the_teacher(self):
        with tenant_context(self.college_a):
            student = self.stud2.student
            student.section_id = self.sec2
            student.save()
        resp = self.as_user(self.staff1).post('/staff/get_students/', {'offering': self.sub1})
        self.assertNotContains(resp, 'Two')

    def test_a_student_cannot_view_attendance_of_an_offering_they_never_took(self):
        resp = self.as_user(self.stud1).post('/student/view/attendance/', {
            'offering': self.sub2, 'start_date': '2026-01-01', 'end_date': '2026-01-31'})
        self.assertEqual(resp.status_code, 404)
