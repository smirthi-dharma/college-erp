from django.conf import settings

from .utils import js_json


def firebase(request):
    """Firebase web config for the push-notification scripts (from FIREBASE_WEB_CONFIG, {} when unset)."""
    return {'firebase_config': js_json(settings.FIREBASE_WEB_CONFIG)}
