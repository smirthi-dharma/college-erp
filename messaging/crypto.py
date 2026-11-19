"""Encrypted per-college secrets (WhatsApp token now, each college's Razorpay keys later).

Keys come from the environment, never from the database, so a leaked database dump does not reveal the secrets:
  FIELD_ENCRYPTION_KEYS  comma-separated Fernet keys, NEWEST FIRST (encrypts with the first, decrypts with any)
  FIELD_ENCRYPTION_KEY   a single key (the original setting; used when the list is empty)
Rotation (see docs/KEY_MANAGEMENT.md): put the new key first, deploy, run `manage.py rotate_encryption_keys`, then retire the
old key. Values are shown only as their last four characters.
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# (app_label.ModelName, field) of every encrypted column, so rotation reaches all of them. Add new ones here (fees keys).
ENCRYPTED_FIELDS = [('messaging.CollegeSecret', 'ciphertext')]


def keys():
    """The configured keys, newest first. Raises ImproperlyConfigured (never echoing a key) if none or one is malformed."""
    listed = getattr(settings, 'FIELD_ENCRYPTION_KEYS', None) or []
    single = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    found = [k.strip() if isinstance(k, str) else k.decode() for k in (listed or ([single] if single else []))]
    if not found:
        raise ImproperlyConfigured('No encryption key is set (FIELD_ENCRYPTION_KEYS); college secrets cannot be stored or read.')
    if len(set(found)) != len(found):
        raise ImproperlyConfigured('FIELD_ENCRYPTION_KEYS lists the same key twice.')
    for position, key in enumerate(found, start=1):
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            raise ImproperlyConfigured('Encryption key number %d is not a valid Fernet key.' % position) from None
    return found


def _multi():
    return MultiFernet([Fernet(k.encode()) for k in keys()])


def encrypt(value):
    return _multi().encrypt(value.encode()).decode()


def decrypt(ciphertext):
    try:
        return _multi().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ImproperlyConfigured('A stored secret cannot be decrypted with any configured key.') from None


def uses_newest_key(ciphertext):
    """True if the newest key alone can decrypt this value (so the older keys are not needed for it)."""
    try:
        Fernet(keys()[0].encode()).decrypt(ciphertext.encode())
        return True
    except InvalidToken:
        return False


def rotate(ciphertext):
    """The same secret, re-encrypted with the newest key. Raises ImproperlyConfigured if no key can read it."""
    try:
        return _multi().rotate(ciphertext.encode()).decode()
    except InvalidToken:
        raise ImproperlyConfigured('A stored secret cannot be decrypted with any configured key.') from None
