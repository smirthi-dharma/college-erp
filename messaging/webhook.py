"""WhatsApp Cloud API webhook: delivery reports and replies.

Meta calls one URL for every college. A report is routed to its college through colleges.MessageRoute (public table,
message id -> college). Requests are accepted only with a valid X-Hub-Signature-256 made from the app secret.
"""
import hashlib
import hmac
import logging
import os

from django.db import transaction
from django_tenants.utils import tenant_context

from colleges.models import College, MessageRoute
from main_app.consent import record_consent
from main_app.models import ConsentRecord, Guardian

from . import texts
from .models import DeliveryEvent, DigestMessage, UsageRate

logger = logging.getLogger(__name__)


def signature_valid(body, header):
    secret = os.environ.get('WHATSAPP_APP_SECRET', '')
    if not secret or not header or not header.startswith('sha256='):
        return False  # fail closed: without a configured secret nothing is accepted
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len('sha256='):])


def verify_token_valid(token):
    expected = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')
    return bool(expected) and hmac.compare_digest(expected, token or '')


def process(payload):
    """Handle a parsed webhook payload. Returns the number of status reports applied."""
    applied = 0
    for entry in payload.get('entry', []) if isinstance(payload, dict) else []:
        for change in entry.get('changes', []):
            value = change.get('value', {})
            for status in value.get('statuses', []):
                applied += apply_status(status)
            for inbound in value.get('messages', []):
                handle_reply(inbound)
    return applied


def apply_status(status):
    wamid = status.get('id', '')
    route = MessageRoute.objects.select_related('college').filter(wamid=wamid).first()
    if route is None:
        logger.warning('WhatsApp status for an unknown message id was ignored.')
        return 0
    with tenant_context(route.college), transaction.atomic():
        message = DigestMessage.objects.select_for_update().filter(wamid=wamid).first()
        if message is None:
            logger.warning('WhatsApp status routed to a college without that message was ignored.')
            return 0
        name = str(status.get('status', ''))[:20]
        _, new = DeliveryEvent.objects.get_or_create(
            message=message, status=name, meta_timestamp=str(status.get('timestamp', ''))[:20],
            defaults={'detail': {k: status[k] for k in ('recipient_id', 'errors', 'pricing') if k in status}})
        if new:
            _move_forward(message, name, status)
        return 1 if new else 0


def _move_forward(message, name, status):
    rank = DigestMessage.RANK
    changeable = (DigestMessage.SENT, DigestMessage.DELIVERED, DigestMessage.READ, DigestMessage.SENDING, DigestMessage.UNKNOWN)
    if name in rank and message.status in changeable and rank[name] > rank.get(message.status, 0):
        message.status = name  # delivered is never overwritten by a late "sent"
    elif name == 'failed' and message.status in (DigestMessage.SENT, DigestMessage.SENDING, DigestMessage.UNKNOWN):
        errors = status.get('errors') or [{}]
        message.status = DigestMessage.FAILED
        message.last_error = ('%s (code %s)' % (errors[0].get('title') or errors[0].get('message', 'failed'),
                                                errors[0].get('code', '?')))[:400]
    pricing = status.get('pricing')
    if pricing:  # Meta says which category it billed and whether it was billable at all
        message.category = str(pricing.get('category') or message.category)[:20]
        message.billed_rate = UsageRate.rate_on(message.category, message.digest_date) if pricing.get('billable', True) else 0
    message.save()


def handle_reply(inbound):
    """A guardian replying STOP withdraws consent, in every college that has them (one number serves all)."""
    text = str((inbound.get('text') or {}).get('body', '')).strip().casefold()
    sender = ''.join(ch for ch in str(inbound.get('from', '')) if ch.isdigit())
    if text not in texts.STOP_WORDS or not sender:
        return
    phone = '+' + sender
    for college in College.objects.exclude(schema_name='public'):
        with tenant_context(college):
            for guardian in Guardian.objects.filter(phone=phone).exclude(whatsapp_consent=Guardian.CONSENT_NO):
                record_consent(guardian, Guardian.CONSENT_NO, ConsentRecord.WHATSAPP_STOP)
