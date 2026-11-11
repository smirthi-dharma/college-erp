"""Running the absence digest: start a run, send each message once, record what happened. Runs inside one college.

Never-twice rests on three things: the unique (date, mode) on DigestRun, the unique (guardian, date, mode) on
DigestMessage, and a row lock plus the status `sending` written BEFORE the WhatsApp call. A message whose outcome is
unclear (timeout, a worker that died mid-send) becomes `unknown` and is never retried automatically.
"""
import datetime
import logging

from django.db import connection, transaction
from django.utils import timezone

from colleges.models import MessageRoute
from main_app import whatsapp

from . import digest, texts
from .models import CollegeSecret, DigestMessage, DigestRun, DigestSettings, UsageRate

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)  # seconds after attempt 1, 2, 3
BUILD_TIMEOUT = datetime.timedelta(minutes=10)  # a run still "building" after this is resumed
SENDING_TIMEOUT = datetime.timedelta(minutes=10)  # a message still "sending" after this is marked unknown
REQUEUE_AFTER = datetime.timedelta(minutes=30)  # a queued message nobody picked up is handed to a worker again
DEFAULT_CATEGORY = 'utility'  # Meta's category for these templates; the webhook corrects it from Meta's own report
TOKEN_SECRET = 'whatsapp_access_token'  # noqa: S105  # pragma: allowlist secret  (the name of the stored secret, not a secret)


# --- credentials -----------------------------------------------------------------------------------------------

def credentials_for(settings):
    """This college's own WhatsApp number and token if both are set, otherwise the platform's (from the environment)."""
    token = CollegeSecret.get(TOKEN_SECRET) if settings.phone_number_id else None
    if settings.phone_number_id and token:
        return whatsapp.Credentials(settings.phone_number_id, token)
    return None  # the sender then uses the platform credentials from the environment


# --- starting a run --------------------------------------------------------------------------------------------

def start_run(now=None, ignore_time=False):
    """Create today's run and its message rows if the digest is on and due. Returns (run, [message ids to send]).

    Returns (None, []) when there is nothing to do: switched off, needs attention, too early, or already run.
    Safe to call every minute and from several workers: only one of them gets to build a given day.
    """
    now = now or timezone.now()
    settings = DigestSettings.current()
    if not settings.enabled or settings.state != DigestSettings.OK:
        return None, []
    local = timezone.localtime(now)
    day = local.date()
    if not ignore_time and local.time() < settings.send_time:
        return None, []
    mode = DigestRun.DRY if settings.dry_run else DigestRun.LIVE
    run, created = DigestRun.objects.get_or_create(date=day, mode=mode, defaults={'started_at': now})
    if not created:
        if run.state == DigestRun.DONE or run.started_at > now - BUILD_TIMEOUT:
            return None, []
        run.started_at = now  # the builder died: resume (rows already written are kept, the unique key stops duplicates)
        run.save(update_fields=['started_at'])
    return run, build_messages(run, settings, now)


def build_messages(run, settings, now):
    day, mode = run.date, run.mode
    absent = digest.absences_on(day)
    by_guardian, run.unreachable_students = digest.guardians_for(absent, settings.primary_guardian_only)
    college_name = getattr(connection.tenant, 'name', '')
    to_send = []
    for guardian, students in by_guardian.items():
        language = digest.language_for(guardian, settings.default_language)
        params = digest.params_for(guardian, students, absent, day, college_name, language)
        status = DigestMessage.DRY_RUN if mode == DigestRun.DRY else DigestMessage.QUEUED
        if not whatsapp.can_message(guardian):
            status = DigestMessage.SKIPPED_CONSENT
        elif not guardian.phone:
            status = DigestMessage.SKIPPED_PHONE
        message, created = DigestMessage.objects.get_or_create(
            guardian=guardian, digest_date=day, mode=mode,
            defaults={'run': run, 'language': language, 'params': params, 'rendered_text': texts.render(language, params),
                      'template_name': whatsapp.template_name('absence_digest', language), 'status': status})
        if created and status == DigestMessage.QUEUED:
            to_send.append(message.pk)
    run.state, run.finished_at = DigestRun.DONE, timezone.now()
    run.save()
    return to_send


