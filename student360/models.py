"""Who looked at a student's personal data (DPDP: know who saw what).

This is a log, like audit_trail: append-only, so it is deliberately not in AUDIT_APPS (auditing a log would
only duplicate every row). Viewer details are copied, so entries survive deletion of the user or the student.
"""
from django.db import models
from django.utils import timezone


class AccessLogImmutable(Exception):
    pass


class AccessLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AccessLogImmutable('Access log entries cannot be modified.')

    def delete(self):
        raise AccessLogImmutable('Access log entries cannot be deleted.')

    def bulk_update(self, *args, **kwargs):
        raise AccessLogImmutable('Access log entries cannot be modified.')


class StudentAccessLog(models.Model):
    ALLOWED, REFUSED = 'allowed', 'refused'
    OUTCOME = [(ALLOWED, 'Allowed'), (REFUSED, 'Refused')]

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    viewer_id = models.BigIntegerField(null=True, blank=True)
    viewer_email = models.CharField(max_length=254, blank=True)
    viewer_role = models.CharField(max_length=1, blank=True)
    student_id = models.BigIntegerField(db_index=True)
    student_repr = models.CharField(max_length=200, blank=True)
    outcome = models.CharField(max_length=7, choices=OUTCOME, default=ALLOWED, db_index=True)

    objects = AccessLogQuerySet.as_manager()

    class Meta:
        ordering = ['-timestamp', '-id']

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} {self.viewer_email} viewed {self.student_repr} ({self.outcome})'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AccessLogImmutable('Access log entries cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AccessLogImmutable('Access log entries cannot be deleted.')
