"""The app as an installable web app (PWA): manifest, service worker and offline page.

What the service worker may keep is deliberately tiny, because phones in a college are shared and the data is personal
(DPDP Act): only the offline page and the public static files (CSS, scripts, icons). It never keeps an HTML page of the
app, a picture, a form post or any JSON, so nothing about a student or a college can be left on a phone or shown to the next
person, and signing out needs no clean-up. Going offline shows the offline page; the app itself needs a connection
(offline attendance marking is not built).

The three URLs are public on purpose: a browser fetches the manifest and the worker without a session.
"""
import json

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET

from .access import public_view

THEME_COLOR = '#5e64ff'  # --primary-color in dist/css/erpnext-style.css
ICONS = (('pwa/icon-192.png', '192x192', 'any'), ('pwa/icon-512.png', '512x512', 'any'),
         ('pwa/icon-maskable-512.png', '512x512', 'maskable'))
SHORT_NAME_LENGTH = 12  # Android shows about this many characters under the icon


def college_name():
    return (getattr(connection.tenant, 'name', '') or '').strip() or 'College ERP'


@public_view
@require_GET
def manifest(request):
    name = college_name()
    data = {
        'id': '/',
        'name': name,
        'short_name': name[:SHORT_NAME_LENGTH].strip() or 'College ERP',
        'description': 'Attendance, students and parent alerts for %s.' % name,
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#ffffff',
        'theme_color': THEME_COLOR,
        'lang': 'en',
        'categories': ['education', 'productivity'],
        'icons': [{'src': static(path), 'sizes': sizes, 'type': 'image/png', 'purpose': purpose} for path, sizes, purpose in ICONS],
    }
    response = JsonResponse(data, content_type='application/manifest+json')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@public_view
@require_GET
def service_worker(request):
    config = {'version': settings.PWA_CACHE_VERSION, 'offlineUrl': reverse('pwa_offline'),
              'precache': [static(path) for path, _sizes, _purpose in ICONS[:2]]}
    response = render(request, 'pwa/sw.js', {'config_json': json.dumps(config)}, content_type='text/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'  # the browser must re-check it every time, or an update would never arrive
    return response


@public_view
@require_GET
def offline(request):
    response = render(request, 'pwa/offline.html', {'college': college_name()})
    response['Cache-Control'] = 'no-cache'
    return response