# --- sending one message ---------------------------------------------------------------------------------------

def send_message(message_id):
    """Send one queued live message. Returns ('done' | 'retry' | 'skip', retry delay in seconds or None)."""
    with transaction.atomic():
        message = DigestMessage.objects.select_for_update().select_related('guardian').filter(pk=message_id).first()
        if message is None or message.status != DigestMessage.QUEUED or message.mode != DigestRun.LIVE:
            return 'skip', None  # already handled by another worker, or not for sending
        settings = DigestSettings.current()
        if not settings.enabled:
            return _finish(message, DigestMessage.CANCELLED, 'The digest was switched off.')
        if settings.state != DigestSettings.OK:
            return _finish(message, DigestMessage.CANCELLED, 'Stopped: %s' % settings.state_reason)
        if not whatsapp.can_message(message.guardian):
            return _finish(message, DigestMessage.SKIPPED_CONSENT, 'Consent was withdrawn before sending.')
        message.status, message.attempts = DigestMessage.SENDING, message.attempts + 1
        message.save()
    # The call happens outside the transaction, after `sending` is committed: a crash from here on leaves `sending`,
    # which the sweeper turns into `unknown`. It is never silently sent twice.
    try:
        sent = whatsapp.send_template(message.guardian, 'absence_digest', message.language, message.params,
                                      credentials_for(settings))
    except whatsapp.ConsentRequired:
        return _finish(message, DigestMessage.SKIPPED_CONSENT, 'Consent was withdrawn before sending.')
    except whatsapp.TransientError as exc:
        if message.attempts < MAX_ATTEMPTS:
            message.status, message.last_error = DigestMessage.QUEUED, str(exc)[:400]
            message.save()
            return 'retry', RETRY_DELAYS[message.attempts - 1]
        return _finish(message, DigestMessage.FAILED, 'Gave up after %d attempts: %s' % (message.attempts, exc))
    except whatsapp.AuthError as exc:
        _needs_attention(settings, 'WhatsApp rejected the access token: %s' % exc)
        return _finish(message, DigestMessage.FAILED, str(exc))
    except (whatsapp.PermanentError, whatsapp.TemplateNotApproved) as exc:
        return _finish(message, DigestMessage.FAILED, str(exc))
    except whatsapp.UnknownOutcome as exc:
        return _finish(message, DigestMessage.UNKNOWN, str(exc))
    except Exception as exc:
        logger.exception('Digest message %s: unexpected error during send', message.pk)
        return _finish(message, DigestMessage.UNKNOWN, 'Unexpected error while sending: %s' % type(exc).__name__)
    with transaction.atomic():
        category = sent.category or DEFAULT_CATEGORY
        message.status, message.wamid, message.sent_at, message.category = DigestMessage.SENT, sent.wamid, timezone.now(), category
        message.billed_rate = UsageRate.rate_on(category, message.digest_date)
        message.last_error = ''
        message.save()
        MessageRoute.objects.get_or_create(wamid=sent.wamid, defaults={'college': connection.tenant})
    return 'done', None


def _finish(message, status, error):
    message.status, message.last_error = status, str(error)[:400]
    message.save()
    return 'done', None


def _needs_attention(settings, reason):
    settings.state, settings.state_reason = DigestSettings.NEEDS_ATTENTION, reason[:300]
    settings.save()


def sweep(now=None):
    """Housekeeping, run every minute: `sending` for too long -> unknown; forgotten `queued` -> ids to send again."""
    now = now or timezone.now()
    for message in DigestMessage.objects.filter(status=DigestMessage.SENDING, updated_at__lt=now - SENDING_TIMEOUT):
        message.status, message.last_error = DigestMessage.UNKNOWN, 'The worker stopped while sending; it may have been sent.'
        message.save()
    return list(DigestMessage.objects.filter(status=DigestMessage.QUEUED, mode=DigestRun.LIVE,
                                             updated_at__lt=now - REQUEUE_AFTER).values_list('pk', flat=True))
