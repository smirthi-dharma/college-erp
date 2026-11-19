from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from colleges.models import College
from messaging import crypto


class Command(BaseCommand):
    help = ('Re-encrypt every stored college secret with the NEWEST key (the first in FIELD_ENCRYPTION_KEYS). '
            'Run it after adding a new key at the front, before retiring the old key. Safe to repeat. '
            'Use --check to only report which secrets are on the newest key. Details: docs/KEY_MANAGEMENT.md')

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Change nothing; report and exit non-zero if any secret is not on the newest key or cannot be read.')
        parser.add_argument('--schema', help='One college only (default: all).')

    def handle(self, *args, **opts):
        try:
            crypto.keys()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None
        colleges = College.objects.exclude(schema_name='public')
        if opts['schema']:
            colleges = colleges.filter(schema_name=opts['schema'])
        rotated = already = unreadable = 0
        for college in colleges:
            with tenant_context(college):
                for label, field in crypto.ENCRYPTED_FIELDS:
                    for row in apps.get_model(label).objects.all():
                        value = getattr(row, field)
                        try:
                            if crypto.uses_newest_key(value):
                                already += 1
                            elif opts['check']:
                                self.stdout.write('%s: %s #%s still needs the older key.' % (college.schema_name, label, row.pk))
                                rotated += 1
                            else:
                                setattr(row, field, crypto.rotate(value))
                                row.save()  # save(), not update(): the audit log records that the ciphertext changed (never its value)
                                rotated += 1
                        except ImproperlyConfigured:
                            unreadable += 1
                            self.stderr.write('%s: %s #%s CANNOT be decrypted with any configured key.' % (college.schema_name, label, row.pk))
        verb = 'would need re-encrypting' if opts['check'] else 're-encrypted'
        self.stdout.write('%d secret(s) %s, %d already on the newest key, %d unreadable.' % (rotated, verb, already, unreadable))
        if unreadable or (opts['check'] and rotated):
            raise CommandError('Not every secret is on the newest key and readable.' if not unreadable else
                               'Some secrets cannot be read: do NOT remove any old key until this is resolved.')
