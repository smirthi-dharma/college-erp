"""Recording a guardian's WhatsApp consent, with the evidence DPDP asks for.

The one way to change Guardian.whatsapp_consent. Every change writes an append-only ConsentRecord (who, when, how, optional
scan of the signed form) in the same transaction, so there is never an answer without its evidence.
"""
from django.db import transaction
from django.utils import timezone

from .models import ConsentRecord, CustomUser, Guardian

ADMIN_METHODS = (ConsentRecord.PAPER_FORM, ConsentRecord.VERBAL)  # what a college admin may record from the screen
SCAN_TYPES = {'.pdf': (b'%PDF-',), '.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',)}
SCAN_MAX_BYTES = 5 * 1024 * 1024


class ConsentError(ValueError):
    """The record is refused; the message is safe to show to the admin."""


def validate_scan(upload):
    """Accept only a PDF, PNG or JPEG of at most 5 MB whose first bytes match its extension."""
    import os
    extension = os.path.splitext(upload.name or '')[1].lower()
    if extension not in SCAN_TYPES:
        raise ConsentError('The scan must be a PDF, PNG or JPEG file.')
    if upload.size > SCAN_MAX_BYTES:
        raise ConsentError('The scan is larger than 5 MB.')
    head = upload.read(8)
    upload.seek(0)
    if not any(head.startswith(magic) for magic in SCAN_TYPES[extension]):
        raise ConsentError('The file is not really a %s.' % extension.lstrip('.').upper())


@transaction.atomic
def record_consent(guardian, value, method, user=None, note='', scan=None, allow_self_service=False):
    """Record a decision and update the guardian's current answer. Returns the ConsentRecord."""
    note = (note or '').strip()
    if value not in (Guardian.CONSENT_YES, Guardian.CONSENT_NO):
        raise ConsentError('Choose yes or no.')
    if method not in dict(ConsentRecord.METHOD):
        raise ConsentError('Say how the consent was given.')
    if method == ConsentRecord.SELF_SERVICE and not allow_self_service:
        raise ConsentError('Parent self-service is recorded by the parent portal, not by staff.')
    if method == ConsentRecord.WHATSAPP_STOP:
        if user is not None or value != Guardian.CONSENT_NO:
            raise ConsentError('A WhatsApp STOP reply is recorded by the system, and only as a withdrawal.')
    elif method in ADMIN_METHODS:
        if user is None or user.user_type != CustomUser.COLLEGE_ADMIN:
            raise ConsentError('Only the college admin can record consent.')
    if method == ConsentRecord.VERBAL and not note:
        raise ConsentError('For verbal consent, write who gave it and who was present or how it was confirmed.')
    if scan is not None:
        validate_scan(scan)
    record = ConsentRecord(guardian=guardian, value=value, method=method, note=note[:300], scan=scan or None,
                           recorded_at=timezone.now())
    if user is not None:
        record.recorded_by_id, record.recorded_by_email, record.recorded_by_role = user.pk, user.email, str(user.user_type)
    else:
        record.recorded_by_email = 'system'
    record.save()
    guardian.whatsapp_consent = value
    guardian.consent_source = ('%s: %s' % (record.get_method_display(), note) if note else record.get_method_display())[:200]
    guardian.consent_recorded_at = record.recorded_at
    guardian.save()
    return record


def has_evidence(guardian):
    return guardian.consent_records.exists()
