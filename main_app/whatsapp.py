"""The only way to message a guardian on WhatsApp.

Two rules are enforced here and by tests:

* A guardian who has not said yes never receives a message (DPDP Act consent): send_template() re-reads the consent
  from the database and refuses unless it is exactly "yes".
* Only pre-approved templates can be sent. There is no free-text path: every sender (absence digest, later fee
  reminders and marks alerts) names a template key from TEMPLATES, and nothing else may call _transport.

Errors tell the caller what to do next: TransientError (safe to retry: the request was not accepted), PermanentError
(do not retry), AuthError (the token is bad: stop the whole run), UnknownOutcome (the request may have been
accepted: do NOT retry, a second send could reach the parent twice).
"""
import logging
import os
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# template key -> language code -> template name approved in Meta's WhatsApp Manager. Four body variables each.
TEMPLATES = {
    'absence_digest': {'en': 'absence_digest_en', 'ta': 'absence_digest_ta'},
}
LANGUAGES = ('en', 'ta')
API_VERSION = os.environ.get('WHATSAPP_API_VERSION', 'v21.0')
GRAPH_URL = 'https://graph.facebook.com/%s/%s/messages'
TIMEOUT = (5, 15)  # connect, read seconds


class ConsentRequired(Exception):
    """Raised instead of sending when the guardian's consent is not exactly "yes"."""


class TemplateNotApproved(Exception):
    """Unknown template key, or no approved template in that language."""


class TransientError(Exception):
    """Not accepted by WhatsApp; retrying later is safe."""


class PermanentError(Exception):
    """Refused for a reason retrying will not fix (not on WhatsApp, template rejected, ...)."""


class AuthError(PermanentError):
    """The access token or phone number id is wrong or expired."""


class UnknownOutcome(Exception):
    """The request may or may not have been accepted (timeout while waiting). Never retried automatically."""


@dataclass(frozen=True)
class Credentials:
    phone_number_id: str
    access_token: str

    def __repr__(self):  # never print the token
        return 'Credentials(phone_number_id=%r, access_token=****%s)' % (self.phone_number_id, self.access_token[-4:])


@dataclass(frozen=True)
class Sent:
    wamid: str
    category: str = ''


def platform_credentials():
    return Credentials(os.environ.get('WHATSAPP_PHONE_NUMBER_ID', ''), os.environ.get('WHATSAPP_ACCESS_TOKEN', ''))


def can_message(guardian):
    from .models import Guardian
    current = Guardian.objects.filter(pk=guardian.pk).values_list('whatsapp_consent', flat=True).first()
    return current == Guardian.CONSENT_YES  # re-read from the database: a stale object must not outvote a withdrawal


def template_name(template_key, language):
    try:
        return TEMPLATES[template_key][language]
    except KeyError:
        raise TemplateNotApproved('No approved template "%s" in language "%s".' % (template_key, language)) from None


def _error(response):
    """(readable text, Meta error code or None). Never includes the request, so the token cannot leak."""
    try:
        error = response.json().get('error', {})
        return '%s (code %s)' % (error.get('message', 'error'), error.get('code', response.status_code)), error.get('code')
    except (ValueError, AttributeError):
        return 'HTTP %s' % response.status_code, None


def _transport(phone, name, language, params, credentials):  # pragma: no cover - needs the network; tests replace it
    """Call the WhatsApp Cloud API. `phone` is +91XXXXXXXXXX; `params` fill the template's body variables in order."""
    credentials = credentials or platform_credentials()
    if not credentials.phone_number_id or not credentials.access_token:
        raise AuthError('WhatsApp is not configured (phone number id or access token missing).')
    body = {
        'messaging_product': 'whatsapp', 'to': phone.lstrip('+'), 'type': 'template',
        'template': {'name': name, 'language': {'code': language},
                     'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': p} for p in params]}]},
    }
    try:
        response = requests.post(GRAPH_URL % (API_VERSION, credentials.phone_number_id), json=body, timeout=TIMEOUT,
                                 headers={'Authorization': 'Bearer ' + credentials.access_token})
    except (requests.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
        raise TransientError('Could not connect: %s' % type(exc).__name__) from None  # nothing was sent
    except requests.ReadTimeout:
        raise UnknownOutcome('Timed out waiting for WhatsApp; the message may have been sent.') from None
    except requests.RequestException as exc:
        raise UnknownOutcome('Request failed after sending: %s' % type(exc).__name__) from None
    if response.status_code == 200:
        data = response.json()
        return Sent(wamid=data['messages'][0]['id'])
    text, code = _error(response)
    if response.status_code in (401, 403) or code == 190:  # 190 = invalid or expired access token
        raise AuthError(text)
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientError(text)
    raise PermanentError(text)


def send_template(guardian, template_key, language, params, credentials=None):
    """Send an approved template to a guardian who has said yes. Returns Sent. See the module docstring for errors."""
    if not can_message(guardian):
        raise ConsentRequired('Guardian #%s has not consented to WhatsApp messages.' % guardian.pk)
    name = template_name(template_key, language)
    return _transport(guardian.phone, name, language, list(params), credentials)
