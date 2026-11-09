"""Who is acting? The current HTTP request, if any, kept in a context variable."""
from contextvars import ContextVar

_current_request = ContextVar('audit_current_request', default=None)


def set_request(request):
    return _current_request.set(request)


def reset_request(token):
    _current_request.reset(token)


def current_request():
    return _current_request.get()
