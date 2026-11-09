"""Validation (no saving) and saving for the three imports. Validation never writes to the database."""
from dataclasses import dataclass, field

from django.db import transaction

from academics.models import Department, Section
from main_app.models import CustomUser, Guardian, Staff, Student, StudentGuardian

from . import parsing


@dataclass
class Plan:
    kind: str
    actions: list = field(default_factory=list)   # {'row', 'action': create|update|nochange, 'data', 'pk'?}
    problems: list = field(default_factory=list)  # (row, column, message)
    warnings: list = field(default_factory=list)  # (row, column, message)

    def count(self, action):
        return sum(1 for a in self.actions if a['action'] == action)

    @property
    def ok(self):
        return not self.problems


class Importer:
    kind = ''
    required = ()
    optional = ()

    def __init__(self, update_existing=False):
        self.update_existing = update_existing

    # -- helpers --------------------------------------------------------------------------------------------------
    def new_plan(self, headers):
        plan = Plan(self.kind)
        missing = [c for c in self.required if c not in headers]
        for column in missing:
            plan.problems.append((1, column, 'column is missing'))
        for column in headers:
            if column and column not in self.required and column not in self.optional:
                plan.warnings.append((1, column, 'unknown column, ignored'))
        return plan, not missing

    @staticmethod
    def check(plan, row, column, result):
        value, error = result
        if error:
            plan.problems.append((row, column, error))
        return value

    @staticmethod
    def first_seen(seen, key, row, plan, column, label):
        if key in seen:
            plan.problems.append((row, column, f'{label} is repeated (first used in row {seen[key]})'))
            return False
        seen[key] = row
        return True


class PersonImporter(Importer):
    """Shared by students and staff: both create a login-less user with a role profile."""
    user_type = ''
    id_column = ''

    def existing_by_id(self):
        raise NotImplementedError

    def profile_fields(self, plan, row, v):
        """Kind-specific cleaned fields (dict)."""
        raise NotImplementedError

    def changed(self, current, data):
        raise NotImplementedError

    def validate(self, headers, rows):
        plan, ok = self.new_plan(headers)
        if not ok:
            return plan
        emails = {e.lower(): pk for pk, e in CustomUser.objects.values_list('pk', 'email')}
        by_id = self.existing_by_id()
        seen_id, seen_email = {}, {}
        for number, v in rows:
            before = len(plan.problems)
            ident = self.check(plan, number, self.id_column, parsing.clean_text(v.get(self.id_column), 30, True))
            first = self.check(plan, number, 'first_name', parsing.clean_text(v.get('first_name'), 150, True))
            last = self.check(plan, number, 'last_name', parsing.clean_text(v.get('last_name'), 150, True))
            email = self.check(plan, number, 'email', parsing.clean_email(v.get('email')))
            gender = self.check(plan, number, 'gender', parsing.clean_gender(v.get('gender')))
            phone = self.check(plan, number, 'phone', parsing.clean_phone(v.get('phone')))
            address = self.check(plan, number, 'address', parsing.clean_text(v.get('address'), 500))
            extra = self.profile_fields(plan, number, v)
            if ident and not self.first_seen(seen_id, ident.lower(), number, plan, self.id_column, self.id_column.replace('_', ' ')):
                continue
            if email and not self.first_seen(seen_email, email, number, plan, 'email', 'e-mail'):
                continue
            if len(plan.problems) > before:
                continue
            data = dict(ident=ident, first_name=first, last_name=last, email=email, gender=gender, phone=phone,
                        address=address, **extra)
            current = by_id.get(ident.lower())
            if current is None:
                if email in emails:
                    plan.problems.append((number, 'email', 'is already used by someone else in this college'))
                elif ident.lower() in by_id:  # defensive, cannot happen
                    pass
                else:
                    plan.actions.append({'row': number, 'action': 'create', 'data': data})
                continue
            # the id matches an existing person
            if not self.update_existing:
                plan.problems.append((number, self.id_column, 'already exists (tick "update existing" to change it)'))
            elif current.admin.email.lower() != email:
                plan.problems.append((number, 'email', 'differs from the e-mail on record; e-mail and ID are never changed by an import'))
            else:
                changes = self.changed(current, data)
                plan.actions.append({'row': number, 'action': 'update' if changes else 'nochange',
                                     'data': data, 'pk': current.pk})
        return plan

    # -- saving ---------------------------------------------------------------------------------------------------
    def make_user(self, data):
        user = CustomUser(email=data['email'], user_type=self.user_type, first_name=data['first_name'],
                          last_name=data['last_name'], gender=data['gender'], address=data['address'],
                          phone=data['phone'])
        user.set_unusable_password()  # nobody can log in until a password is set (reset link, later)
        user.save()  # the existing signal creates the role profile
        return user


