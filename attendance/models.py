"""Attendance rules (configuration per Regulation), condonations and one-day substitutes.

Nothing about 75% is hard-coded: services read an AttendanceRule. Every model here is audited.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class AttendanceRule(models.Model):
    PER_SUBJECT, OVERALL = 'per_subject', 'overall'
    BASIS = [(PER_SUBJECT, 'Per subject'), (OVERALL, 'Overall')]

    regulation = models.OneToOneField('academics.Regulation', on_delete=models.CASCADE)
    required_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('75'))
    warning_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('80'))
    condonable_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('65'))
    basis = models.CharField(max_length=12, choices=BASIS, default=PER_SUBJECT)
    on_duty_counts_as_present = models.BooleanField(default=True)
    edit_window_days = models.PositiveSmallIntegerField(default=3)

    def __str__(self):
        return f'Attendance rule for {self.regulation}'

    def clean(self):
        if not (Decimal('0') <= self.condonable_percent <= self.required_percent <= self.warning_percent <= Decimal('100')):
            raise ValidationError('Percentages must satisfy: 0 <= condonable <= required <= warning <= 100.')


def default_rule():
    """The rule used for a regulation that has none saved yet (not stored until the college admin saves it)."""
    return AttendanceRule()


def rule_for(regulation):
    if regulation is None:
        return default_rule()
    return AttendanceRule.objects.filter(regulation=regulation).first() or default_rule()


class Condonation(models.Model):
    """The college admin excuses a shortage that lies inside the condonable band. Revocable."""
    student = models.ForeignKey('main_app.Student', on_delete=models.CASCADE)
    offering = models.ForeignKey('academics.SubjectOffering', null=True, blank=True, on_delete=models.CASCADE)  # null = overall
    reason = models.TextField()
    percentage_at_approval = models.DecimalField(max_digits=5, decimal_places=2)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(auto_now_add=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['student', 'offering'], condition=Q(revoked_at__isnull=True, offering__isnull=False),
                                    name='one_active_condonation_per_subject'),
            models.UniqueConstraint(fields=['student'], condition=Q(revoked_at__isnull=True, offering__isnull=True),
                                    name='one_active_overall_condonation'),
        ]

    def __str__(self):
        return 'Condonation #%s' % self.pk

    @property
    def active(self):
        return self.revoked_at is None


class SubstituteAssignment(models.Model):
    """For one class (offering, date, period) only the substitute may take or edit the attendance."""
    offering = models.ForeignKey('academics.SubjectOffering', on_delete=models.CASCADE)
    date = models.DateField()
    period = models.PositiveSmallIntegerField(default=1)
    substitute = models.ForeignKey('main_app.Staff', on_delete=models.CASCADE)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['offering', 'date', 'period'], name='one_substitute_per_class')]

    def __str__(self):
        return 'Substitute #%s' % self.pk
