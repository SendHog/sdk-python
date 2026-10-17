"""Docs: docs/sdk-schema-plan.md. Same canonical fixtures as JS; no outward sends."""
import asyncio
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from sendhog.ses import SESv2Client
from sendhog.client import SendHog
from sendhog.postmark import ServerClient
from sendhog.resend import Resend
from sendhog.sendgrid import SendGridAPIClient
from sendhog.runtime import normalize, validate, ApiError, ProtocolError, ValidationError, BaseClient
from sendhog.contract_generated import MANIFEST
from sendhog import models

FIXTURES = json.loads((Path(__file__).parent.parent / 'contract' / 'cases.json').read_text())
CLASSES = {'sendhog':SendHog,'ses': SESv2Client, 'postmark': ServerClient, 'resend': Resend, 'sendgrid': SendGridAPIClient}

def call(provider, client, payload):
    if provider == 'sendhog': return client.send(payload)
    if provider == 'ses': return client.send_email(**payload)
    if provider == 'postmark': return asyncio.run(client.outbound.send(payload))
    if provider == 'resend': return client.Emails.send(payload)
    return client.send(payload)

class Conformance(unittest.TestCase):
    def test_published_templates_preserve_selector_and_variables(self):
        message={'from':'hello@example.com','to':'you@example.net','reply_to':'reply@example.com','template':{'id':'welcome','version':1,'variables':{'name':'Gabe','count':3}}}
        for provider in [p for p in MANIFEST['providers'] if p['id'] in ('sendhog','resend')]:
            result=normalize(provider,message)
            self.assertEqual(result['template'],message['template'])
            self.assertEqual(result['to'],['you@example.net'])
            self.assertEqual(result['replyTo'],['reply@example.com'])
            self.assertNotIn('subject',result)
            with self.assertRaises(ValidationError):normalize(provider,dict(message,html='<p>ambiguous</p>'))
    def test_simple_native_send_preserves_optional_fields(self):
        from sendhog import SendHog
        calls=[]
        def transport(method,url,headers,payload):
            calls.append(payload)
            return 200,{},FIXTURES['sendResponse']
        client=SendHog('fixture',transport=transport)
        message=dict(FIXTURES['providers']['sendhog']['python'],cc='cc@example.net',bcc=['bcc@example.net'],reply_to='reply@example.com',html='<p>Hello</p>')
        result=client.send(message)
        self.assertEqual(result['id'],'msg_fixture')
        self.assertEqual(calls[0]['replyTo'],['reply@example.com'])
        self.assertEqual(calls[0]['cc'],['cc@example.net'])
        self.assertEqual(calls[0]['html'],'<p>Hello</p>')
        self.assertNotIn('Content',calls[0])
        with self.assertRaises(ValidationError):client.send(dict(message,reply_to=['reply@example.com']*11))
        with self.assertRaises(ValidationError):client.send(FIXTURES['providers']['ses']['python'])
    def test_schema_is_valid_and_all_python_docs_execute(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).parent.parent / 'contract' / 'schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        examples = json.loads((Path(__file__).parent.parent / 'contract' / 'examples.json').read_text())
        for provider in MANIFEST['providers']:
            for operation in ('send', 'receive'):
                with self.subTest(provider=provider['id'], operation=operation):
                    result = FIXTURES['sendResponse'] if operation == 'send' else {'data': [], 'has_more': False, 'next_cursor': None}
                    with patch.dict('os.environ', {'SENDHOG_API_KEY': 'fixture_not_real'}), patch.object(BaseClient, '_http', staticmethod(lambda *args: (200, {}, result))), patch('builtins.print'):
                        exec(examples[provider['id']][operation]['python'], {'__name__': '__main__'})
    def test_generated_surface_and_translation(self):
        for provider in MANIFEST['providers']:
            with self.subTest(provider=provider['id']):
                calls = []
                def transport(method, url, headers, body):
                    calls.append(body)
                    self.assertEqual(method, 'POST')
                    self.assertEqual(url, 'https://app.sendhog.com/v1/send')
                    self.assertEqual(headers['Authorization'], 'Bearer sh_test')
                    self.assertEqual(body, FIXTURES['canonical'])
                    return 200, {'x-request-id': 'req_fixture'}, FIXTURES['sendResponse']
                client = CLASSES[provider['id']]('sh_test', transport=transport)
                result = call(provider['id'], client, FIXTURES['providers'][provider['id']]['python'])
                self.assertEqual(len(calls), 1)
                if provider['id'] == 'ses': self.assertEqual(result['MessageId'], 'msg_fixture')
                if provider['id'] == 'postmark': self.assertEqual(result.message_id, 'msg_fixture')
                if provider['id'] == 'resend': self.assertEqual(result['id'], 'msg_fixture')
                if provider['id'] == 'sendgrid': self.assertEqual(result.headers['x-message-id'], 'msg_fixture'); self.assertEqual(result.body, b'')

    def test_unsupported_fields_fail_before_transport(self):
        for provider in MANIFEST['providers']:
            client = CLASSES[provider['id']]('sh_test', transport=lambda *args: self.fail('HTTP must not run'))
            payload = dict(FIXTURES['providers'][provider['id']]['python'], unsupported=True)
            with self.assertRaises(ValidationError): call(provider['id'], client, payload)

    def test_count_and_charset_boundaries(self):
        provider = next(p for p in MANIFEST['providers'] if p['id']=='ses')
        payload = json.loads(json.dumps(FIXTURES['providers']['ses']['python']))
        payload['Destination']['CcAddresses'] = ['a@example.com'] * 50
        with self.assertRaises(ValidationError): normalize(provider, payload)
        payload['Destination'].pop('CcAddresses')
        payload['Content']['Simple']['Subject']['Charset'] = 'ASCII'
        with self.assertRaises(ValidationError): normalize(provider, payload)

    def test_paused_error_and_protocol_validation_no_retry(self):
        for provider in MANIFEST['providers']:
            calls = []
            def transport(*args):
                calls.append(args)
                return 503, {}, {'error': {'code': 'SendingPaused', 'message': 'No message was sent.', 'request_id': 'req'}}
            client = CLASSES[provider['id']]('sh_test', transport=transport)
            with self.assertRaises(ApiError) as error: call(provider['id'], client, FIXTURES['providers'][provider['id']]['python'])
            self.assertEqual(error.exception.code, 'SendingPaused')
            self.assertEqual(len(calls), 1)
            client = CLASSES[provider['id']]('sh_test', transport=lambda *args: (200, {}, {'unexpected': 'response'}))
            with self.assertRaises(ProtocolError): call(provider['id'], client, FIXTURES['providers'][provider['id']]['python'])

    def test_messages_and_query_validation(self):
        urls = []
        def transport(method, url, *args):
            urls.append(url)
            return 200, {}, {'data': [], 'has_more': False, 'next_cursor': None}
        client = SESv2Client('sh_test', transport=transport)
        self.assertEqual(client.messages.list(limit=2, direction='received')['data'], [])
        self.assertIn('limit=2', urls[0])
        with self.assertRaises(ValidationError): client.messages.list(unknown=True)
        with self.assertRaises(ValidationError): client.messages.list([])
        with self.assertRaises(ProtocolError): client.messages.get('a/b?c')
        self.assertTrue(urls[-1].endswith('a%2Fb%3Fc'))

    def test_examples_and_models(self):
        self.assertTrue(hasattr(models, 'SesSendRequest'))
        for provider in MANIFEST['providers']:
            validate(provider.get('pythonRequest', provider['request']), FIXTURES['providers'][provider['id']]['python'])

    def test_unsafe_origins(self):
        for origin in ('http://example.com', 'https://u:p@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError): SESv2Client('test', origin=origin)

if __name__ == '__main__': unittest.main()
