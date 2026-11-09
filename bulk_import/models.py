from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    """One confirmed import. The file itself is never kept, only its SHA-256. Audited like every other model."""
    STUDENTS, PARENTS, STAFF = 'students', 'parents', 'staff'
    KIND = [(STUDENTS, 'Students'), (PARENTS, 'Parents'), (STAFF, 'Staff')]
    APPLIED, ROLLED_BACK = 'applied', 'rolled_back'
    STATUS = [(APPLIED, 'Applied'), (ROLLED_BACK, 'Rolled back')]

    kind = models.CharField(max_length=10, choices=KIND)
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    update_existing = models.BooleanField(default=False)
    rows = models.PositiveIntegerField()
    created = models.PositiveIntegerField()
    updated = models.PositiveIntegerField()
    unchanged = models.PositiveIntegerField()
    # What this import created, so a rollback removes exactly that: {"users": [...], "guardians": [...], "links": [...]}
    created_refs = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=STATUS, default=APPLIED)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    imported_at = models.DateTimeField(auto_now_add=True)
    rolled_back_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-imported_at']
        verbose_name_plural = 'import batches'

    def __str__(self):
        return f'{self.kind} import of {self.rows} rows'
