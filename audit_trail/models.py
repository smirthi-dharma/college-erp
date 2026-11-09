from django.db import models
from django.utils import timezone


class AuditLogImmutable(Exception):
    """Audit entries are append-only."""


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditLogImmutable('Audit entries cannot be modified.')

    def delete(self):
        raise AuditLogImmutable('Audit entries cannot be deleted.')

    def bulk_update(self, *args, **kwargs):
        raise AuditLogImmutable('Audit entries cannot be modified.')


class AuditLog(models.Model):
    """Append-only record of who changed what, when and from where.

    Lives in each college's own schema. Actor details are copied (not a foreign key) so the
    record survives deletion of the user. Personal data and secrets are never stored in
    ``changes`` (see audit_trail.signals.MASKED_FIELDS / REDACTED_FIELDS). A database trigger
    (migration 0002) also blocks UPDATE, DELETE and TRUNCATE.
    """

    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'
        LOGIN = 'login', 'Login'
        LOGOUT = 'logout', 'Logout'
        LOGIN_FAILED = 'login_failed', 'Login failed'
        LOCKED_OUT = 'locked_out', 'Locked out (too many failed logins)'

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=16, choices=Action.choices, db_index=True)

    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)
    actor_role = models.CharField(max_length=1, blank=True)
    source = models.CharField(max_length=10, default='web')  # "web" or "system" (commands, jobs, shell)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=300, blank=True)

    model_label = models.CharField(max_length=100, blank=True, db_index=True)
    object_pk = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ['-timestamp', '-id']
        indexes = [models.Index(fields=['model_label', 'object_pk'])]

    def __str__(self):
        return '%s %s %s #%s by %s' % (self.timestamp.strftime('%Y-%m-%d %H:%M:%S'), self.action,
                                       self.model_label, self.object_pk, self.actor_email or self.source)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AuditLogImmutable('Audit entries cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutable('Audit entries cannot be deleted.')
