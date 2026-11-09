"""Institution structure: departments, programmes, regulations, batches, sections,
subjects (catalogue), terms, offerings (who teaches what to whom) and enrolments.

Lives in each college's own schema. Never use queryset.update()/bulk_create() on these
models: the audit log listens to save()/delete().
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Department(models.Model):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class Programme(models.Model):
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    duration_semesters = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)])

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class Regulation(models.Model):
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT)
    name = models.CharField(max_length=60)  # e.g. R2022
    effective_from = models.DateField()

    class Meta:
        ordering = ['programme__code', '-effective_from']
        constraints = [models.UniqueConstraint(fields=['programme', 'name'], name='uniq_regulation_per_programme')]

    def __str__(self):
        return f'{self.programme.code} {self.name}'


class Batch(models.Model):
    """An admission batch of a programme (e.g. B.Tech CSE, joined 2024)."""
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT)
    regulation = models.ForeignKey(Regulation, on_delete=models.PROTECT)
    start_year = models.PositiveSmallIntegerField(validators=[MinValueValidator(1950), MaxValueValidator(2100)])

    class Meta:
        ordering = ['programme__code', '-start_year']
        verbose_name_plural = 'batches'
        constraints = [models.UniqueConstraint(fields=['programme', 'start_year'], name='uniq_batch_per_programme_year')]

    def __str__(self):
        return f'{self.programme.code} {self.start_year}'

    def clean(self):
        if self.programme_id and self.regulation_id and self.regulation.programme_id != self.programme_id:
            raise ValidationError({'regulation': 'The regulation belongs to a different programme.'})


class Section(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    name = models.CharField(max_length=20)

    class Meta:
        ordering = ['batch__programme__code', '-batch__start_year', 'name']
        constraints = [models.UniqueConstraint(fields=['batch', 'name'], name='uniq_section_per_batch')]

    def __str__(self):
        return f'{self.batch} {self.name}'


class Subject(models.Model):
    """A paper in a regulation's catalogue. Who teaches it, to whom and when is a SubjectOffering."""
    THEORY, LAB = 'theory', 'lab'
    KIND = [(THEORY, 'Theory'), (LAB, 'Lab')]

    programme = models.ForeignKey(Programme, on_delete=models.PROTECT)
    regulation = models.ForeignKey(Regulation, on_delete=models.PROTECT)
    semester = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    credits = models.DecimalField(max_digits=4, decimal_places=1, validators=[MinValueValidator(Decimal('0'))])
    kind = models.CharField(max_length=10, choices=KIND, default=THEORY)

    class Meta:
        ordering = ['programme__code', 'semester', 'code']
        constraints = [
            models.UniqueConstraint(fields=['regulation', 'code'], name='uniq_subject_code_per_regulation'),
            models.CheckConstraint(condition=Q(credits__gte=0), name='subject_credits_non_negative'),
            models.CheckConstraint(condition=Q(semester__gte=1, semester__lte=12), name='subject_semester_range'),
        ]

    def __str__(self):
        return f'{self.code} {self.name}'

    def clean(self):
        if self.programme_id and self.regulation_id and self.regulation.programme_id != self.programme_id:
            raise ValidationError({'regulation': 'The regulation belongs to a different programme.'})
        if self.programme_id and self.semester and self.semester > self.programme.duration_semesters:
            raise ValidationError({'semester': f'The programme has only {self.programme.duration_semesters} semesters.'})


class AcademicTerm(models.Model):
    name = models.CharField(max_length=60, unique=True)  # e.g. 2026-27 ODD
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'The term cannot end before it starts.'})


class SubjectOffering(models.Model):
    CORE, ELECTIVE = 'core', 'elective'
    KIND = [(CORE, 'Core'), (ELECTIVE, 'Elective')]

    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    section = models.ForeignKey(Section, on_delete=models.PROTECT)
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT)
    faculty = models.ForeignKey('main_app.Staff', on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=KIND, default=CORE)

    class Meta:
        ordering = ['-term__start_date', 'section__batch__programme__code', 'subject__code']
        constraints = [models.UniqueConstraint(fields=['subject', 'section', 'term'], name='uniq_offering')]

    def __str__(self):
        return f'{self.subject.code} / {self.section} / {self.term}'

    def clean(self):
        if self.subject_id and self.section_id and self.subject.regulation_id != self.section.batch.regulation_id:
            raise ValidationError('The subject belongs to a different regulation than this section.')


class Enrolment(models.Model):
    """A student taking an offering. Withdrawal keeps the row, so marks and attendance stay linked."""
    ACTIVE, WITHDRAWN = 'active', 'withdrawn'
    STATUS = [(ACTIVE, 'Active'), (WITHDRAWN, 'Withdrawn')]
    AUTO, MANUAL, IMPORT, TRANSFER = 'auto', 'manual', 'import', 'transfer'
    SOURCE = [(AUTO, 'Automatic'), (MANUAL, 'HoD'), (IMPORT, 'Excel import'), (TRANSFER, 'Section change')]

    offering = models.ForeignKey(SubjectOffering, on_delete=models.PROTECT)
    student = models.ForeignKey("main_app.Student", on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS, default=ACTIVE)
    source = models.CharField(max_length=10, choices=SOURCE, default=MANUAL)
    enrolled_on = models.DateField(auto_now_add=True)
    withdrawn_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['offering', 'student']
        constraints = [models.UniqueConstraint(fields=['offering', 'student'], name='uniq_enrolment')]

    def __str__(self):
        return f'{self.student} -> {self.offering}'

    @property
    def outside_section(self):
        """Arrear/repeat students sit in an offering of a section other than their own."""
        return self.student.section_id != self.offering.section_id
