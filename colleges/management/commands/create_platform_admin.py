"""Create (or re-arm) a platform admin inside one college: a superuser with an authenticator device.

    python manage.py create_platform_admin <schema> <email> [--reset-2fa]

The e-mail must be listed in PLATFORM_ADMIN_EMAILS. The password is asked interactively (never
passed on the command line). The authenticator secret is shown once as an otpauth:// link; scan it
with an authenticator app (paste it into a QR generator you trust, or use the app's manual entry).
"""
import getpass

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_tenants.utils import get_tenant_model, tenant_context


class Command(BaseCommand):
    help = 'Create a platform admin (superuser + TOTP device) in a college schema.'

    def add_arguments(self, parser):
        parser.add_argument('schema')
        parser.add_argument('email')
        parser.add_argument('--reset-2fa', action='store_true', help='Replace the authenticator device (lost phone).')

    def handle(self, *args, schema, email, reset_2fa, **options):
        from main_app.models import CustomUser
        email = email.strip().lower()
        if email not in settings.PLATFORM_ADMIN_EMAILS:
            raise CommandError('That e-mail is not listed in PLATFORM_ADMIN_EMAILS.')
        try:
            college = get_tenant_model().objects.get(schema_name=schema)
        except get_tenant_model().DoesNotExist as exc:
            raise CommandError(f'No college with schema {schema!r}.') from exc
        with tenant_context(college):
            user = CustomUser.objects.filter(email=email).first()
            if user is None:
                password = getpass.getpass('Password: ')
                if password != getpass.getpass('Password (again): '):
                    raise CommandError('Passwords do not match.')
                try:
                    validate_password(password)
                except ValidationError as exc:
                    raise CommandError(' '.join(exc.messages)) from exc
                user = CustomUser.objects.create_superuser(email, password)
            elif not (user.is_staff and user.is_superuser):
                user.is_staff = user.is_superuser = True
                user.save()
            if reset_2fa:
                TOTPDevice.objects.filter(user=user).delete()
            device = TOTPDevice.objects.filter(user=user).first()
            if device is not None:
                self.stdout.write('This account already has an authenticator device (use --reset-2fa to replace it).')
                return
            device = TOTPDevice.objects.create(user=user, name='platform', confirmed=True)
            self.stdout.write('Add this to your authenticator app (shown once):')
            self.stdout.write(device.config_url)
