"""Write an AuditLog row for every create / update / delete of an audited model, and for logins.

Audited models: every model of the apps listed in settings.AUDIT_APPS. Rows are written in the
same database transaction as the change, so a rolled-back change leaves no entry, and a failure
to write the entry stops the change (fail closed).

Known limit: ``QuerySet.update()``, ``bulk_create()`` and raw SQL do not send signals and are not
logged. Application code must use ``save()`` / ``delete()``; the audit tests scan for the others.
"""
import logging

from axes.signals import user_locked_out
from django.apps import apps
from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save

from . import context
from .ip import get_client_ip
from .models import AuditLog

logger = logging.getLogger(__name__)

# Never stored, not even as a marker of the old value.
REDACTED_FIELDS = {'password', 'fcm_token'}
# Personal data: we record that the field changed, not its value, so that the log never
# blocks an erasure request (DPDP Act) and never leaks names/addresses to log readers.
MASKED_FIELDS = {'first_name', 'last_name', 'email', 'address', 'gender', 'profile_pic'}
# Bookkeeping that changes on every login/save and says nothing about intent.
IGNORED_FIELDS = {'last_login', 'updated_at', 'created_at'}

REDACTED = '[redacted]'
MASKED = '[personal data]'


def _audited_models():
    labels = getattr(settings, 'AUDIT_APPS', [])
    return [m for label in labels for m in apps.get_app_config(label).get_models()]


def connect():
    for model in _audited_models():
        uid = 'audit_%s' % model._meta.label_lower
        pre_save.connect(_pre_save, sender=model, dispatch_uid=uid + '_pre', weak=False)
        post_save.connect(_post_save, sender=model, dispatch_uid=uid + '_post', weak=False)
        post_delete.connect(_post_delete, sender=model, dispatch_uid=uid + '_del', weak=False)
    user_logged_in.connect(_logged_in, dispatch_uid='audit_login', weak=False)
    user_logged_out.connect(_logged_out, dispatch_uid='audit_logout', weak=False)
    user_login_failed.connect(_login_failed, dispatch_uid='audit_login_failed', weak=False)
    user_locked_out.connect(_locked_out, dispatch_uid='audit_locked_out', weak=False)


# --- helpers --------------------------------------------------------------------------

def _actor(request, user=None):
    """Return the actor/request columns for a new entry."""
    if request is None and user is None:
        return {'source': 'system'}
    data = {'source': 'web'}
    if request is not None:
        data.update(ip_address=get_client_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', '')[:200],
                    request_method=request.method, request_path=request.path[:300])
        user = user or getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        data.update(actor_id=user.pk, actor_email=user.email, actor_role=str(getattr(user, 'user_type', '')))
    return data


def _write(action, **fields):
    AuditLog.objects.create(action=action, **_actor(context.current_request(), fields.pop('user', None)), **fields)


def _snapshot(instance):
    """{field attname: JSON-safe value} for the model's concrete fields, with secrets/PII masked."""
    values = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key or field.name in IGNORED_FIELDS:
            continue
        if field.name in REDACTED_FIELDS:
            values[field.attname] = REDACTED
        elif field.name in MASKED_FIELDS:
            values[field.attname] = MASKED
        else:
            raw = field.value_from_object(instance)
            values[field.attname] = None if raw is None else str(field.to_python(raw))
    return values


def _raw_values(instance):
    """Unmasked values, used only to decide whether a masked/redacted field changed."""
    return {f.attname: f.value_from_object(instance) for f in instance._meta.concrete_fields
            if not f.primary_key and f.name not in IGNORED_FIELDS}


def _base(instance, action):
    return {'model_label': instance._meta.label, 'object_pk': str(instance.pk),
            'object_repr': '%s #%s' % (instance._meta.object_name, instance.pk)}


# --- model signals ---------------------------------------------------------------------

def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or instance.pk is None:
        return
    previous = sender._base_manager.filter(pk=instance.pk).first()
    instance._audit_previous = previous  # None when the row is new (explicit primary key)


def _post_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    previous = getattr(instance, '_audit_previous', None)
    instance._audit_previous = None
    if created or previous is None:
        changes = {name: [None, value] for name, value in _snapshot(instance).items() if value not in (None, '')}
        _write(AuditLog.Action.CREATE, changes=changes, **_base(instance, 'create'))
        return
    old_raw, new_raw = _raw_values(previous), _raw_values(instance)
    old_view, new_view = _snapshot(previous), _snapshot(instance)
    changes = {name: [old_view[name], new_view[name]] for name in new_view if old_raw[name] != new_raw[name]}
    if changes:  # a save() that changed nothing is not an event
        _write(AuditLog.Action.UPDATE, changes=changes, **_base(instance, 'update'))


def _post_delete(sender, instance, **kwargs):
    changes = {name: [value, None] for name, value in _snapshot(instance).items() if value not in (None, '')}
    _write(AuditLog.Action.DELETE, changes=changes, **_base(instance, 'delete'))


# --- authentication signals ------------------------------------------------------------

def _logged_in(sender, request, user, **kwargs):
    _write(AuditLog.Action.LOGIN, user=user, **_base(user, 'login'))


def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        _write(AuditLog.Action.LOGOUT, user=user, **_base(user, 'logout'))


def _login_failed(sender, credentials, request=None, **kwargs):
    # Only the email that was tried; the password is never read or stored.
    email = str((credentials or {}).get('email') or (credentials or {}).get('username') or '')[:254]
    AuditLog.objects.create(action=AuditLog.Action.LOGIN_FAILED, actor_email=email,
                            **_actor(request or context.current_request()))


def _locked_out(sender, request=None, username=None, ip_address=None, **kwargs):
    """django-axes refused a login because of repeated failures."""
    AuditLog.objects.create(action=AuditLog.Action.LOCKED_OUT, actor_email=str(username or '')[:254],
                            **_actor(request or context.current_request()))
