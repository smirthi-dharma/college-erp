"""Celery app: background jobs (WhatsApp digests). Started with `celery -A college_management_system worker -B`."""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'college_management_system.settings')

app = Celery('college_management_system')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