class StudentImporter(PersonImporter):
    kind = 'students'
    user_type = CustomUser.STUDENT
    id_column = 'roll_number'
    required = ('roll_number', 'first_name', 'last_name', 'email', 'programme_code', 'batch_start_year', 'section')
    optional = ('gender', 'phone', 'address', 'date_of_birth')

    def existing_by_id(self):
        return {s.roll_number.lower(): s for s in Student.objects.exclude(roll_number=None).select_related('admin')}

    def profile_fields(self, plan, row, v):
        if not hasattr(self, '_sections'):
            self._sections = {(s.batch.programme.code.lower(), s.batch.start_year, s.name.lower()): s.pk
                              for s in Section.objects.select_related('batch__programme')}
        programme = self.check(plan, row, 'programme_code', parsing.clean_text(v.get('programme_code'), 20, True))
        year_text = (v.get('batch_start_year') or '').strip()
        name = self.check(plan, row, 'section', parsing.clean_text(v.get('section'), 20, True))
        dob = self.check(plan, row, 'date_of_birth', parsing.clean_dob(v.get('date_of_birth')))
        section_id = None
        if programme and year_text and name:
            if not year_text.isdigit():
                plan.problems.append((row, 'batch_start_year', 'must be a year such as 2025'))
            else:
                section_id = self._sections.get((programme.lower(), int(year_text), name.lower()))
                if section_id is None:
                    plan.problems.append((row, 'section', f'no section {name} in {programme} batch {year_text}; create it first under Institution structure'))
        elif not year_text:
            plan.problems.append((row, 'batch_start_year', 'is required'))
        return {'section_id': section_id, 'date_of_birth': dob}

    def changed(self, current, data):
        a = current.admin
        return [f for f, now, was in (
            ('first_name', data['first_name'], a.first_name), ('last_name', data['last_name'], a.last_name),
            ('gender', data['gender'], a.gender), ('phone', data['phone'], a.phone), ('address', data['address'], a.address),
            ('date_of_birth', data['date_of_birth'], current.date_of_birth), ('section', data['section_id'], current.section_id),
        ) if now != was]

    def apply(self, plan):
        refs, created, updated, unchanged = {'users': []}, 0, 0, 0
        for item in plan.actions:
            data = item['data']
            if item['action'] == 'create':
                user = self.make_user(data)
                student = Student.objects.get(admin=user)
                student.roll_number, student.section_id, student.date_of_birth = data['ident'], data['section_id'], data['date_of_birth']
                student.save()  # section change triggers the enrolment rules
                refs['users'].append(user.pk)
                created += 1
            elif item['action'] == 'update':
                student = Student.objects.select_related('admin').get(pk=item['pk'])
                a = student.admin
                a.first_name, a.last_name, a.gender, a.phone, a.address = (
                    data['first_name'], data['last_name'], data['gender'], data['phone'], data['address'])
                a.save()
                student.section_id, student.date_of_birth = data['section_id'], data['date_of_birth']
                student.save()
                updated += 1
            else:
                unchanged += 1
        return refs, created, updated, unchanged


class StaffImporter(PersonImporter):
    kind = 'staff'
    user_type = CustomUser.STAFF
    id_column = 'employee_id'
    required = ('employee_id', 'first_name', 'last_name', 'email')
    optional = ('gender', 'phone', 'department_code', 'address')

    def existing_by_id(self):
        return {s.employee_id.lower(): s for s in Staff.objects.exclude(employee_id=None).select_related('admin')}

    def profile_fields(self, plan, row, v):
        if not hasattr(self, '_departments'):
            self._departments = {d.code.lower(): d.pk for d in Department.objects.all()}
        code = self.check(plan, row, 'department_code', parsing.clean_text(v.get('department_code'), 20))
        department_id = None
        if code:
            department_id = self._departments.get(code.lower())
            if department_id is None:
                plan.problems.append((row, 'department_code', f'no department with code {code}'))
        return {'department_id': department_id}

    def changed(self, current, data):
        a = current.admin
        return [f for f, now, was in (
            ('first_name', data['first_name'], a.first_name), ('last_name', data['last_name'], a.last_name),
            ('gender', data['gender'], a.gender), ('phone', data['phone'], a.phone), ('address', data['address'], a.address),
            ('department', data['department_id'], current.department_id),
        ) if now != was]

    def apply(self, plan):
        refs, created, updated, unchanged = {'users': []}, 0, 0, 0
        for item in plan.actions:
            data = item['data']
            if item['action'] == 'create':
                user = self.make_user(data)
                staff = Staff.objects.get(admin=user)
                staff.employee_id, staff.department_id = data['ident'], data['department_id']
                staff.save()
                refs['users'].append(user.pk)
                created += 1
            elif item['action'] == 'update':
                staff = Staff.objects.select_related('admin').get(pk=item['pk'])
                a = staff.admin
                a.first_name, a.last_name, a.gender, a.phone, a.address = (
                    data['first_name'], data['last_name'], data['gender'], data['phone'], data['address'])
                a.save()
                staff.department_id = data['department_id']
                staff.save()
                updated += 1
            else:
                unchanged += 1
        return refs, created, updated, unchanged


