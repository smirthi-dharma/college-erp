"""/healthz: answers for monitors on any host, needs no login, reports a broken database, leaks nothing."""
import json
from unittest import mock

from django.test import Client

from colleges.tests.base import HOST_A, HOST_B, TwoCollegeTestCase


class HealthzTests(TwoCollegeTestCase):
    def test_answers_200_ok_without_logging_in(self):
        response = Client(HTTP_HOST=HOST_A).get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'status': 'ok', 'checks': {'database': 'ok'}})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_works_on_any_host_including_ones_no_college_owns_and_a_bare_ip(self):
        for host in (HOST_B, 'localhost', '127.0.0.1', 'unknown.example.org', 'not-a-college.ourerp.in'):
            self.assertEqual(Client(HTTP_HOST=host).get('/healthz').status_code, 200, host)

    def test_works_over_plain_http_even_when_https_is_forced(self):
        with self.settings(SECURE_SSL_REDIRECT=True):
            response = Client(HTTP_HOST=HOST_A).get('/healthz')  # a container check calls http://localhost, not https
        self.assertEqual(response.status_code, 200)

    def test_a_database_failure_is_503_and_says_which_check_failed(self):
        with mock.patch('main_app.health.connection.cursor', side_effect=RuntimeError('db down')):
            response = Client(HTTP_HOST=HOST_A).get('/healthz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content), {'status': 'unavailable', 'checks': {'database': 'failed'}})
        self.assertNotIn(b'db down', response.content)  # the error text is never shown

    def test_head_works_and_other_methods_are_refused(self):
        client = Client(HTTP_HOST=HOST_A)
        head = client.head('/healthz')
        self.assertEqual((head.status_code, head.content), (200, b''))
        for method in ('post', 'put', 'delete'):
            self.assertEqual(getattr(client, method)('/healthz').status_code, 405, method)

    def test_it_reveals_nothing_about_colleges_or_versions(self):
        text = Client(HTTP_HOST=HOST_A).get('/healthz').content.decode().lower()
        for word in ('college', 'schema', 'django', 'version', 'postgres', 'user'):
            self.assertNotIn(word, text)

    def test_other_paths_are_unaffected(self):
        client = Client(HTTP_HOST=HOST_A)
        self.assertEqual(client.get('/healthz/').status_code, 404)  # only the exact path
        self.assertEqual(client.get('/').status_code, 200)

    def test_the_health_check_is_the_first_middleware(self):
        from django.conf import settings
        self.assertEqual(settings.MIDDLEWARE[0], 'main_app.health.HealthCheckMiddleware')
