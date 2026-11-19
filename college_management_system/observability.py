"""Error tracking with Sentry, switched on only when SENTRY_DSN is set, and configured not to send personal data.

A college ERP holds students' and parents' personal data (DPDP Act), so an error report must carry the error and where it
happened, not who it happened to. Names and roll numbers cannot be recognised by pattern, so the rule is to send only what we
control: the exception type, the stack of code locations, the request method and path (no query string), the release, and
the college's schema name as a tag. Everything that can carry a person's data is removed: request bodies, cookies, headers,
query strings, user, local variables, breadcrumbs, extra data, and the text of exception and log messages (a database error or
an f-string can contain a name, an e-mail or a roll number). The text is replaced by a fixed notice; to debug, use the type, the
stack and the path, and reproduce with demo data. Sentry's servers are in the US/EU (accepted 2026-11-19, see docs/BACKUPS.md).
A test (student360/tests/test_error_reporting.py) raises real errors from a student's page and a form post and checks that no
name, phone, e-mail, roll number, address, date of birth or guardian name is in what would be sent.
"""
import os

REQUEST_PARTS = ('data', 'cookies', 'headers', 'query_string', 'env')
WITHHELD = '[message withheld: it may contain personal data]'


def scrub_event(event, hint=None):
    """Sentry `before_send`: strip personal data from the event, tag the college, and return it (never drop it)."""
    request = event.get('request')
    if isinstance(request, dict):
        for part in REQUEST_PARTS:
            request.pop(part, None)
        if isinstance(request.get('url'), str):
            request['url'] = request['url'].split('?', 1)[0].split('#', 1)[0]  # a search box puts names in the query
    for key in ('user', 'extra', 'breadcrumbs', 'contexts_user', 'spans'):
        event.pop(key, None)
    if isinstance(event.get('_meta'), dict):
        event['_meta'].pop('request', None)  # Sentry's own note of which header names it removed
    if event.get('message') is not None:
        event['message'] = WITHHELD
    logentry = event.get('logentry')
    if isinstance(logentry, dict):
        logentry['message'] = logentry['formatted'] = WITHHELD
        logentry.pop('params', None)
    for exception in (event.get('exception') or {}).get('values', []):
        if exception.get('value') is not None:
            exception['value'] = WITHHELD
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


def init_sentry(environ=None, transport=None):
    """Start Sentry if SENTRY_DSN is set. Returns True when it was started. `transport` is only for tests."""
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
        **({'transport': transport} if transport is not None else {}),
    )
    return True
