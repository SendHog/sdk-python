# SendHog Python SDK

Send transactional email through [SendHog](https://sendhog.com) with a domain-scoped
API key. Native API plus SES-, Postmark-, Resend- and SendGrid-shaped migration
clients, all using SendHog authentication and the same transport.

Python 3.11+. MIT licensed. Published on
[PyPI as `sendhog-sdk`](https://pypi.org/project/sendhog-sdk/), starting with0.1.0.
Release artifacts and provenance: [GitHub Releases](https://github.com/SendHog/sdk-python/releases).

## Install

```sh
python -m pip install sendhog-sdk
```

No local SendHog checkout or Git installation is needed. All migration clients
below are included in this package; Python imports use `sendhog`.

## Send

```python
import os
from sendhog import SendHog

client = SendHog(os.environ['SENDHOG_API_KEY'])
message = client.send({
    'from': 'hello@your-verified-domain.com',
    'to': 'recipient@example.com',
    'subject': 'Welcome',
    'html': '<h1>Welcome</h1>',
    'text': 'Welcome',
})
print(message['id'])
```

Your domain must be verified and approved for sending. Account allowances,
recipient suppressions and other server-side controls still apply. Store API keys
in your server's secret manager, not in source code.

## Published templates

```python
client.send({
    'from': 'hello@your-verified-domain.com',
    'to': 'recipient@example.com',
    'template': {'id': 'welcome', 'version': 1, 'variables': {'name': 'Alex'}},
})
```

IDs/slugs belong to your sending domain; omit `version` for latest published.
Template and inline `html`/`text` are mutually exclusive. HTML variables are escaped;
missing variables fail before dispatch. No arbitrary JSX or JavaScript execution.

## Read retained mail

```python
page = client.messages.list({'limit': 20})
detail = client.messages.get(page['data'][0]['id'])
```

Requires `email.read`. This is a SendHog extension, not provider inbox/API emulation.
No automatic send retries; inspect failures before resubmitting an email.

## Migration modules

| Module             | Supported method                          |
| ------------------ | ----------------------------------------- |
| `sendhog.ses`      | `SESv2Client.send_email(**input)`         |
| `sendhog.postmark` | async `ServerClient.outbound.send(input)` |
| `sendhog.resend`   | `Resend.Emails.send(input)`               |
| `sendhog.sendgrid` | `SendGridAPIClient.send(input)`           |

All use **SendHog** API keys/endpoints, not upstream credentials or SigV4. Supported
subset: text/HTML, up to 50 total recipients, cc/bcc/reply-to (native `reply_to`);
native/Resend-style requests also support published SendHog templates. No raw MIME,
attachments, batch, scheduling, tracking or complete upstream SDK compatibility.
[Documentation](https://sendhog.com/docs/sdks/).

## Source and development

This is a reproducible release mirror of SendHog's canonical SDK source, not an
independently maintained API fork. `SOURCE.json` records the exact source revision,
contract hash and exported file digests. Public `contract/` fixtures are synthetic;
no service source, account data or credentials are exported. File issues here;
maintainers update the canonical generator/runtime and re-export the release.

```sh
python -m pip install . build twine
python scripts/verify-source.py
python -m unittest discover -s tests -p '*_test.py'
python -m build
python -m twine check dist/*
```

CI runs on pushes/PRs. The manual `publish.yml` workflow publishes a matching `vX.Y.Z`
tag with PyPI trusted publishing in the `pypi` GitHub environment. An authorized PyPI
account must first configure the pending/existing publisher for `sendhog-sdk`,
`SendHog/sdk-python`, workflow `publish.yml`, environment `pypi`. No token is stored here.
# Bulk sending and email events (0.2.0)

`client.send_bulk({'from':sender,'template':{'id':'welcome'},'recipients':[
{'to':recipient,'variables':{'name':'Alex'}}]},idempotency_key='stable-operation')`
sends one separate personalized email per recipient (up to50), waiting for SES
acceptance. Inspect ordered individual outcomes with `client.bulk.get(id)`.
Replays never resend/resume a batch. Single `client.send` accepts the same
`idempotency_key` keyword option.

`client.messages.events(id,{'limit':20})` reads30-day SES event history with
email.read. `client.webhooks.create({'url':url,'events':['email.delivered']})`,
`.list()` and `.delete(id)` require webhooks.manage. Creation returns webhook
metadata and a one-time secret; keep it securely. Verify exact raw bodies using
`client.webhooks.verify(body,headers,secret)` or `sendhog.verify_webhook(...)`.

Email acceptance is not recipient delivery. No customer email queue flag exists.
See https://sendhog.com/docs/templates/ and https://sendhog.com/docs/webhooks/.
