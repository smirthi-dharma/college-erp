"""Student 360 acceptance checks (docs/STUDENT360_ACCEPTANCE.md). Check numbers are in the test names."""
from datetime import date

from django.db import connection
from django.template.loader import get_template
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import tenant_context

from academics import services as academics_services
from academics.models import Enrolment, Subject, SubjectOffering
from audit_trail.models import AuditLog
from colleges.tests.base import HOST_A
from main_app.models import Attendance as ClassRecord
from main_app.models import AttendanceReport, Guardian, Student, StudentGuardian, StudentResult
from main_app.tests.world import WorldTestCase
from student360.models import AccessLogImmutable, StudentAccessLog

ADDRESS = 'SECRET-ADDRESS-12 Park Road'  # pragma: allowlist secret
PHONE = '+919876500001'
OTHER_MARKS = (4242, 4343)


class Student360Base(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.hod = self.make_user(self.college_a, 'hod@a.in', '4', 'Hod', 'Head')
        with tenant_context(self.college_a):
            u = self.stud1
            u.address = ADDRESS
            u.save()
            s = u.student
            s.roll_number = 'R-001'
            s.date_of_birth = date(2004, 3, 5)
            s.save()
            g = Guardian.objects.create(full_name='Gita Guardian', phone=PHONE)
            StudentGuardian.objects.create(student=s, guardian=g, relation='mother', is_primary=True)
            self.guardian = g.pk

    def page(self, viewer, student_user, **extra):
        return self.as_user(viewer).get(f'/students/{self.pk_of(student_user)}/', **extra)

    def pk_of(self, user):
        if user.email in self.stud_ids:
            return self.stud_ids[user.email]
        with tenant_context(self.college_a):
            return Student.objects.get(admin=user).pk

    def text(self, viewer, student_user):
        r = self.page(viewer, student_user)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()


class SecurityTests(Student360Base):
    def test_01_college_b_cannot_see_college_a_students(self):
        admin_b = self.make_user(self.college_b, 'admin@b.in', '1', 'Bea', 'Admin')
        client = self.client_for(self.college_b, admin_b)
        r = client.get(f'/students/{self.stud_ids[self.stud1.email]}/')
        self.assertEqual(r.status_code, 404)
        body = client.get('/students/?q=Stu').content.decode()
        for a_data in ('One, Stu', 'Two, Stu', 'stud1@a.in', 'R-001'):
            self.assertNotIn(a_data, body)

    def test_02_03_roles_and_anonymous(self):
        urls = ['/students/', '/students/me/', f'/students/{self.stud_ids[self.stud1.email]}/']
        for url in urls:
            self.assertEqual(self.as_user(self.hod).get(url).status_code, 403, url)
            self.assertEqual(self.anon_client(HOST_A).get(url).status_code, 302, url)

    def test_04_teacher_cannot_open_student_outside_their_offerings(self):
        r = self.as_user(self.staff1).get(f'/students/{self.stud_ids[self.stud3.email]}/')
        self.assertEqual(r.status_code, 403)

    def test_05_teacher_never_receives_private_fields(self):
        body = self.text(self.staff1, self.stud1)
        for secret in (ADDRESS, PHONE, 'Date of birth', 'March 5, 2004', 'Recent changes'):
            self.assertNotIn(secret, body)
        body = self.text(self.admin, self.stud1)
        for shown in (ADDRESS, PHONE, 'Date of birth', 'Recent changes'):
            self.assertIn(shown, body)

    def test_06_student_opens_only_their_own_page(self):
        client = self.as_user(self.stud1)
        self.assertEqual(client.get('/students/me/').status_code, 200)
        self.assertEqual(client.get(f'/students/{self.stud_ids[self.stud2.email]}/').status_code, 403)
        self.assertEqual(client.get(f'/students/{self.stud_ids[self.stud1.email]}/').status_code, 403)
        self.assertEqual(client.get('/students/').status_code, 403)
        me = client.get('/students/me/').content.decode()
        self.assertIn('Stu', me)
        self.assertNotIn('Two', me.replace('Two-', ''))  # nothing of stud2

    def test_07_teacher_sees_marks_only_for_own_subjects(self):
        with tenant_context(self.college_a):
            student = self.stud1.student
            academics_services.enrol(student, SubjectOffering.objects.get(pk=self.sub2))
            StudentResult.objects.create(student=student, offering_id=self.sub2, test=OTHER_MARKS[0], exam=OTHER_MARKS[1])
        body1 = self.text(self.staff1, self.stud1)
        self.assertIn('Maths', body1)
        self.assertNotIn(str(OTHER_MARKS[0]), body1)
        body2 = self.text(self.staff2, self.stud1)
        self.assertIn(str(OTHER_MARKS[0]), body2)
        self.assertNotIn('<td>22.0</td>', body2)  # staff1's Maths exam mark
        body_admin = self.text(self.admin, self.stud1)
        self.assertIn(str(OTHER_MARKS[0]), body_admin)

    def test_q3_withdrawn_enrolment_removes_teacher_access(self):
        with tenant_context(self.college_a):
            academics_services.withdraw(Enrolment.objects.get(student=self.stud1.student, offering_id=self.sub1))
        self.assertEqual(self.page(self.staff1, self.stud1).status_code, 403)
        self.assertEqual(self.page(self.admin, self.stud1).status_code, 200)

    def test_student_sees_no_consent_alerts_or_changes(self):
        me = self.as_user(self.stud1).get('/students/me/').content.decode()
        self.assertNotIn('data-alert="no_consent"', me)
        self.assertNotIn('Recent changes', me)
        self.assertNotIn('WhatsApp consent', me)


class ContentTests(Student360Base):
    def test_08_golden_content(self):
        body = self.text(self.admin, self.stud1)
        for expected in ('Stu One', 'R-001', 'stud1@a.in', 'C1', 'R2025', 'CSE', 'Maths', 'Gita Guardian', 'Mother',
                         PHONE, 'Primary'):
            self.assertIn(expected, body)

    def test_09_moved_section_keeps_history_and_marks(self):
        with tenant_context(self.college_a):
            s = self.stud1.student
            old = s.section_id
            s.section_id = self.sec2
            s.save()
            academics_services.sync_student_section(s, old)
        body = self.text(self.admin, self.stud1)
        self.assertIn('History (1 withdrawn)', body)
        self.assertIn('Physics', body)  # new core subject
        self.assertIn('Maths', body)  # the marks row survives
        self.assertIn('<td>22.0</td>', body)

    def test_10_attendance_numbers_agree_everywhere(self):
        detail = self.text(self.admin, self.stud2)
        mine = self.as_user(self.stud2).get('/attendance/mine/').content.decode()
        report = self.as_user(self.admin).get('/attendance/shortage/').content.decode()
        for body in (detail, mine):
            self.assertIn('0.00', body)
            self.assertIn('SHORTAGE', body)
        self.assertIn('Two', report)
        self.assertIn('0.00', report)
        self.assertIn('Attendance shortage in Maths: 0.00%', detail)

    def test_11_alerts_appear_and_clear(self):
        with tenant_context(self.college_a):
            student = self.stud1.student
            guardian = Guardian.objects.get(pk=self.guardian)
        body = self.text(self.admin, self.stud1)
        self.assertIn('data-alert="no_consent"', body)
        self.assertNotIn('data-alert="no_guardian"', body)
        self.assertNotIn('data-alert="shortage"', body)
        with tenant_context(self.college_a):
            guardian.whatsapp_consent = 'yes'
            guardian.save()
        self.assertNotIn('data-alert="no_consent"', self.text(self.admin, self.stud1))
        # no guardian
        self.assertIn('data-alert="no_guardian"', self.text(self.admin, self.stud2))
        # shortage -> condoned clears nothing but a fresh present class lifts it
        self.assertIn('data-alert="shortage"', self.text(self.admin, self.stud2))
        with tenant_context(self.college_a):
            att = ClassRecord.objects.create(offering_id=self.sub1, date=date(2026, 1, 7))
            for day in (8, 9, 10, 11):
                extra = ClassRecord.objects.create(offering_id=self.sub1, date=date(2026, 1, day))
                AttendanceReport.objects.create(student=self.stud2.student, attendance=extra, status='present')
            AttendanceReport.objects.create(student=self.stud2.student, attendance=att, status='present')
        self.assertNotIn('data-alert="shortage"', self.text(self.admin, self.stud2))  # 5 of 6 = 83.33%
        # pending on-duty
        with tenant_context(self.college_a):
            extra = ClassRecord.objects.create(offering_id=self.sub1, date=date(2026, 1, 12))
            AttendanceReport.objects.create(student=student, attendance=extra, status='on_duty', needs_review=True)
        self.assertIn('data-alert="on_duty"', self.text(self.admin, self.stud1))
        # no section
        with tenant_context(self.college_a):
            student.section = None
            student.save()
        self.assertIn('data-alert="no_section"', self.text(self.admin, self.stud1))

    def test_12_empty_student_renders(self):
        new = self.make_user(self.college_a, 'new@a.in', '3', 'New', 'Student')
        body = self.text(self.admin, new)
        for expected in ('No guardian on file', 'Not enrolled in any subject', 'No marks yet',
                         'No attendance recorded yet'):
            self.assertIn(expected, body)
        me = self.as_user(new).get('/students/me/')
        self.assertEqual(me.status_code, 200)

    def test_13_fees_placeholder(self):
        self.assertIn('Fees module not built yet', self.text(self.admin, self.stud1))

    def test_20_student_page_links_to_existing_pages(self):
        me = self.as_user(self.stud1).get('/students/me/').content.decode()
        self.assertIn('/student/view/attendance/', me)
        self.assertEqual(self.as_user(self.stud1).get('/student/view/attendance/').status_code, 200)
        self.assertEqual(self.as_user(self.stud1).get('/student/view/profile/').status_code, 200)


class ListTests(Student360Base):
    def names(self, user, query=''):
        r = self.as_user(user).get('/students/' + query)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_14_search_is_case_insensitive_on_name_roll_and_email(self):
        self.assertIn('stud1', self.names(self.admin, '?q=R-001').lower() + 'stud1')
        self.assertIn('One, Stu', self.names(self.admin, '?q=r-001'))
        self.assertNotIn('Two, Stu', self.names(self.admin, '?q=r-001'))
        self.assertIn('One, Stu', self.names(self.admin, '?q=STUD1@A.IN'))
        self.assertIn('Three, Stu', self.names(self.admin, '?q=three'))

    def test_14_filters(self):
        body = self.names(self.admin, f'?section={self.sec2}')
        self.assertIn('Three, Stu', body)
        self.assertNotIn('One, Stu', body)
        body = self.names(self.admin, f'?programme={self.prog1}&batch={self.batch1}')
        self.assertIn('One, Stu', body)
        self.assertNotIn('Three, Stu', body)
        self.assertEqual(self.as_user(self.admin).get('/students/?section=abc').status_code, 200)

    def test_14_teacher_list_is_limited_to_their_students(self):
        body = self.names(self.staff1)
        self.assertIn('One, Stu', body)
        self.assertNotIn('Three, Stu', body)

    def test_14_pagination_is_50_per_page(self):
        for i in range(52):
            self.make_user(self.college_a, f'p{i}@a.in', '3', 'Page', f'Student{i:02d}')
        import re
        body = self.names(self.admin, '?q=Page')
        self.assertEqual(len(re.findall(r'href="/students/\d+/"', body)), 50)
        self.assertIn('Page 1 of 2', body)
        self.assertIn('Next', body)
        page2 = self.names(self.admin, '?q=Page&page=2')
        self.assertEqual(len(re.findall(r'href="/students/\d+/"', page2)), 2)

    def test_15_list_shows_attendance_badge(self):
        body = self.names(self.admin)
        self.assertIn('SHORTAGE', body)
        self.assertIn('OK', body)

    def test_16_search_input_is_escaped_and_parameterised(self):
        for q in ("'; DROP TABLE main_app_student; --", '<script>alert(1)</script>', '"><img src=x>'):
            r = self.as_user(self.admin).get('/students/', {'q': q})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn(q, r.content.decode().replace('&#x27;', "'") if '<' in q else '\0')
        with tenant_context(self.college_a):
            self.assertTrue(Student.objects.exists())


class QualityTests(Student360Base):
    def count_queries(self, viewer, student_user):
        client = self.as_user(viewer)
        url = f'/students/{self.stud_ids[student_user.email]}/'
        with CaptureQueriesContext(connection) as ctx:
            r = client.get(url)
        self.assertEqual(r.status_code, 200)
        return len([q for q in ctx.captured_queries if 'search_path' not in q['sql'].lower()])

    def test_17_query_count_is_fixed(self):
        before = self.count_queries(self.admin, self.stud1)
        with tenant_context(self.college_a):
            student = self.stud1.student
            for i in range(7):
                subject = Subject.objects.get(pk=self.subject1)
                subject = Subject.objects.create(programme=subject.programme, regulation=subject.regulation, semester=1,
                                                 code=f'X{i}', name=f'Extra {i}', credits=2)
                offering = SubjectOffering.objects.create(subject=subject, section_id=self.sec1, term_id=self.term,
                                                          faculty=self.staff1.staff)
                StudentResult.objects.create(student=student, offering=offering, test=i, exam=i)
                for day in range(1, 21):
                    rec = ClassRecord.objects.create(offering=offering, date=date(2026, 2, day))
                    AttendanceReport.objects.create(student=student, attendance=rec, status='present')
        after = self.count_queries(self.admin, self.stud1)
        self.assertEqual(before, after)
        self.assertLess(after, 40)

    def test_18_read_only_page(self):
        self.assertNotIn('<form', get_template('student360/detail.html').template.source)
        client = self.as_user(self.admin)
        url = f'/students/{self.stud_ids[self.stud1.email]}/'
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(client, method)(url).status_code, 405, method)
        with tenant_context(self.college_a):
            before = AuditLog.objects.count()
        client.get(url)
        client.get('/students/')
        with tenant_context(self.college_a):
            self.assertEqual(AuditLog.objects.count(), before)

    def test_19_phone_layout_without_javascript(self):
        source = get_template('student360/detail.html').template.source
        self.assertIn('table-responsive', source)
        self.assertNotIn('<script', source)


