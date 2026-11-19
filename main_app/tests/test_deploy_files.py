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
    def test_the_stack_has_web_worker_beat_and_redis(self):
        self.assertEqual(set(compose()['services']), {'web', 'worker', 'beat', 'redis'})

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
        for name in ('worker', 'beat', 'redis'):
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

    def test_media_is_a_volume_only_on_web(self):
        services = compose()['services']
        self.assertTrue(any(v.endswith(':/app/media') for v in services['web']['volumes']))
        self.assertNotIn('volumes', services['worker'])


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
