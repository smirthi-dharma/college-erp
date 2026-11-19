"""The backup and restore scripts: what they promise is checked here, without a database or the network.

The real run (pg_dump, gpg, R2) is done by a person on staging (docs/BACKUPS.md); these tests keep the safety rules from
being edited away: encrypt before upload, never overwrite or delete, refuse the wrong database, stop on the first error.
"""
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / 'deploy' / 'backup' / 'backup.sh'
RESTORE = ROOT / 'deploy' / 'backup' / 'restore.sh'
MINIMAL_PATH = '/usr/bin:/bin'  # no pg_dump, psql, gpg or aws here: anything that needs them fails loudly


def run(script, env=None, args=()):
    base = {'PATH': MINIMAL_PATH, 'HOME': os.environ.get('HOME', '')}
    base.update(env or {})
    return subprocess.run(['bash', str(script), *args], env=base, capture_output=True, text=True, timeout=30)  # noqa: S603, S607


def code_lines(script):
    """The executable lines: no comments, and not the lines that only print a hint for the operator."""
    return [line for line in script.read_text().splitlines() if line.strip() and not line.lstrip().startswith(('#', 'log '))]


class FileRulesTests(SimpleTestCase):
    def test_scripts_exist_are_executable_and_stop_on_the_first_error(self):
        for script in (BACKUP, RESTORE):
            self.assertTrue(script.exists(), script.name)
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, f'{script.name} is not executable')
            text = script.read_text()
            self.assertTrue(text.startswith('#!/usr/bin/env bash'))
            self.assertIn('set -euo pipefail', text)

    def test_the_backup_is_encrypted_before_it_is_uploaded_and_the_readable_copy_is_removed(self):
        text = BACKUP.read_text()
        positions = {key: text.index(key) for key in ('pg_dump ', '--symmetric --cipher-algo AES256', 'rm -f "$work/db.dump"', 'aws s3 cp')}
        self.assertEqual(list(positions.values()), sorted(positions.values()), 'order must be dump, encrypt, remove plaintext, upload')
        self.assertLess(text.index('pg_dump '), text.index('tar -C "$MEDIA_ROOT"'), 'database first, then the files')
        media = text[text.index('tar -C "$MEDIA_ROOT"'):text.index('media_sha=')]
        self.assertRegex(media, r'(?s)tar -C "\$MEDIA_ROOT" -cf - \. \| gpg .*--symmetric --cipher-algo AES256', 'the archive is encrypted in the pipe')
        self.assertNotIn('> "$work/media.tar"', text)  # a readable archive must never be written to disk by the backup
        self.assertIn('--format=custom', text)
        self.assertIn('--passphrase-file', text)
        self.assertNotIn('--passphrase ', text)  # a passphrase on the command line shows in `ps`

    def test_the_upload_is_verified_and_a_failure_pings_the_dead_mans_switch(self):
        text = BACKUP.read_text()
        self.assertIn('head-object', text)
        self.assertIn('ping /fail', text)
        self.assertIn('sha256', text)

    def test_nothing_is_ever_deleted_or_overwritten_in_the_backup_store(self):
        for script in (BACKUP, RESTORE):
            code = '\n'.join(code_lines(script))
            self.assertNotRegex(code, r'aws s3 (rm|rb|mv|sync)\b', script.name)
            self.assertNotRegex(code, r'\b(drop database|dropdb|truncate)\b', script.name)
        self.assertNotIn('rm -rf "$dest', BACKUP.read_text())

    def test_no_secret_is_written_into_the_scripts(self):
        for script in (BACKUP, RESTORE):
            text = '\n'.join(code_lines(script))  # the usage comments show example URLs on purpose
            self.assertNotRegex(text, r'AKIA[0-9A-Z]{12,}', script.name)
            self.assertNotRegex(text, r'postgres(?:ql)?://[^\s:@/$"]+:[^\s@$"]+@', script.name)  # user:password@ literal
            self.assertNotRegex(text, r'(?i)(password|secret|token)=["\']?[A-Za-z0-9/+]{12,}', script.name)

    def test_the_transaction_pooler_is_refused_for_dumps(self):
        self.assertIn(':6543/', BACKUP.read_text())


