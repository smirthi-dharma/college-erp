from django.conf import settings


def get_client_ip(request):
    """The client address. Uses X-Forwarded-For-style headers only when AUDIT_CLIENT_IP_HEADER names
    one that a proxy you control sets; otherwise the connection address (headers can be spoofed)."""
    header = getattr(settings, 'AUDIT_CLIENT_IP_HEADER', None)
    value = request.META.get(header) if header else None
    if value:
        value = value.split(',')[0].strip()
    return value or request.META.get('REMOTE_ADDR') or None