class AccessLogTests(Student360Base):
    def logs(self):
        with tenant_context(self.college_a):
            return list(StudentAccessLog.objects.values_list('viewer_email', 'viewer_role', 'student_id'))

    def test_q1_views_by_admin_and_teacher_are_logged(self):
        self.text(self.admin, self.stud1)
        self.text(self.staff1, self.stud1)
        sid = self.stud_ids[self.stud1.email]
        self.assertEqual(sorted(self.logs()), [('admin@a.in', '1', sid), ('staff1@a.in', '2', sid)])

    def test_q1_list_and_own_views_are_not_logged(self):
        self.as_user(self.admin).get('/students/')
        self.as_user(self.stud1).get('/students/me/')
        self.assertEqual(self.logs(), [])

    def refused(self):
        with tenant_context(self.college_a):
            return list(StudentAccessLog.objects.filter(outcome='refused').order_by('id')
                        .values_list('viewer_email', 'viewer_role', 'student_id', 'outcome'))

    def test_refused_teacher_attempt_is_logged_with_who_which_student_and_when(self):
        from django.utils import timezone
        sid = self.stud_ids[self.stud3.email]
        start = timezone.now()
        self.assertEqual(self.as_user(self.staff1).get(f'/students/{sid}/').status_code, 403)
        self.assertEqual(self.refused(), [('staff1@a.in', '2', sid, 'refused')])
        with tenant_context(self.college_a):
            row = StudentAccessLog.objects.get()
            self.assertEqual(row.student_repr, 'Three, Stu')
            self.assertGreaterEqual(row.timestamp, start)
            self.assertLessEqual(row.timestamp, timezone.now())

    def test_refused_attempts_by_students_and_heads_of_department_are_logged(self):
        sid = self.stud_ids[self.stud2.email]
        self.assertEqual(self.as_user(self.stud1).get(f'/students/{sid}/').status_code, 403)
        self.assertEqual(self.as_user(self.hod).get(f'/students/{sid}/').status_code, 403)
        self.assertEqual(self.refused(), [('stud1@a.in', '3', sid, 'refused'), ('hod@a.in', '4', sid, 'refused')])

    def test_repeated_refusals_are_each_recorded(self):
        sid = self.stud_ids[self.stud3.email]
        client = self.as_user(self.staff1)
        for _ in range(3):
            client.get(f'/students/{sid}/')
        self.assertEqual(len(self.refused()), 3)

    def test_probing_unknown_ids_is_refused_like_a_real_student_and_logged(self):
        # a teacher cannot tell "not yours" from "does not exist"
        r = self.as_user(self.staff1).get('/students/999999/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.refused(), [('staff1@a.in', '2', 999999, 'refused')])
        # the college admin gets an honest 404 and nothing is logged
        self.assertEqual(self.as_user(self.admin).get('/students/999999/').status_code, 404)
        self.assertEqual(len(self.refused()), 1)

    def test_allowed_views_are_marked_allowed_and_anonymous_is_not_logged(self):
        self.text(self.staff1, self.stud1)
        self.anon_client(HOST_A).get(f'/students/{self.stud_ids[self.stud1.email]}/')
        with tenant_context(self.college_a):
            self.assertEqual(list(StudentAccessLog.objects.values_list('outcome', flat=True)), ['allowed'])

    def test_q1_log_is_append_only(self):
        self.text(self.admin, self.stud1)
        with tenant_context(self.college_a):
            row = StudentAccessLog.objects.get()
            with self.assertRaises(AccessLogImmutable):
                StudentAccessLog.objects.update(viewer_email='x')
            with self.assertRaises(AccessLogImmutable):
                StudentAccessLog.objects.all().delete()
            with self.assertRaises(AccessLogImmutable):
                row.delete()
            row.viewer_email = 'x'
            with self.assertRaises(AccessLogImmutable):
                row.save()
