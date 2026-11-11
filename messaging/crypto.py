"""Encrypted per-college secrets (WhatsApp token now, each college's Razorpay keys later).

The key comes from the environment (settings.FIELD_ENCRYPTION_KEY, a Fernet key), never from the database, so a leaked
database dump does not reveal the secrets. Values are shown only as their last four characters.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured('FIELD_ENCRYPTION_KEY is not set; college secrets cannot be stored or read.')
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(ciphertext):
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ImproperlyConfigured('A stored secret cannot be decrypted with the current FIELD_ENCRYPTION_KEY.') from None
