"""Guards on the deployment files (no Docker needed): the stack must keep Redis private, run exactly one scheduler, hold no
secret values, and let only the web container migrate. A real Coolify deploy is the final test of these files."""
import os
import re
import stat

import yaml
from django.conf import settings
from django.test import SimpleTestCase

ROOT = settings.BASE_DIR


def compose():
    with open(os.path.join(ROOT, 'docker-compose.yml'), encoding='utf-8') as handle:
        return yaml.safe_load(handle)


class ComposeTests(SimpleTestCase):
    def test_the_stack_has_web_worker_beat_backup_and_redis(self):
        self.assertEqual(set(compose()['services']), {'web', 'worker', 'beat', 'backup', 'redis'})

    def test_redis_is_private_password_protected_and_persistent(self):
        redis = compose()['services']['redis']
        self.assertNotIn('ports', redis)  # not published to the host or the internet
        self.assertIn('--requirepass', ' '.join(redis['command']))
        self.assertIn('--appendonly yes', ' '.join(redis['command']))
        self.assertTrue(any(v.endswith(':/data') for v in redis['volumes']))
        self.assertIn('healthcheck', redis)
        for name in ('web', 'worker', 'beat'):  # every container that uses Redis waits until it is healthy
            self.assertEqual(compose()['services'][name]['depends_on']['redis']['condition'], 'service_healthy')

    def test_only_the_web_service_is_exposed(self):
        services = compose()['services']
        self.assertEqual(services['web']['expose'], ['8000'])
        for name in ('worker', 'beat', 'backup', 'redis'):
            self.assertNotIn('ports', services[name])
            self.assertNotIn('expose', services[name])
        self.assertNotIn('ports', services['web'])  # Coolify's proxy reaches it; nothing is published on the host

    def test_exactly_one_scheduler_and_the_worker_runs_the_app_queue(self):
        services = compose()['services']
        beats = [n for n, s in services.items() if 'beat' in (s.get('command') or [])]
        self.assertEqual(beats, ['beat'])
        self.assertNotIn('replicas', (services['beat'].get('deploy') or {}))
        self.assertEqual(services['worker']['command'][:4], ['celery', '-A', 'college_management_system', 'worker'])
        self.assertNotIn('command', services['web'])  # web uses the image's gunicorn command
        self.assertIn('healthcheck', services['worker'])

    def test_app_containers_share_one_image_and_the_same_settings(self):
        services = compose()['services']
        for name in ('web', 'worker', 'beat'):
            self.assertEqual(services[name]['build'], '.')
        self.assertEqual(services['web']['environment'], services['worker']['environment'])
        self.assertEqual(services['web']['environment'], services['beat']['environment'])
        env = services['web']['environment']
        self.assertIn('@redis:6379', env['REDIS_URL'])
        for required in ('DJANGO_SECRET_KEY', 'DATABASE_URL', 'FIELD_ENCRYPTION_KEYS', 'REDIS_PASSWORD'):
            self.assertTrue(any(required in str(v) and ':?' in str(v) for v in env.values()), required)  # missing = refuses to start

    def test_no_secret_value_is_written_in_the_file(self):
        text = open(os.path.join(ROOT, 'docker-compose.yml'), encoding='utf-8').read()
        for key, value in compose()['services']['web']['environment'].items():
            self.assertRegex(str(value), r'\$\{|^redis://:\$\{', key)
        self.assertIsNone(re.search(r'(?i)(password|secret|token)\s*[:=]\s*[\'"]?[A-Za-z0-9+/]{12,}', text.replace('${', '')))

    def test_media_is_written_only_by_web_and_read_only_for_the_backup(self):
        services = compose()['services']
        self.assertTrue(any(v.endswith(':/app/media') for v in services['web']['volumes']))
        self.assertEqual(services['backup']['volumes'], ['media:/app/media:ro'])  # the backup can never change or delete an upload
        self.assertNotIn('volumes', services['worker'])
        self.assertNotIn('volumes', services['beat'])

    def test_web_has_a_health_check_on_the_health_endpoint(self):
        check = compose()['services']['web']['healthcheck']
        self.assertIn('/healthz', ' '.join(check['test']))
        self.assertIn('localhost:8000', ' '.join(check['test']))
        self.assertIn('start_period', check)  # the first start runs the migrations

    def test_the_release_name_reaches_every_app_container(self):
        env = compose()['services']['web']['environment']
        self.assertIn('SOURCE_COMMIT', env['GIT_COMMIT'])  # Coolify's commit id: Sentry release and PWA cache version
        self.assertIn('SENTRY_DSN', env)
        self.assertEqual(compose()['services']['worker']['environment']['GIT_COMMIT'], env['GIT_COMMIT'])