class ParentImporter(Importer):
    kind = 'parents'
    required = ('student_roll_number', 'name', 'relation', 'phone')
    optional = ('email', 'is_primary', 'whatsapp_consent')
    RELATIONS = {r: r for r, _ in StudentGuardian.RELATION}

    def validate(self, headers, rows):
        plan, ok = self.new_plan(headers)
        if 'whatsapp_consent' in headers:
            plan.warnings.append((1, 'whatsapp_consent', 'ignored: consent can only be recorded by the consent feature, never by an import'))
        if not ok:
            return plan
        students = {r.lower(): pk for pk, r in Student.objects.exclude(roll_number=None).values_list('pk', 'roll_number')}
        guardians = {g.phone: g for g in Guardian.objects.all()}
        links = {(link.student_id, link.guardian_id): link for link in StudentGuardian.objects.all()}
        primary = {link.student_id: link.guardian_id for link in StudentGuardian.objects.filter(is_primary=True)}
        seen, primary_in_file = {}, {}
        for number, v in rows:
            before = len(plan.problems)
            roll = self.check(plan, number, 'student_roll_number', parsing.clean_text(v.get('student_roll_number'), 30, True))
            name = self.check(plan, number, 'name', parsing.clean_text(v.get('name'), 150, True))
            relation = (v.get('relation') or '').strip().lower()
            if relation not in self.RELATIONS:
                plan.problems.append((number, 'relation', 'must be father, mother, guardian or other'))
            phone = self.check(plan, number, 'phone', parsing.clean_phone(v.get('phone'), required=True))
            email = self.check(plan, number, 'email', parsing.clean_email(v.get('email'), required=False))
            flag = (v.get('is_primary') or '').strip().lower()
            if flag not in ('', 'yes', 'no', 'y', 'n', 'true', 'false'):
                plan.problems.append((number, 'is_primary', 'must be yes or no'))
            is_primary = flag in ('yes', 'y', 'true')
            student_id = students.get(roll.lower()) if roll else None
            if roll and student_id is None:
                plan.problems.append((number, 'student_roll_number', f'no student with roll number {roll}; import the students first'))
            if len(plan.problems) > before:
                continue
            if not self.first_seen(seen, (student_id, phone), number, plan, 'phone', 'this guardian for this student'):
                continue
            guardian = guardians.get(phone)
            link = links.get((student_id, guardian.pk)) if guardian else None
            if is_primary:
                holder = primary.get(student_id)
                if holder is not None and (guardian is None or holder != guardian.pk):
                    plan.problems.append((number, 'is_primary', 'this student already has a primary guardian'))
                    continue
                if primary_in_file.get(student_id, phone) != phone:
                    plan.problems.append((number, 'is_primary', 'two primary guardians for the same student in this file'))
                    continue
                primary_in_file[student_id] = phone
            data = dict(roll=roll, student_id=student_id, name=name, relation=relation, phone=phone, email=email,
                        is_primary=is_primary)
            if link is not None and not self.update_existing:
                plan.problems.append((number, 'phone', 'this guardian is already linked to this student (tick "update existing" to change it)'))
                continue
            if guardian is not None and guardian.full_name.lower() != name.lower():
                (plan.warnings if not self.update_existing else []).append(
                    (number, 'name', f'phone already belongs to {guardian.full_name}; the name on record is kept'))
            if link is not None:
                changed = (link.relation != relation or link.is_primary != is_primary
                           or guardian.full_name != name or (email and guardian.email != email))
                plan.actions.append({'row': number, 'action': 'update' if changed else 'nochange', 'data': data})
            else:
                plan.actions.append({'row': number, 'action': 'create', 'data': data})
        return plan

    def apply(self, plan):
        refs, created, updated, unchanged = {'guardians': [], 'links': []}, 0, 0, 0
        cache = {g.phone: g for g in Guardian.objects.all()}
        for item in plan.actions:
            d = item['data']
            if item['action'] == 'nochange':
                unchanged += 1
                continue
            guardian = cache.get(d['phone'])
            if guardian is None:
                guardian = Guardian(full_name=d['name'], phone=d['phone'], email=d['email'])  # consent stays "unknown"
                guardian.save()
                cache[d['phone']] = guardian
                refs['guardians'].append(guardian.pk)
            elif self.update_existing and item['action'] == 'update':
                guardian.full_name = d['name']
                if d['email']:
                    guardian.email = d['email']
                guardian.save()
            link = StudentGuardian.objects.filter(student_id=d['student_id'], guardian=guardian).first()
            if link is None:
                link = StudentGuardian(student_id=d['student_id'], guardian=guardian, relation=d['relation'],
                                       is_primary=d['is_primary'])
                link.save()
                refs['links'].append(link.pk)
                created += 1
            else:
                link.relation, link.is_primary = d['relation'], d['is_primary']
                link.save()
                updated += 1
        return refs, created, updated, unchanged


IMPORTERS = {'students': StudentImporter, 'parents': ParentImporter, 'staff': StaffImporter}


@transaction.atomic
def apply_plan(importer, plan):
    """All rows or none: any exception rolls the whole import back."""
    return importer.apply(plan)
