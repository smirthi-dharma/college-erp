"""GET /healthz: is the app up and can it reach its database? For Coolify / uptime monitors / the container health check.

Answered before the tenant lookup, the host check and the HTTPS redirect, so a monitor can call it by IP or `localhost`
over plain HTTP from inside the server. It needs no login and returns no data about colleges, users or versions:
only "ok" or "unavailable" and which check failed.
"""
import json

from django.db import connection
from django.http import HttpResponse

PATH = '/healthz'


def check_database():
    """True if the database answers a trivial query. Runs on the shared (public) schema, so it needs no college."""
    try:
        connection.set_schema_to_public()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            return cursor.fetchone() == (1,)
    except Exception:
        return False


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != PATH:
            return self.get_response(request)
        if request.method not in ('GET', 'HEAD'):
            return HttpResponse(status=405, headers={'Allow': 'GET, HEAD'})
        checks = {'database': check_database()}
        healthy = all(checks.values())
        body = json.dumps({'status': 'ok' if healthy else 'unavailable', 'checks': {k: 'ok' if v else 'failed' for k, v in checks.items()}})
        return HttpResponse(body if request.method == 'GET' else b'', content_type='application/json', status=200 if healthy else 503,
                            headers={'Cache-Control': 'no-store'})
