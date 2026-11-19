"""Sentry: off by default, and when on it sends no personal data."""
import json
from unittest import mock

from django.test import SimpleTestCase

from college_management_system import observability
from college_management_system.observability import init_sentry, mask, scrub_event


class SwitchTests(SimpleTestCase):
    def test_nothing_starts_without_a_dsn(self):
        with mock.patch('sentry_sdk.init') as init:
            self.assertFalse(init_sentry({}))
            self.assertFalse(init_sentry({'SENTRY_DSN': '   '}))
        init.assert_not_called()

    def test_with_a_dsn_it_starts_with_the_privacy_settings(self):
        with mock.patch('sentry_sdk.init') as init:
            self.assertTrue(init_sentry({'SENTRY_DSN': 'https://key@o1.ingest.example/1', 'SENTRY_ENVIRONMENT': 'staging',
                                         'GIT_COMMIT': 'abc123'}))
        kwargs = init.call_args.kwargs
        self.assertEqual(kwargs['send_default_pii'], False)
        self.assertEqual(kwargs['max_request_body_size'], 'never')
        self.assertEqual(kwargs['include_local_variables'], False)
        self.assertEqual(kwargs['traces_sample_rate'], 0.0)  # no performance tracing unless asked for
        self.assertEqual((kwargs['environment'], kwargs['release']), ('staging', 'abc123'))
        self.assertIs(kwargs['before_send'], scrub_event)

    def test_settings_start_it_from_the_environment_at_import(self):
        source = open(observability.__file__.replace('observability.py', 'settings.py')).read()
        self.assertIn('init_sentry()', source)


class ScrubTests(SimpleTestCase):
    def event(self):
        return {
            'message': 'Could not message +91 98765 43210 for parent@example.com',
            'request': {'url': 'https://demo.ourerp.in/students/5/', 'method': 'POST', 'data': {'password': 'hunter2'},  # pragma: allowlist secret
                        'cookies': {'sessionid': 'abc'}, 'headers': {'Authorization': 'Bearer x'}, 'query_string': 'q=Asha',
                        'env': {'REMOTE_ADDR': '1.2.3.4'}},
            'user': {'email': 'asha@example.com', 'ip_address': '1.2.3.4'},
            'extra': {'student': 'Asha'},
            'breadcrumbs': {'values': [{'message': 'asha@example.com'}]},
            'logentry': {'message': 'bad %s', 'formatted': 'bad asha@example.com', 'params': ['asha@example.com']},
            'exception': {'values': [{'type': 'ValueError', 'value': 'no guardian 9876543210 / mum@example.org',
                                      'stacktrace': {'frames': [{'function': 'f', 'vars': {'student': 'Asha'}}]}}]},
        }

    def test_no_personal_data_survives(self):
        cleaned = scrub_event(self.event())
        dumped = json.dumps(cleaned)
        for secret in ('parent@example.com', '98765 43210', '9876543210', 'asha@example.com', 'mum@example.org', 'hunter2',
                       'sessionid', 'Bearer', 'Asha', '1.2.3.4'):
            self.assertNotIn(secret, dumped, secret)

    def test_what_helps_debugging_is_kept(self):
        cleaned = scrub_event(self.event())
        self.assertEqual(cleaned['request']['url'], 'https://demo.ourerp.in/students/5/')
        self.assertEqual(cleaned['request']['method'], 'POST')
        self.assertEqual(cleaned['exception']['values'][0]['type'], 'ValueError')
        self.assertEqual(cleaned['exception']['values'][0]['stacktrace']['frames'][0]['function'], 'f')
        self.assertIn('[phone]', cleaned['message'])
        self.assertIn('[email]', cleaned['message'])

    def test_the_college_is_attached_as_a_tag(self):
        with mock.patch('django.db.connection.schema_name', 'demo', create=True):
            self.assertEqual(scrub_event({})['tags'], {'college': 'demo'})
        with mock.patch('django.db.connection.schema_name', '', create=True):
            self.assertNotIn('tags', scrub_event({}))

    def test_an_odd_event_never_breaks_the_scrubber_or_gets_dropped(self):
        for event in ({}, {'request': None}, {'exception': None}, {'exception': {'values': [{'value': None}]}}, {'message': 5}):
            self.assertIsNotNone(scrub_event(dict(event)))

    def test_mask_leaves_ordinary_numbers_alone(self):
        self.assertEqual(mask('class 10 had 148 students on 2026-11-19, rows 123456'), 'class 10 had 148 students on 2026-11-19, rows 123456')
        self.assertEqual(mask('x@y.in'), '[email]')
