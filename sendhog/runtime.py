"""Docs: docs/sdk-schema-plan.md. Shared validation/translation, no provider auth or automatic retries."""
import json
import urllib.request
import urllib.error
import urllib.parse
from email.utils import getaddresses
from types import SimpleNamespace
from jsonschema import Draft202012Validator, FormatChecker
from .contract_generated import DEFINITIONS, MANIFEST

class ValidationError(ValueError):
    pass

class ProtocolError(RuntimeError):
    pass

class ApiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(body['error']['message'])
        self.status_code = status
        self.code = body['error']['code']
        self.request_id = body['error']['request_id']

_validators = {name: Draft202012Validator({'$ref': '#/$defs/' + name, '$defs': DEFINITIONS}, format_checker=FormatChecker()) for name in DEFINITIONS}

def validate(model, value):
    errors = list(_validators[model].iter_errors(value))
    if errors:
        paths = sorted(set('/' + '/'.join(str(p) for p in e.absolute_path) for e in errors))
        raise ValidationError('Invalid ' + model + ': ' + ', '.join(paths)) from None
    return value

def _list(value):
    return value if isinstance(value, list) else [value]

def _mailbox(value):
    if isinstance(value, str):
        return value
    return (json.dumps(value['name'], ensure_ascii=False) + ' <' + value['email'] + '>') if value.get('name') else value['email']

def _csv(value):
    if '\r' in value or '\n' in value:
        raise ValidationError('Address lists cannot contain line breaks')
    if not value.strip():
        return []
    return [_mailbox({'name': name, 'email': email}) for name, email in getaddresses([value])]

def check_recipients(payload):
    if not 1 <= sum(len(_list(payload[field])) for field in ('to','cc','bcc') if field in payload) <= MANIFEST['limits']['recipients']:
        raise ValidationError('Total recipients must be between 1 and 50')

def normalize(provider, payload):
    validate(provider.get('pythonRequest', provider['request']), payload)
    if provider['format']=='native':
        result=dict(payload)
        if 'reply_to' in result: result['replyTo']=result.pop('reply_to')
        for field in ('to','cc','bcc','replyTo'):
            if field in result: result[field]=_list(result[field])
        validate('SendRequest',result)
        check_recipients(result)
        return result
    if provider['format'] == 'ses':
        result = payload
    elif provider['format'] == 'resend' and 'template' in payload:
        result = dict(payload)
        if 'reply_to' in result: result['replyTo'] = result.pop('reply_to')
        for field in ('to','cc','bcc','replyTo'):
            if field in result: result[field] = _list(result[field])
        validate('SendRequest', result)
        check_recipients(result)
        return result
    elif provider['format'] == 'sendgrid':
        body = {}
        for part in payload['content']:
            key = 'Text' if part['type'] == 'text/plain' else 'Html'
            if key in body:
                raise ValidationError('Duplicate SendGrid content type')
            body[key] = {'Data': part['value']}
        destination = {}
        for field, target in [('to', 'ToAddresses'), ('cc', 'CcAddresses'), ('bcc', 'BccAddresses')]:
            if field in payload['personalizations'][0]:
                destination[target] = [_mailbox(v) for v in payload['personalizations'][0][field]]
        result = {'FromEmailAddress': _mailbox(payload['from']), 'Destination': destination, 'Content': {'Simple': {'Subject': {'Data': payload['subject']}, 'Body': body}}}
        if 'reply_to' in payload:
            result['ReplyToAddresses'] = [_mailbox(payload['reply_to'])]
    else:
        fields = provider.get('pythonFields', provider['fields'])
        def addresses(value):
            return _csv(value) if provider['format'] == 'postmark' else _list(value)
        body = {}
        for field, target in [('text', 'Text'), ('html', 'Html')]:
            if fields[field] in payload:
                body[target] = {'Data': payload[fields[field]]}
        destination = {}
        for field, target in [('to', 'ToAddresses'), ('cc', 'CcAddresses'), ('bcc', 'BccAddresses')]:
            if fields[field] in payload:
                values = addresses(payload[fields[field]])
                if values:
                    destination[target] = values
        result = {'FromEmailAddress': payload[fields['from']], 'Destination': destination, 'Content': {'Simple': {'Subject': {'Data': payload.get(fields['subject'], '')}, 'Body': body}}}
        if fields['replyTo'] in payload:
            result['ReplyToAddresses'] = addresses(payload[fields['replyTo']])
    validate('SesSendRequest', result)
    flat={'from':result['FromEmailAddress'],'subject':result['Content']['Simple']['Subject']['Data']}
    for field,target in [('ToAddresses','to'),('CcAddresses','cc'),('BccAddresses','bcc')]:
        if result['Destination'].get(field): flat[target]=result['Destination'][field]
    for field,target in [('Text','text'),('Html','html')]:
        if field in result['Content']['Simple']['Body']: flat[target]=result['Content']['Simple']['Body'][field]['Data']
    if result.get('ReplyToAddresses'): flat['replyTo']=result['ReplyToAddresses']
    validate('SendRequest',flat)
    check_recipients(flat)
    return flat

