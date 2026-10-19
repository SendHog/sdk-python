"""Docs: docs/sending-events-bulk-plan.md. Verify Standard Webhooks before parsing JSON."""
import base64
import hashlib
import hmac
import json
import re
import time


def verify_webhook(raw_body, headers, secret):
    from .runtime import validate
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode('utf-8', errors='strict')
    values = {k.lower(): v for k, v in headers.items()}
    event_id = values.get('webhook-id', '')
    timestamp = values.get('webhook-timestamp', '')
    signatures = values.get('webhook-signature', '')
    if not isinstance(raw_body, str) or len(raw_body.encode('utf-8')) > 65536 or not re.fullmatch(r'evt_[a-f0-9]{64}', event_id) or not re.fullmatch(r'\d{1,12}', timestamp) or abs(time.time() - int(timestamp)) > 300 or len(signatures) > 2048:
        raise ValueError('Invalid webhook signature or timestamp')
    if not isinstance(secret, str) or not re.fullmatch(r'whsec_[A-Za-z0-9+/]{43}=', secret):
        raise ValueError('Invalid webhook signing secret')
    key = base64.b64decode(secret[6:], validate=True)
    expected = hmac.new(key, f'{event_id}.{timestamp}.{raw_body}'.encode('utf-8'), hashlib.sha256).digest()
    verified = False
    for candidate in signatures.split(' ')[:10]:
        if not re.fullmatch(r'v1,[A-Za-z0-9+/]{43}=', candidate):
            continue
        verified |= hmac.compare_digest(expected, base64.b64decode(candidate[3:], validate=True))
    if not verified:
        raise ValueError('Invalid webhook signature or timestamp')
    event = validate('EmailEvent', json.loads(raw_body))
    if event['id'] != event_id:
        raise ValueError('Webhook event ID mismatch')
    return event
