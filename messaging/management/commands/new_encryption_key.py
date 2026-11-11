from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Print a new random encryption key (a Fernet key). Store it in the password manager first; see docs/KEY_MANAGEMENT.md.'

    def handle(self, *args, **opts):
        self.stdout.write(Fernet.generate_key().decode())
