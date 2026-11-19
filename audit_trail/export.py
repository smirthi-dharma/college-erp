"""Record that someone downloaded data (an Excel file), with who, from where, and which data: part of knowing who took what."""
from . import context
from .models import AuditLog
from .signals import _actor


def log_export(label, detail, changes=None):
    """One audit entry per download. `label` names what was exported, `detail` says which (for example the term)."""
    return AuditLog.objects.create(action=AuditLog.Action.EXPORT, model_label=label[:100], object_repr=detail[:200],
                                   changes=changes or {}, **_actor(context.current_request()))
