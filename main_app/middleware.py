from django.conf import settings
from django.http import HttpResponseForbidden


def build_csp(directives):
    return '; '.join('%s %s' % (name, ' '.join(values)) for name, values in directives.items())


class SecurityHeadersMiddleware:
    """Adds Content-Security-Policy (from settings.CSP_DIRECTIVES) and Permissions-Policy to every response."""

    PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        header = 'Content-Security-Policy-Report-Only' if settings.CSP_REPORT_ONLY else 'Content-Security-Policy'
        response.headers.setdefault(header, build_csp(settings.CSP_DIRECTIVES))
        response.headers.setdefault('Permissions-Policy', self.PERMISSIONS_POLICY)
        return response


class LoginCheckMiddleWare:
    """Deny by default: a view must declare ``allowed_roles`` (see access.py) or be marked public.

    The decorators do the actual login/role checks; this middleware guarantees no
    endpoint can be added without choosing a policy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        module = getattr(view_func, '__module__', '') or ''
        if module.startswith(('django.contrib.admin', 'django.contrib.auth.views', 'django.views.static')):
            return None  # Django's own views carry their own checks (is_staff, login_required, ...)
        if getattr(view_func, 'is_public', False) or getattr(view_func, 'allowed_roles', None):
            return None
        return HttpResponseForbidden('Access policy missing for this page.')
