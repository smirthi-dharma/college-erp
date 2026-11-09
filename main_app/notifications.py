"""Push notifications (Firebase Cloud Messaging).

The inherited code shipped the upstream project's FCM server key in the source. The key now
comes from the environment, and nothing is sent when it is not configured.
"""
import logging

import requests
from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse

logger = logging.getLogger(__name__)


def send_push(token, message, click_action_name):
    """Best-effort push. Returns True if FCM accepted the message."""
    if not (settings.FCM_SERVER_KEY and token):
        return False
    body = {
        'notification': {
            'title': 'College ERP',
            'body': message,
            'click_action': reverse(click_action_name),
            'icon': static('dist/img/AdminLTELogo.png'),
        },
        'to': token,
    }
    headers = {'Authorization': 'key=' + settings.FCM_SERVER_KEY, 'Content-Type': 'application/json'}
    try:
        response = requests.post(settings.FCM_URL, json=body, headers=headers, timeout=10)
        return response.ok
    except requests.RequestException:
        logger.exception('FCM push failed')
        return False
