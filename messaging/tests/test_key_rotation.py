"""Encryption key rotation with MultiFernet (docs/KEY_MANAGEMENT.md)."""
from io import StringIO

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import tenant_context

from colleges.tests.base import TwoCollegeTestCase
from messaging import crypto

K1, K2, K3 = (Fernet.generate_key().decode() for _ in range(3))
SECRET_A, SECRET_B = 'EAAG-college-a-token-1111', 'EAAG-college-b-token-2222'  # pragma: allowlist secret


def run(*args, **options):
    out, err = StringIO(), StringIO()
    try:
        call_command(*args, stdout=out, stderr=err, **options)
    finally:
        run.out, run.err = out.getvalue(), err.getvalue()
    return run.out


class CryptoTests(SimpleTestCase):
    @override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY='')
    def test_encrypts_with_the_newest_key_and_reads_with_any(self):
        new = crypto.encrypt('hello')
        self.assertTrue(crypto.uses_newest_key(new))
        old = Fernet(K1.encode()).encrypt(b'hello').decode()  # written before the new key existed
        self.assertFalse(crypto.uses_newest_key(old))
        self.assertEqual((crypto.decrypt(new), crypto.decrypt(old)), ('hello', 'hello'))
        rotated = crypto.rotate(old)
        self.assertTrue(crypto.uses_newest_key(rotated))
        self.assertEqual(crypto.decrypt(rotated), 'hello')
        self.assertNotEqual(rotated, old)

    @override_settings(FIELD_ENCRYPTION_KEYS=[K2], FIELD_ENCRYPTION_KEY='')
    def test_after_retiring_the_old_key_old_values_cannot_be_read(self):
        old = Fernet(K1.encode()).encrypt(b'hello').decode()
        with self.assertRaises(ImproperlyConfigured):
            crypto.decrypt(old)
        with self.assertRaises(ImproperlyConfigured):
            crypto.rotate(old)

    @override_settings(FIELD_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_KEY=K1)
    def test_the_single_key_setting_still_works(self):
        self.assertEqual(crypto.decrypt(crypto.encrypt('x')), 'x')

    def test_bad_configuration_is_refused_without_echoing_a_key(self):
        for keys in ([], ['not-a-fernet-key'], [K1, K1], [K1, 'short']):
            with override_settings(FIELD_ENCRYPTION_KEYS=keys, FIELD_ENCRYPTION_KEY=''):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    crypto.encrypt('x')
                self.assertNotIn(K1, str(caught.exception))
                self.assertNotIn('not-a-fernet-key', str(caught.exception))

    def test_the_new_key_command_prints_a_usable_key(self):
        key = run('new_encryption_key').strip()
        Fernet(key.encode())
        self.assertNotEqual(key, run('new_encryption_key').strip())


class RotationCommandTests(TwoCollegeTestCase):
    def put(self, college, value):
        from messaging.models import CollegeSecret
        with tenant_context(college):
            return CollegeSecret.put('whatsapp_access_token', value)

    def stored(self, college):
        from messaging.models import CollegeSecret
        with tenant_context(college):
            return CollegeSecret.objects.get(name='whatsapp_access_token').ciphertext

    def setUp(self):
        super().setUp()
        with override_settings(FIELD_ENCRYPTION_KEYS=[K1], FIELD_ENCRYPTION_KEY=''):  # written under the old key
            self.put(self.college_a, SECRET_A)
            self.put(self.college_b, SECRET_B)

    def test_rotation_moves_every_college_to_the_newest_key_and_keeps_the_values(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY=''):
            before = (self.stored(self.college_a), self.stored(self.college_b))
            self.assertTrue(not crypto.uses_newest_key(before[0]) and not crypto.uses_newest_key(before[1]))
            out = run('rotate_encryption_keys')
            self.assertIn('2 secret(s) re-encrypted, 0 already on the newest key, 0 unreadable.', out)
            after = (self.stored(self.college_a), self.stored(self.college_b))
            self.assertTrue(all(crypto.uses_newest_key(c) for c in after))
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2], FIELD_ENCRYPTION_KEY=''):  # the old key is retired
            self.assertEqual((crypto.decrypt(after[0]), crypto.decrypt(after[1])), (SECRET_A, SECRET_B))
        for text in (run.out, run.err):
            self.assertNotIn(SECRET_A[-4:], text)

    def test_running_it_again_changes_nothing(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY=''):
            run('rotate_encryption_keys')
            first = (self.stored(self.college_a), self.stored(self.college_b))
            out = run('rotate_encryption_keys')
            self.assertIn('0 secret(s) re-encrypted, 2 already on the newest key', out)
            self.assertEqual((self.stored(self.college_a), self.stored(self.college_b)), first)

    def test_check_changes_nothing_and_fails_until_everything_is_rotated(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY=''):
            before = self.stored(self.college_a)
            with self.assertRaises(CommandError):
                run('rotate_encryption_keys', check=True)
            self.assertIn('2 secret(s) would need re-encrypting', run.out)
            self.assertEqual(self.stored(self.college_a), before)
            run('rotate_encryption_keys')
            self.assertIn('0 secret(s) would need re-encrypting, 2 already', run('rotate_encryption_keys', check=True))

    def test_one_college_only(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY=''):
            run('rotate_encryption_keys', schema='college_a')
            self.assertTrue(crypto.uses_newest_key(self.stored(self.college_a)))
            self.assertFalse(crypto.uses_newest_key(self.stored(self.college_b)))

    def test_an_unreadable_secret_stops_the_retirement_and_nothing_else_is_lost(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[K3, K2], FIELD_ENCRYPTION_KEY=''):  # K1 forgotten: both are unreadable
            before = (self.stored(self.college_a), self.stored(self.college_b))
            with self.assertRaises(CommandError) as caught:
                run('rotate_encryption_keys')
            self.assertIn('do NOT remove any old key', str(caught.exception))
            self.assertIn('CANNOT be decrypted', run.err)
            self.assertEqual((self.stored(self.college_a), self.stored(self.college_b)), before)

    def test_rotation_is_audited_without_values(self):
        from audit_trail.models import AuditLog
        with override_settings(FIELD_ENCRYPTION_KEYS=[K2, K1], FIELD_ENCRYPTION_KEY=''):
            old = self.stored(self.college_a)
            run('rotate_encryption_keys', schema='college_a')
            new = self.stored(self.college_a)
            with tenant_context(self.college_a):
                entry = AuditLog.objects.filter(model_label='messaging.CollegeSecret', action='update').latest('id')
        self.assertEqual(entry.actor_email, '')  # the system, not a person
        self.assertEqual(set(entry.changes), {'ciphertext'})
        text = str(entry.changes)
        for secret in (old, new, SECRET_A):
            self.assertNotIn(secret, text)

    def test_without_any_key_the_command_refuses(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_KEY=''):
            with self.assertRaises(CommandError):
                run('rotate_encryption_keys')