class BackupRunTests(SimpleTestCase):
    def env(self, tmp, **extra):
        passphrase = Path(tmp) / 'pass'
        passphrase.write_text('not-a-real-passphrase')  # pragma: allowlist secret
        media = Path(tmp) / 'media'
        media.mkdir(exist_ok=True)
        base = {'DATABASE_URL': 'postgres://u@db.example:5432/erp', 'BACKUP_PASSPHRASE_FILE': str(passphrase),
                'BACKUP_DEST': 's3://bucket/erp', 'R2_ENDPOINT': 'https://acct.r2.cloudflarestorage.com', 'MEDIA_ROOT': str(media)}
        base.update(extra)
        return base

    def test_dry_run_lists_the_steps_in_order_and_needs_no_tools(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = run(BACKUP, self.env(tmp, DRY_RUN='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = [line.split('DRY RUN: ')[1] for line in result.stdout.splitlines() if 'DRY RUN: ' in line]
        self.assertTrue(steps[0].startswith('would pg_dump'))
        self.assertTrue(steps[1].startswith('would encrypt'))
        self.assertTrue(steps[2].startswith('would tar the uploaded files'), steps[2])
        self.assertIn('.media.tar.gpg', steps[2])
        self.assertTrue(steps[3].startswith('would upload the four files to s3://bucket/erp/daily/'), steps[3])
        self.assertTrue(steps[-1].startswith('would ping'))
        self.assertRegex(result.stdout, r'erp-\d{8}T\d{6}Z\.dump\.gpg')

    def test_it_refuses_to_start_without_its_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for missing in ('DATABASE_URL', 'BACKUP_PASSPHRASE_FILE', 'BACKUP_DEST', 'MEDIA_ROOT'):
                env = self.env(tmp)
                del env[missing]
                result = run(BACKUP, env)
                self.assertNotEqual(result.returncode, 0, missing)
                self.assertIn(missing, result.stderr, missing)

    def test_it_refuses_the_transaction_pooler_an_empty_passphrase_and_a_bad_destination(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pooler = run(BACKUP, self.env(tmp, DATABASE_URL='postgres://u@aws-0.pooler.supabase.com:6543/postgres'))
            self.assertEqual(pooler.returncode, 2)
            self.assertIn('transaction pooler', pooler.stderr)
            (Path(tmp) / 'empty').write_text('')
            empty = run(BACKUP, self.env(tmp, BACKUP_PASSPHRASE_FILE=str(Path(tmp) / 'empty')))
            self.assertEqual(empty.returncode, 2)
            self.assertIn('empty', empty.stderr)
            no_media = run(BACKUP, self.env(tmp, MEDIA_ROOT=str(Path(tmp) / 'does-not-exist')))
            self.assertEqual(no_media.returncode, 2)
            self.assertIn('not a directory', no_media.stderr)  # uploaded files must never be silently left out
            bad = run(BACKUP, self.env(tmp, BACKUP_DEST='relative/dir'))
            self.assertEqual(bad.returncode, 2)
            no_endpoint = self.env(tmp)
            del no_endpoint['R2_ENDPOINT']
            self.assertNotEqual(run(BACKUP, no_endpoint).returncode, 0)

    def test_without_the_database_tools_a_real_run_fails_and_leaves_nothing_behind(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'dest'
            result = run(BACKUP, self.env(tmp, BACKUP_DEST=str(dest)))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(dest.exists(), 'a failed run must not leave a partial backup')


class RestoreGuardTests(SimpleTestCase):
    def env(self, tmp, **extra):
        passphrase = Path(tmp) / 'pass'
        passphrase.write_text('not-a-real-passphrase')  # pragma: allowlist secret
        base = {'RESTORE_ADMIN_URL': 'postgres://u@localhost:5432/postgres', 'RESTORE_DATABASE': 'restore_drill_1',
                'BACKUP_PASSPHRASE_FILE': str(passphrase), 'BACKUP_DEST': str(Path(tmp) / 'dest'),
                'RESTORE_MEDIA_DIR': str(Path(tmp) / 'restore_media')}
        base.update(extra)
        return base

    def refused(self, tmp, why, **extra):
        result = run(RESTORE, self.env(tmp, **extra))
        self.assertEqual(result.returncode, 2, f'{why}: {result.stdout} {result.stderr}')  # exit 2 = our guard, not 127 "psql missing"
        return result.stderr

    def test_the_scratch_database_name_must_start_with_restore_(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('erp', 'college_erp_dev', 'postgres', 'restore_', 'restore_Bad', 'restore_a-b', 'restore_x;drop', 'prod_restore_1'):
                self.assertIn('restore_', self.refused(tmp, name, RESTORE_DATABASE=name), name)

    def test_it_will_not_restore_into_the_database_it_is_connected_to(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            error = self.refused(tmp, 'same db', RESTORE_ADMIN_URL='postgres://u@localhost:5432/restore_drill_1')
            self.assertIn('RESTORE_ADMIN_URL connects to', error)

    def test_it_will_not_run_on_the_production_host_or_on_supabase(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            error = self.refused(tmp, 'prod host', RESTORE_ADMIN_URL='postgres://u@db.prod.example:5432/postgres',
                                 PRODUCTION_DATABASE_URL='postgres://app:pw@db.prod.example:5432/erp')  # pragma: allowlist secret
            self.assertIn('production database host', error)
            for url in ('postgres://u@db.abcd.supabase.co:5432/postgres', 'postgres://u@aws-0-ap.pooler.supabase.com:5432/postgres'):
                self.assertIn('Supabase', self.refused(tmp, url, RESTORE_ADMIN_URL=url))

    def test_a_bad_destination_or_passphrase_file_is_refused_before_anything_is_fetched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.refused(tmp, 'relative dest', BACKUP_DEST='relative/dir')
            self.refused(tmp, 'no passphrase file', BACKUP_PASSPHRASE_FILE=str(Path(tmp) / 'missing'))

    def test_the_files_folder_must_be_new_named_restore_and_never_the_live_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ('relative/restore_x', str(Path(tmp) / 'media'), str(Path(tmp) / 'uploads'), '/', tmp):
                self.assertRegex(self.refused(tmp, bad, RESTORE_MEDIA_DIR=bad), r'RESTORE_MEDIA_DIR', bad)
            self.assertIn('live MEDIA_ROOT', self.refused(tmp, 'live', MEDIA_ROOT=str(Path(tmp) / 'restore_media')))
            full = Path(tmp) / 'restore_full'
            full.mkdir()
            (full / 'keep.txt').write_text('x')
            self.assertIn('not empty', self.refused(tmp, 'non-empty', RESTORE_MEDIA_DIR=str(full)))
            self.assertEqual((full / 'keep.txt').read_text(), 'x')  # nothing was touched

    def test_skip_media_is_the_only_way_to_leave_the_files_out(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(tmp)
            del env['RESTORE_MEDIA_DIR']
            result = run(RESTORE, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('RESTORE_MEDIA_DIR', result.stderr)
            env['SKIP_MEDIA'] = '1'
            env['RESTORE_DATABASE'] = 'bad name'  # passes the media rule, then stops at the next guard
            self.assertIn('restore_', run(RESTORE, env).stderr)

    def test_the_restore_checks_the_archive_and_every_file_the_database_names(self):
        text = RESTORE.read_text()
        self.assertIn("grep -Eq '^/|(^|/)\\.\\.(/|$)'", text)  # no absolute or .. paths
        self.assertLess(text.index('unsafe paths'), text.index('-c "create database'), 'inspect the archive before creating anything')
        self.assertIn('main_app_customuser', text)
        self.assertIn('main_app_consentrecord', text)
        self.assertIn('MISSING FILE', text)
        self.assertIn('exit 3', text)  # a database that names a file the backup lacks is an incident, not a pass

    @skipUnless(shutil.which('gpg'), 'gpg is not installed')
    def test_an_archive_with_paths_that_escape_is_refused_before_anything_is_created(self):
        """A real (hostile) encrypted archive: the script must stop at the path check, before psql is ever needed."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = self.env(tmp)
            gpg = shutil.which('gpg')
            stamp = 'erp-20261119T000000Z'
            daily = Path(env['BACKUP_DEST']) / 'daily'
            daily.mkdir(parents=True)
            (tmp / 'db.dump').write_bytes(b'stands in for the database dump; the archive check comes first')
            encrypt = [gpg, '--batch', '--yes', '--quiet', '--pinentry-mode', 'loopback', '--passphrase-file',
                       env['BACKUP_PASSPHRASE_FILE'], '--symmetric', '--cipher-algo', 'AES256']
            gpg_env = {**os.environ, 'GNUPGHOME': str(tmp)}
            subprocess.run([*encrypt, '--output', str(daily / f'{stamp}.dump.gpg'), str(tmp / 'db.dump')], check=True, env=gpg_env)  # noqa: S603
            data = (daily / f'{stamp}.dump.gpg').read_bytes()
            (daily / f'{stamp}.dump.gpg.sha256').write_text(f'{hashlib.sha256(data).hexdigest()}  {stamp}.dump.gpg\n')
            plain = io.BytesIO()
            with tarfile.open(fileobj=plain, mode='w') as archive:
                info = tarfile.TarInfo('../escape.txt')
                info.size = 1
                archive.addfile(info, io.BytesIO(b'x'))
            (tmp / 'evil.tar').write_bytes(plain.getvalue())
            subprocess.run([*encrypt, '--output', str(daily / f'{stamp}.media.tar.gpg'), str(tmp / 'evil.tar')], check=True, env=gpg_env)  # noqa: S603
            data = (daily / f'{stamp}.media.tar.gpg').read_bytes()
            (daily / f'{stamp}.media.tar.gpg.sha256').write_text(f'{hashlib.sha256(data).hexdigest()}  {stamp}.media.tar.gpg\n')
            env['PATH'] = f'{MINIMAL_PATH}:{Path(gpg).parent}'
            env['GNUPGHOME'] = str(tmp)
            result = run(RESTORE, env)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)  # 127 would mean it went on to psql
            self.assertIn('unsafe paths', result.stderr)
            self.assertFalse((tmp / 'escape.txt').exists())
            self.assertFalse(Path(env['RESTORE_MEDIA_DIR']).exists(), 'nothing may be created')

    def test_a_missing_setting_is_reported_by_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for missing in ('RESTORE_ADMIN_URL', 'RESTORE_DATABASE', 'BACKUP_PASSPHRASE_FILE', 'BACKUP_DEST'):
                env = self.env(tmp)
                del env[missing]
                result = run(RESTORE, env)
                self.assertNotEqual(result.returncode, 0, missing)
                self.assertIn(missing, result.stderr, missing)

    def test_the_guards_come_before_any_download_or_database_command(self):
        lines = RESTORE.read_text().splitlines()
        guards_end = next(i for i, line in enumerate(lines) if '# --- find and fetch' in line)
        for command in ('aws s3', 'gpg ', 'psql "', 'pg_restore', 'cp "$src'):
            first = next(i for i, line in enumerate(lines) if command in line and not line.lstrip().startswith('#'))
            self.assertGreater(first, guards_end, f'{command} must come after the guards')
        self.assertTrue(re.search(r'\^restore_\[a-z0-9_\]\+\$', RESTORE.read_text()))
