"""Bulk inserts that still write the audit log.

``bulk_create()`` sends no signals, so a plain bulk insert would leave no trace. ``bulk_create_audited`` inserts the rows
and then writes one "create" entry per row, in the same transaction, with the same content ``save()`` would have logged
(personal data masked, actor and request copied). Two queries for any number of rows instead of about four per row.

Use it for audited models only; ``NoUnloggedWritesTests`` fails the build for any other ``bulk_create``.
"""
from django.db import transaction

from . import context
from .models import AuditLog
from .signals import _actor, _base, _snapshot

BATCH = 500


def bulk_create_audited(model, instances):
    """Insert `instances` of an audited model and log each one. Returns the saved instances (with primary keys)."""
    instances = list(instances)
    if not instances:
        return []
    actor = _actor(context.current_request())
    with transaction.atomic():
        saved = model.objects.bulk_create(instances, batch_size=BATCH)
        entries = [AuditLog(action=AuditLog.Action.CREATE, **actor, **_base(obj, 'create'),
                            changes={name: [None, value] for name, value in _snapshot(obj).items() if value not in (None, '')})
                   for obj in saved]
        AuditLog.objects.bulk_create(entries, batch_size=BATCH)
    return saved