class BackupServiceTests(SimpleTestCase):
    def service(self):
        return compose()['services']['backup']

    def test_it_runs_the_nightly_script_and_cannot_start_without_its_settings(self):
        service = self.service()
        self.assertEqual(service['command'], ['/app/deploy/backup/run_nightly.sh'])
        for required in ('DATABASE_URL', 'BACKUP_PASSPHRASE', 'BACKUP_DEST', 'R2_ENDPOINT', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'):
            self.assertIn(':?', service['environment'][required], f'{required} must be required')  # staging cannot run with no backups

    def test_no_secret_is_written_in_the_file_and_it_is_not_exposed(self):
        service = self.service()
        for key, value in service['environment'].items():
            if key in ('AWS_DEFAULT_REGION', 'MEDIA_ROOT'):
                continue
            self.assertRegex(str(value), r'^\$\{', key)
        self.assertNotIn('ports', service)
        self.assertNotIn('expose', service)

    def test_the_image_has_the_tools_the_backup_needs(self):
        dockerfile = open(os.path.join(ROOT, 'Dockerfile'), encoding='utf-8').read()
        for package in ('postgresql-client', 'gnupg', 'curl', 'awscli'):
            self.assertRegex(dockerfile, rf'apt-get install[^\n]*\b{package}\b')
        self.assertIn('--no-install-recommends', dockerfile)
        self.assertIn('rm -rf /var/lib/apt/lists/*', dockerfile)  # keep the image small


class NightlyRunnerTests(SimpleTestCase):
    SCRIPT = os.path.join(ROOT, 'deploy', 'backup', 'run_nightly.sh')

    def run_script(self, *args, **env):
        import subprocess
        base = {'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin', 'HOME': os.environ.get('HOME', '')}
        base.update(env)
        return subprocess.run(['bash', self.SCRIPT, *args], env=base, capture_output=True, text=True, timeout=30)  # noqa: S603, S607

    def test_it_is_executable_and_stops_on_errors(self):
        self.assertTrue(os.stat(self.SCRIPT).st_mode & stat.S_IXUSR)
        self.assertIn('set -euo pipefail', open(self.SCRIPT, encoding='utf-8').read())

    def test_it_waits_until_the_next_0230_india_time(self):
        for now, seconds in (('2026-11-19T12:00:00Z', 30600), ('2026-11-19T20:29:00Z', 60), ('2026-11-19T20:30:00Z', 86400),
                             ('2026-11-19T21:00:00Z', 84600), ('2026-12-31T23:59:00Z', 73860)):
            result = self.run_script('--seconds-until', FAKE_NOW=now)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, str(seconds)), now)

    def test_the_run_time_can_be_changed(self):
        result = self.run_script('--seconds-until', FAKE_NOW='2026-11-19T12:00:00Z', BACKUP_RUN_AT_UTC='13:15')
        self.assertEqual(result.stdout.strip(), '4500')

    def test_it_refuses_to_start_without_the_passphrase(self):
        result = self.run_script('--once')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BACKUP_PASSPHRASE', result.stderr)

    def test_the_passphrase_goes_to_a_private_file_and_not_to_the_child_environment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, 'deploy', 'backup')
            os.makedirs(fake)
            import shutil
            shutil.copy(self.SCRIPT, os.path.join(fake, 'run_nightly.sh'))
            seen = os.path.join(tmp, 'seen.txt')
            with open(os.path.join(fake, 'backup.sh'), 'w', encoding='utf-8') as handle:  # a stand-in that records what it receives
                handle.write('#!/usr/bin/env bash\n{ echo "file=$BACKUP_PASSPHRASE_FILE"; echo "env=${BACKUP_PASSPHRASE:-unset}";'
                             ' echo "mode=$(stat -c %a "$BACKUP_PASSPHRASE_FILE" 2>/dev/null || stat -f %Lp "$BACKUP_PASSPHRASE_FILE")";'
                             ' echo "content=$(cat "$BACKUP_PASSPHRASE_FILE")"; } > "' + seen + '"\n')
            os.chmod(os.path.join(fake, 'backup.sh'), 0o700)
            os.chmod(os.path.join(fake, 'run_nightly.sh'), 0o700)
            import subprocess
            result = subprocess.run(['bash', os.path.join(fake, 'run_nightly.sh'), '--once'], capture_output=True, text=True, timeout=30,  # noqa: S603, S607
                                    env={'PATH': '/usr/bin:/bin', 'TMPDIR': tmp, 'BACKUP_PASSPHRASE': 'a-test-passphrase'})  # pragma: allowlist secret
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = dict(line.split('=', 1) for line in open(seen, encoding='utf-8').read().splitlines())
            self.assertEqual(lines['env'], 'unset')  # the child never sees the passphrase as a variable
            self.assertEqual(lines['mode'], '600')
            self.assertEqual(lines['content'], 'a-test-passphrase')
            self.assertFalse(os.path.exists(lines['file']), 'the file is removed when the script ends')


class ImageTests(SimpleTestCase):
    def test_only_the_gunicorn_container_migrates(self):
        script = open(os.path.join(ROOT, 'deploy', 'entrypoint.sh'), encoding='utf-8').read()
        self.assertRegex(script, r'if \[ "\$1" = "gunicorn" \]; then\s+python manage.py migrate_schemas --shared.*\s+python manage.py migrate_schemas --noinput[^\n]*\s+fi\s+exec "\$@"')
        self.assertTrue(os.stat(os.path.join(ROOT, 'deploy', 'entrypoint.sh')).st_mode & stat.S_IXUSR)

    def test_the_image_starts_gunicorn_through_the_entrypoint_as_a_non_root_user(self):
        dockerfile = open(os.path.join(ROOT, 'Dockerfile'), encoding='utf-8').read()
        self.assertIn('ENTRYPOINT ["/app/deploy/entrypoint.sh"]', dockerfile)
        self.assertIn('CMD ["gunicorn"', dockerfile)
        self.assertIn('USER app', dockerfile)


class CeleryWiringTests(SimpleTestCase):
    def test_the_scheduler_points_at_a_real_task_every_minute(self):
        from college_management_system.celery import app
        app.loader.import_default_modules()
        schedule = settings.CELERY_BEAT_SCHEDULE['dispatch-whatsapp-digests']
        self.assertIn(schedule['task'], app.tasks)
        self.assertEqual(schedule['schedule'], 60.0)
        self.assertIn('messaging.tasks.send_message_task', app.tasks)

    def test_the_broker_comes_from_redis_url(self):
        self.assertTrue(settings.CELERY_BROKER_URL.startswith('redis://'))
