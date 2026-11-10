"""Fill a DEMO college with sample data so every role has something to look at.

For development and demo tenants only: the schema name must start with "demo". Safe to re-run: every row is looked up
before it is created and nothing that already exists is changed (so edits made through the app survive a re-run).
All phone numbers are obviously fake (+91 90000 000xx); no real person's data is used.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import tenant_context

from colleges.models import College

DEMO_PREFIX = 'demo'

# (first, last, guardian index); students 1 and 2 are siblings and share guardian 0.
STUDENTS = [
    ('Aarthi', 'Kumar', 0), ('Arjun', 'Kumar', 0), ('Divya', 'Raman', 1), ('Karthik', 'Subramanian', 2),
    ('Meena', 'Iyer', 3), ('Nithya', 'Pillai', 4), ('Pranav', 'Nair', 5), ('Priya', 'Selvam', 6),
    ('Rahul', 'Venkat', 7), ('Sneha', 'Mohan', 8),
]
# (name, relation, consent): a mix, so the consent alerts and the WhatsApp consent guard have something to act on.
GUARDIANS = [
    ('Mr. Kumar', 'father', 'yes'), ('Mrs. Raman', 'mother', 'yes'), ('Mr. Subramanian', 'father', 'unknown'),
    ('Mrs. Iyer', 'mother', 'yes'), ('Mr. Pillai', 'father', 'no'), ('Mrs. Nair', 'mother', 'yes'),
    ('Mr. Selvam', 'father', 'unknown'), ('Mrs. Venkat', 'mother', 'yes'), ('Mr. Mohan', 'father', 'yes'),
]
SUBJECTS = [('CS101', 'Programming in Python', 1, 4), ('MA101', 'Engineering Mathematics I', 2, 4)]  # code, name, period, credits


def weekdays_before(today, count):
    days, day = [], today
    while len(days) < count:
        day -= datetime.timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return sorted(days)


class Command(BaseCommand):
    help = 'Add a sample programme, section, subjects, 10 students with guardians and 10 days of attendance to a demo college.'

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='demo', help='Must start with "demo".')
        parser.add_argument('--password', default='demo-pass-123', help='Dev password for the demo users that get created.')

    def handle(self, *args, **opts):
        schema = opts['schema']
        if not schema.startswith(DEMO_PREFIX):
            raise CommandError('Refusing to seed "%s": only demo colleges (schema name starting with "%s") may be seeded.'
                               % (schema, DEMO_PREFIX))
        college = College.objects.filter(schema_name=schema).first()
        if college is None:
            raise CommandError('No college with schema "%s". Run create_demo_college first.' % schema)
        with tenant_context(college), transaction.atomic():
            counts = self.seed(schema, opts['password'])
        self.stdout.write(self.style.SUCCESS('Seeded "%s": %s' % (schema, ', '.join('%s %s' % (v, k) for k, v in counts.items()))))

    def seed(self, schema, password):
        from academics.models import (
            AcademicTerm,
            Batch,
            Department,
            Programme,
            Regulation,
            Section,
            Subject,
            SubjectOffering,
        )
        from academics.services import enrol_section_in_core
        from attendance import services as attendance
        from main_app.models import Attendance, CustomUser, Guardian, Staff, Student, StudentGuardian

        counts = {}

        def get(model, lookup, defaults=None):
            obj, created = model.objects.get_or_create(defaults=defaults or {}, **lookup)
            counts[model.__name__] = counts.get(model.__name__, 0) + created
            return obj

        def user(email, user_type, first, last):
            existing = CustomUser.objects.filter(email=email).first()
            if existing:
                return existing
            counts['users'] = counts.get('users', 0) + 1
            return CustomUser.objects.create_user(email, password, first_name=first, last_name=last, user_type=user_type)

        today = datetime.date.today()
        dept = get(Department, {'code': 'CSE'}, {'name': 'Computer Science and Engineering'})
        prog = get(Programme, {'code': 'BTCSE'}, {'department': dept, 'name': 'B.Tech Computer Science', 'duration_semesters': 8})
        reg = get(Regulation, {'programme': prog, 'name': 'R2026'}, {'effective_from': datetime.date(2026, 6, 1)})
        batch = get(Batch, {'programme': prog, 'start_year': today.year}, {'regulation': reg})
        section = get(Section, {'batch': batch, 'name': 'A'})
        term = get(AcademicTerm, {'name': 'Demo term (current)'},
                   {'start_date': today - datetime.timedelta(days=35), 'end_date': today + datetime.timedelta(days=120)})

        user('admin@%s.in' % schema, CustomUser.COLLEGE_ADMIN, 'Demo', 'Admin')
        staff_user = user('staff@%s.in' % schema, CustomUser.STAFF, 'Demo', 'Staff')
        staff = Staff.objects.get(admin=staff_user)
        if staff.department_id is None:
            staff.department = dept
            staff.save()

        guardians = []
        for index, (name, _relation, consent) in enumerate(GUARDIANS):
            guardians.append(get(Guardian, {'phone': '+91900000%04d' % (index + 1)},
                                 {'full_name': name, 'whatsapp_consent': consent}))

        students = []
        for index, (first, last, guardian_index) in enumerate(STUDENTS):
            student_user = user('student%02d@%s.in' % (index + 1, schema), CustomUser.STUDENT, first, last)
            student = Student.objects.get(admin=student_user)
            if student.section_id is None:
                student.section = section
                student.roll_number = student.roll_number or 'D%d%03d' % (today.year % 100, index + 1)
                student.date_of_birth = student.date_of_birth or datetime.date(today.year - 18, 1 + index, 10)
                student.save()
            relation = GUARDIANS[guardian_index][1]
            get(StudentGuardian, {'student': student, 'guardian': guardians[guardian_index]},
                {'relation': relation, 'is_primary': True})
            students.append(student)
        demo_student = Student.objects.filter(admin__email='student@%s.in' % schema).first()
        if demo_student and demo_student.section_id is None:  # the login create_demo_college made
            demo_student.section = section
            demo_student.save()

        offerings = []
        for code, name, _period, credits in SUBJECTS:
            subject = get(Subject, {'regulation': reg, 'code': code},
                          {'programme': prog, 'semester': 1, 'name': name, 'credits': credits})
            offerings.append(get(SubjectOffering, {'subject': subject, 'section': section, 'term': term},
                                 {'faculty': staff, 'kind': SubjectOffering.CORE}))
        for offering in offerings:
            enrol_section_in_core(offering)

        admin_user = CustomUser.objects.get(email='admin@%s.in' % schema)
        days = weekdays_before(today, 10)
        marked = 0
        for (_code, _name, period, _credits), offering in zip(SUBJECTS, offerings, strict=True):
            for day_number, day in enumerate(days):
                if Attendance.objects.filter(offering=offering, date=day, period=period).exists():
                    continue  # never overwrite a class somebody has taken or edited
                absent = {}
                for index, student in enumerate(students):
                    # Karthik (index 3) misses every other class so the shortage warning has a case;
                    # everyone else misses the odd class.
                    if (index == 3 and day_number % 2 == 0) or (index != 3 and (index * 3 + day_number) % 11 == 0):
                        absent[student.pk] = attendance.ABSENT
                attendance.submit(admin_user, offering, day, period, absent, key='seed-%s-%s' % (offering.pk, day))
                marked += 1
        counts['classes marked'] = marked
        return counts
