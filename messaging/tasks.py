"""Background jobs. All logic lives in services.py; these only pick the college and call it.

Run with:  celery -A college_management_system worker -B -l info     (needs Redis: REDIS_URL)
Without Redis (a cron job works too):  manage.py run_digest
"""
import logging

from celery import shared_task
from django_tenants.utils import tenant_context

from colleges.models import College

from . import services

logger = logging.getLogger(__name__)


def _college(schema):
    return College.objects.get(schema_name=schema)


@shared_task(name='messaging.tasks.send_message_task')
def send_message_task(schema, message_id):
    with tenant_context(_college(schema)):
        action, delay = services.send_message(message_id)
    if action == 'retry':  # a transient failure: try again later (the message row says how many attempts were made)
        send_message_task.apply_async(args=[schema, message_id], countdown=delay)
    return action


@shared_task(name='messaging.tasks.dispatch_due_digests')
def dispatch_due_digests():
    """Every minute: for each college, start today's run if it is due, queue its messages, tidy up stuck ones."""
    queued = 0
    for college in College.objects.filter(is_active=True).exclude(schema_name='public'):
        try:
            with tenant_context(college):
                _, ids = services.start_run()
                ids = list(ids) + services.sweep()
            for message_id in ids:
                send_message_task.delay(college.schema_name, message_id)
                queued += 1
        except Exception:  # one college's problem must not stop the others
            logger.exception('Digest dispatch failed for %s', college.schema_name)
    return queued
