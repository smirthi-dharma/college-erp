"""URLconf for the public schema (no college selected).

No college data is served from here. The WhatsApp webhook is reachable here too, so Meta's callback works on the bare
platform domain as well as on any college's address (it is signature-checked and routes by message id).
"""
from django.urls import path

from messaging.views import webhook

urlpatterns = [path('webhooks/whatsapp/', webhook, name='whatsapp_webhook')]
