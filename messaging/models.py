"""WhatsApp absence digest data (per college, tenant schema).

DigestSettings, UsageRate and CollegeSecret are configuration: they are audited (who changed what, never a secret).
DigestRun, DigestMessage and DeliveryEvent are system-written logs and are exempt from the audit log (audit_exempt)
because every status change would otherwise be written twice; DeliveryEvent is append-only.
"""
import datetime
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from . import crypto


class DigestSettings(models.Model):
    """One row per college. Safe defaults: nothing is sent until an admin turns it on and turns dry run off."""
    OK, NEEDS_ATTENTION = 'ok', 'needs_attention'
    LANGUAGE = [('en', 'English'), ('ta', 'Tamil')]

    enabled = models.BooleanField(default=False)
    dry_run = models.BooleanField(default=True)
    send_time = models.TimeField(default=datetime.time(17, 0), help_text='India time (IST).')
    default_language = models.CharField(max_length=2, choices=LANGUAGE, default='en')
    primary_guardian_only = models.BooleanField(default=True)
    # Optional own WhatsApp number for this college; blank = the platform number. The token is in CollegeSecret.
    phone_number_id = models.CharField(max_length=40, blank=True, default='')
    # Message rows (they hold child names) older than this are deleted every night.
    retention_months = models.PositiveSmallIntegerField(default=12, validators=[MinValueValidator(1), MaxValueValidator(60)])
    last_purge_at = models.DateTimeField(null=True, blank=True)
    last_purge_count = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=20, default=OK)
    state_reason = models.CharField(max_length=300, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'digest settings'

    def __str__(self):
        return 'Digest settings'

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CollegeSecret(models.Model):
    """A secret value, encrypted at rest. Audited without its value (audit_trail redacts `ciphertext`)."""
    name = models.CharField(max_length=60, unique=True)
    ciphertext = models.TextField()
    last4 = models.CharField(max_length=4, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '%s (****%s)' % (self.name, self.last4)

    @classmethod
    def put(cls, name, value):
        obj = cls.objects.filter(name=name).first() or cls(name=name)
        obj.ciphertext, obj.last4 = crypto.encrypt(value), value[-4:]
        obj.save()
        return obj

    @classmethod
    def get(cls, name):
        obj = cls.objects.filter(name=name).first()
        return crypto.decrypt(obj.ciphertext) if obj else None


class UsageRate(models.Model):
    """What one live message of a Meta category costs, from a date. Rates are data, never hard-coded."""
    category = models.CharField(max_length=20, default='utility')
    effective_from = models.DateField()
    rate = models.DecimalField(max_digits=8, decimal_places=4, help_text='Rupees per message.')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['category', 'effective_from'], name='uniq_rate_per_category_date')]
        ordering = ['category', '-effective_from']

    def __str__(self):
        return '%s from %s: %s' % (self.category, self.effective_from, self.rate)

    @classmethod
    def rate_on(cls, category, day):
        row = cls.objects.filter(category=category, effective_from__lte=day).order_by('-effective_from').first()
        return row.rate if row else Decimal('0')


class DigestRun(models.Model):
    """One college-day in one mode. The unique (date, mode) is what makes a run start once."""
    LIVE, DRY = 'live', 'dry_run'
    MODES = [(LIVE, 'Live'), (DRY, 'Dry run')]
    BUILDING, DONE = 'building', 'done'

    date = models.DateField()
    mode = models.CharField(max_length=8, choices=MODES)
    state = models.CharField(max_length=10, default=BUILDING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    unreachable_students = models.PositiveIntegerField(default=0)  # absent, but no guardian to tell (or none marked primary)

    audit_exempt = True

    class Meta:
        constraints = [models.UniqueConstraint(fields=['date', 'mode'], name='uniq_digest_run_per_day_and_mode')]
        ordering = ['-date', 'mode']

    def __str__(self):
        return 'Digest %s (%s)' % (self.date, self.mode)


class DigestMessage(models.Model):
    QUEUED, SENDING, SENT, DELIVERED, READ = 'queued', 'sending', 'sent', 'delivered', 'read'
    FAILED, UNKNOWN, CANCELLED, DRY_RUN = 'failed', 'unknown', 'cancelled', 'dry_run'
    SKIPPED_CONSENT, SKIPPED_PHONE = 'skipped_no_consent', 'skipped_no_phone'
    STATUS = [(s, s.replace('_', ' ')) for s in (QUEUED, SENDING, SENT, DELIVERED, READ, FAILED, UNKNOWN, CANCELLED,
                                                  DRY_RUN, SKIPPED_CONSENT, SKIPPED_PHONE)]
    RANK = {SENT: 1, DELIVERED: 2, READ: 3}  # a report can only move a message forward
    ACCEPTED = (SENT, DELIVERED, READ)  # Meta took it: counts as usage

    run = models.ForeignKey(DigestRun, on_delete=models.CASCADE, related_name='messages')
    guardian = models.ForeignKey('main_app.Guardian', on_delete=models.CASCADE)  # a DPDP deletion removes their messages
    digest_date = models.DateField(db_index=True)
    mode = models.CharField(max_length=8, choices=DigestRun.MODES)
    language = models.CharField(max_length=2, default='en')
    template_name = models.CharField(max_length=60, blank=True, default='')
    params = models.JSONField(default=list)
    rendered_text = models.TextField(blank=True, default='')  # what the parent sees, for the report and for dry runs
    status = models.CharField(max_length=20, choices=STATUS, default=QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=400, blank=True, default='')
    wamid = models.CharField(max_length=200, null=True, blank=True, unique=True)
    category = models.CharField(max_length=20, blank=True, default='')
    billed_rate = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    resend_count = models.PositiveSmallIntegerField(default=0)  # times an admin sent an "unclear" message again
    last_resent_at = models.DateTimeField(null=True, blank=True)
    last_resent_by = models.CharField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    audit_exempt = True

    class Meta:
        constraints = [models.UniqueConstraint(fields=['guardian', 'digest_date', 'mode'], name='uniq_message_per_guardian_day_mode')]
        ordering = ['digest_date', 'id']

    def __str__(self):
        return 'Message #%s %s' % (self.pk, self.status)


class DeliveryEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableEvent('Delivery events cannot be modified.')

    def delete(self):
        raise ImmutableEvent('Delivery events cannot be deleted.')


class ImmutableEvent(Exception):
    pass


class DeliveryEvent(models.Model):
    """Every status report Meta sent, kept as received. Append-only."""
    message = models.ForeignKey(DigestMessage, on_delete=models.CASCADE, related_name='events')
    status = models.CharField(max_length=20)
    meta_timestamp = models.CharField(max_length=20, blank=True, default='')
    detail = models.JSONField(default=dict)
    received_at = models.DateTimeField(default=timezone.now)

    objects = DeliveryEventQuerySet.as_manager()
    audit_exempt = True

    class Meta:
        ordering = ['received_at', 'id']
        constraints = [models.UniqueConstraint(fields=['message', 'status', 'meta_timestamp'], name='uniq_delivery_event')]

    def __str__(self):
        return 'Event %s for message #%s' % (self.status, self.message_id)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableEvent('Delivery events cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableEvent('Delivery events cannot be deleted.')
