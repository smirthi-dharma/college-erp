from . import context


class AuditContextMiddleware:
    """Expose the request to the audit signals. Must come after AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = context.set_request(request)
        try:
            return self.get_response(request)
        finally:
            context.reset_request(token)