def translate(provider, payload, response, metadata):
    validate('SendResponse', response)
    if provider['format']=='native': return response
    kind = provider['format']
    if kind == 'ses':
        # boto3 uses ResponseMetadata rather than the JS $metadata spelling.
        result = {'MessageId': response['id'], 'ResponseMetadata': {'HTTPStatusCode': metadata['status'], 'RequestId': metadata.get('request_id') or '', 'RetryAttempts': 0}}
        return validate(provider['pythonResponse'], result)
    if kind == 'postmark':
        result = {'to': payload['to'], 'submitted_at': response['created_at'], 'message_id': response['id'], 'error_code': 0, 'message': 'OK'}
        validate(provider['pythonResponse'], result)
        return SimpleNamespace(**result)
    if kind == 'resend':
        result = {'id': response['id']}
        return validate(provider['response'], result)
    result = {'statusCode': 202, 'headers': {'x-message-id': response['id']}, 'body': ''}
    validate(provider['response'], result)
    return SimpleNamespace(status_code=202, headers=result['headers'], body=b'')

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class _Messages:
    def __init__(self, client): self._client = client
    def list(self, params=None, **kwargs):
        if params is not None and kwargs: raise ValidationError('Pass a query dictionary or keyword arguments, not both')
        return self._client._request('listMessages', params=params if params is not None else kwargs)[0]
    def get(self, message_id): return self._client._request('getMessage', params={'id': message_id})[0]

class BaseClient:
    def __init__(self, api_key, *, origin=None, transport=None):
        if not isinstance(api_key, str) or not api_key: raise ValueError('A SendHog API key is required')
        url = urllib.parse.urlsplit(origin or MANIFEST['baseUrl'])
        if not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Use an HTTPS origin, or HTTP loopback for local development')
        self._origin = urllib.parse.urlunsplit((url.scheme, url.netloc, '', '', ''))
        self._api_key = api_key
        self._transport = transport or self._http
        self.messages = _Messages(self)

    @staticmethod
    def _http(method, url, headers, payload):
        request = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers, method=method)
        try:
            response = urllib.request.build_opener(_NoRedirect).open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(2097153)
            if len(raw) > 2097152: raise ProtocolError('Response exceeds the SDK size limit')
            try: data = json.loads(raw)
            except (ValueError, UnicodeError): raise ProtocolError('Response is not valid public API JSON') from None
            return response.status, dict(response.headers), data

    def _request(self, operation, payload=None, params=None):
        definition = next(m for m in MANIFEST['methods'] if m['name'] == operation)
        params = {} if params is None else params
        path = definition['http']['path']
        if '{id}' in path:
            message_id = params.get('id')
            if not isinstance(message_id, str) or not message_id or len(message_id) > 256: raise ValidationError('Invalid message ID')
            path = path.replace('{id}', urllib.parse.quote(message_id, safe=''))
        if 'query' in definition:
            validate(definition['query'], params)
            if params: path += '?' + urllib.parse.urlencode(params)
        if 'request' in definition:
            validate(definition['request'], payload)
            if definition['request'] == 'SendRequest': check_recipients(payload)
        status, headers, data = self._transport(definition['http']['method'], self._origin + path, {'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'}, payload)
        if not 200 <= status < 300:
            try: validate('ApiError', data)
            except ValidationError: raise ProtocolError('Error response does not match the public schema') from None
            raise ApiError(status, data)
        try: validate(definition['response'], data)
        except ValidationError: raise ProtocolError('Success response does not match the public schema') from None
        normalized_headers = {k.lower(): v for k, v in headers.items()}
        return data, {'status': status, 'request_id': normalized_headers.get('x-request-id'), 'headers': headers}

    def _send_variant(self, provider, payload):
        request = normalize(provider, payload)
        result, metadata = self._request('send', request)
        return translate(provider, payload, result, metadata)
