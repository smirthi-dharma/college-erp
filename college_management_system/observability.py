"""Error tracking with Sentry, switched on only when SENTRY_DSN is set, and configured not to send personal data.

A college ERP holds students' and parents' personal data (DPDP Act), so an error report must carry the error and where it
happened, not who it happened to: no request bodies, cookies, headers, user, query strings or local variables, e-mail
addresses and phone numbers masked in messages, and the college's schema name attached as a tag so an error can be traced to
one tenant. Sentry's servers are outside India: with this scrubbing no personal data leaves, but see docs/BACKUPS.md
(Error tracking) for the region choice and for how to confirm it.
"""
import os
import re

EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
PHONE = re.compile(r'(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)')
REQUEST_PARTS = ('data', 'cookies', 'headers', 'query_string', 'env')


def mask(text):
    return PHONE.sub('[phone]', EMAIL.sub('[email]', text)) if isinstance(text, str) else text


def scrub_event(event, hint=None):
    """Sentry `before_send`: strip personal data from the event, tag the college, and return it (never drop it)."""
    request = event.get('request')
    if isinstance(request, dict):
        for part in REQUEST_PARTS:
            request.pop(part, None)
    event.pop('user', None)
    event.pop('extra', None)
    event.pop('breadcrumbs', None)
    if isinstance(event.get('message'), str):
        event['message'] = mask(event['message'])
    logentry = event.get('logentry')
    if isinstance(logentry, dict):
        for key in ('message', 'formatted'):
            logentry[key] = mask(logentry.get(key))
        logentry.pop('params', None)
    for exception in (event.get('exception') or {}).get('values', []):
        exception['value'] = mask(exception.get('value'))
        for frame in (exception.get('stacktrace') or {}).get('frames', []):
            frame.pop('vars', None)
    try:
        from django.db import connection
        schema = getattr(connection, 'schema_name', '') or ''
    except Exception:
        schema = ''
    if schema:
        event.setdefault('tags', {})['college'] = schema
    return event


def init_sentry(environ=None):
    """Start Sentry if SENTRY_DSN is set. Returns True when it was started."""
    environ = os.environ if environ is None else environ
    dsn = (environ.get('SENTRY_DSN') or '').strip()
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        environment=environ.get('SENTRY_ENVIRONMENT', 'production'),
        release=environ.get('GIT_COMMIT') or None,
        traces_sample_rate=float(environ.get('SENTRY_TRACES_SAMPLE_RATE', '0') or 0),
        send_default_pii=False,
        max_request_body_size='never',
        include_local_variables=False,
        before_send=scrub_event,
    )
    return True
