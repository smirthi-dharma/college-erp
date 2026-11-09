"""The only way to message a guardian on WhatsApp.

A guardian who has not said yes must never receive a message (DPDP Act consent). Every sender (absence digest, fee
reminder, marks alert) goes through send_whatsapp(); nothing else may call _transport. A test enforces both.
"""


class ConsentRequired(Exception):
    """Raised instead of sending when the guardian's consent is not exactly "yes"."""


def can_message(guardian):
    from .models import Guardian
    current = Guardian.objects.filter(pk=guardian.pk).values_list('whatsapp_consent', flat=True).first()
    return current == Guardian.CONSENT_YES  # re-read from the database: a stale object must not outvote a withdrawal


def _transport(phone, text):  # pragma: no cover - the WhatsApp Cloud API is connected in pilot item 7
    raise NotImplementedError('WhatsApp Cloud API is not connected yet.')


def send_whatsapp(guardian, text):
    if not can_message(guardian):
        raise ConsentRequired('Guardian #%s has not consented to WhatsApp messages.' % guardian.pk)
    return _transport(guardian.phone, text)
