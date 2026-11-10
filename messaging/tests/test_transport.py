"""The real transport maps WhatsApp's answers to the right error (requests is faked: no network)."""
from unittest import mock

import requests
from django.test import SimpleTestCase

from main_app import whatsapp

CREDS = whatsapp.Credentials('111', 'secret-token-ABCD')  # pragma: allowlist secret


def response(status, body=None):
    r = mock.Mock(status_code=status, text=str(body))
    r.json.return_value = body if body is not None else {}
    return r


class TransportMappingTests(SimpleTestCase):
    def call(self, **post):
        with mock.patch('main_app.whatsapp.requests.post', **post) as posted:
            try:
                return whatsapp._transport('+919000000001', 'absence_digest_en', 'en', ['a', 'b', 'c', 'd'], CREDS), posted
            except Exception as exc:
                return exc, posted

    def test_success_returns_the_message_id_and_posts_a_template(self):
        result, posted = self.call(return_value=response(200, {'messages': [{'id': 'wamid.XYZ'}]}))
        self.assertEqual(result, whatsapp.Sent('wamid.XYZ'))
        url, kwargs = posted.call_args[0][0], posted.call_args[1]
        self.assertTrue(url.endswith('/111/messages'))
        self.assertEqual(kwargs['json']['type'], 'template')
        self.assertEqual(kwargs['json']['to'], '919000000001')
        self.assertEqual(kwargs['json']['template']['name'], 'absence_digest_en')
        self.assertEqual(len(kwargs['json']['template']['components'][0]['parameters']), 4)
        self.assertEqual(kwargs['timeout'], whatsapp.TIMEOUT)

    def test_errors_map_to_what_the_caller_should_do(self):
        cases = [
            (response(401, {'error': {'message': 'bad token', 'code': 190}}), whatsapp.AuthError),
            (response(400, {'error': {'message': 'Invalid OAuth access token', 'code': 190}}), whatsapp.AuthError),
            (response(429, {'error': {'message': 'rate limit', 'code': 80007}}), whatsapp.TransientError),
            (response(503, {}), whatsapp.TransientError),
            (response(400, {'error': {'message': 'Re-engagement message', 'code': 131047}}), whatsapp.PermanentError),
        ]
        for reply, expected in cases:
            result, _ = self.call(return_value=reply)
            self.assertIsInstance(result, expected, reply.json.return_value)

    def test_network_failures(self):
        self.assertIsInstance(self.call(side_effect=requests.ConnectTimeout())[0], whatsapp.TransientError)  # nothing was sent
        self.assertIsInstance(self.call(side_effect=requests.exceptions.ConnectionError())[0], whatsapp.TransientError)
        self.assertIsInstance(self.call(side_effect=requests.ReadTimeout())[0], whatsapp.UnknownOutcome)  # may have been sent

    def test_the_token_never_appears_in_an_error_or_a_repr(self):
        result, _ = self.call(return_value=response(400, {'error': {'message': 'nope', 'code': 100}}))
        self.assertNotIn('ABCD', str(result))
        self.assertNotIn('ABCD', repr(CREDS)[:-5])

    def test_missing_platform_credentials_is_an_auth_error(self):
        with mock.patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': '', 'WHATSAPP_ACCESS_TOKEN': ''}):
            with self.assertRaises(whatsapp.AuthError):
                whatsapp._transport('+919000000001', 'x', 'en', [], None)
