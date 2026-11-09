from django.http import HttpResponseForbidden


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
