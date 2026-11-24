# JWT Authentication Guide

RS256 JWT token validation for AWS Lambda functions behind API Gateway.

## Overview

The `jwt_auth` module validates RS256-signed JWTs using public keys stored in AWS Secrets Manager. It uses the pure-Python `rsa` package (~100KB) instead of PyJWT or `cryptography` (~35MB), keeping Lambda bundles small without needing a Lambda Layer.

## Installation

```bash
pip install nui-python-shared-utils[jwt]
```

## Quick Start

```python
from nui_shared_utils import require_auth, AuthenticationError

def lambda_handler(event, context):
    try:
        claims = require_auth(event)
    except AuthenticationError:
        return {"statusCode": 401, "body": "Unauthorized"}

    return {"statusCode": 200, "body": f"Hello {claims['sub']}"}
```

## API Reference

### `require_auth(event, secret_name=None) -> dict`

One-call authentication for API Gateway Lambda handlers. Extracts the Bearer token from the Authorization header, fetches the public key from Secrets Manager, and validates the token.

- **event** — API Gateway Lambda proxy event (v1 REST or v2 HTTP API)
- **secret_name** — Secrets Manager secret name (falls back to `JWT_PUBLIC_KEY_SECRET` env var)
- **Returns** — Decoded claims dict
- **Raises** — `AuthenticationError` on any failure (missing header, bad token, expired, bad signature, missing key), or when the token status check refuses the token (see [Token Status Check](#token-status-check-revocation))

### `validate_jwt(token, public_key) -> dict`

Lower-level validation when you already have the public key. Verifies compact-token encoding, JSON object structure, the RS256 signature, and optional `exp` and `nbf` time claims.

- **token** — Raw JWT string (no `Bearer` prefix)
- **public_key** — `rsa.PublicKey` instance
- **Returns** — Decoded claims dict
- **Raises** — `JWTValidationError` on failure

### `get_jwt_public_key(secret_name=None, key_field="TOKEN_PUBLIC_KEY") -> rsa.PublicKey`

Fetches and parses the PEM public key from Secrets Manager. Calls use the default
300-second `secrets_helper` retrieval cache. The first call at or after expiry
retrieves the current secret; a failed retrieval surfaces instead of serving a
stale key.

- **secret_name** — Falls back to `JWT_PUBLIC_KEY_SECRET` env var
- **key_field** — JSON field in the secret containing the PEM string
- **Returns** — `rsa.PublicKey`
- **Raises** — `JWTValidationError` if secret or field is missing/invalid

### Exceptions

- **`JWTValidationError`** — Base exception for token validation failures
- **`AuthenticationError(JWTValidationError)`** — Authentication-level failures (what `require_auth` raises)

## AWS Setup

### 1. Store the Public Key

```bash
aws secretsmanager create-secret \
  --name "prod/jwt-public-key" \
  --secret-string '{"TOKEN_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqh...\n-----END PUBLIC KEY-----"}'
```

The key must be PKCS#8 PEM format (`BEGIN PUBLIC KEY`, not `BEGIN RSA PUBLIC KEY`).

### 2. Lambda Environment

```yaml
# serverless.yml
environment:
  JWT_PUBLIC_KEY_SECRET: prod/jwt-public-key
```

### 3. IAM Permissions

```yaml
# serverless.yml
iamRoleStatements:
  - Effect: Allow
    Action: secretsmanager:GetSecretValue
    Resource: arn:aws:secretsmanager:*:*:secret:prod/jwt-public-key-*
```

## Usage Patterns

### Skip Auth for Health Endpoints

```python
def lambda_handler(event, context):
    path = event.get("path") or event.get("rawPath", "")
    if path == "/health":
        return {"statusCode": 200, "body": "ok"}

    try:
        claims = require_auth(event)
    except AuthenticationError:
        return {"statusCode": 401, "body": "Unauthorized"}

    # Authenticated route handling
    return handle_request(event, claims)
```

### Direct Validation (Pre-fetched Key)

```python
from nui_shared_utils import get_jwt_public_key, validate_jwt, JWTValidationError

# This retained key is not refreshed by later secrets_helper retrievals.
public_key = get_jwt_public_key(secret_name="prod/jwt-public-key")

def validate_token(token: str) -> dict:
    try:
        return validate_jwt(token, public_key)
    except JWTValidationError as e:
        raise ValueError(f"Bad token: {e}")
```

For bounded adoption of rotated keys, call `require_auth` or
`get_jwt_public_key` on each request. These paths retrieve through the default
300-second cache. The example above retains its key until the application
explicitly fetches and assigns another one. The TTL bounds cached retrievals;
it does not promise immediate revocation or mutate a key already held by a
caller. The cache is process-local and keyed by secret name, so clear it before
changing AWS account or region in the same process.

### Custom Key Field Name

```python
# If your secret uses a different field name
key = get_jwt_public_key(
    secret_name="prod/auth-keys",
    key_field="RSA_PUBLIC_KEY"
)
```

## What Gets Validated

| Check | Behavior |
|-------|----------|
| Token structure | Three nonempty, unpadded base64url segments using only ASCII letters, digits, `-` and `_` |
| JSON structure | Header and payload must each decode to a JSON object |
| Algorithm | Header `alg` must be `RS256` |
| Signature | RSA PKCS#1 v1.5 with SHA-256; payload claims are used only after signature verification |
| Time claim types | Present `exp` and `nbf` claims must be finite numbers; null, booleans, strings, arrays and objects are rejected |
| Expiration | Accept through `exp + 30` seconds; an absent `exp` claim is supported |
| Not before | Accept from `nbf - 30` seconds; an absent `nbf` claim is supported |

Malformed tokens raise `JWTValidationError` from `validate_jwt` and
`AuthenticationError` from `require_auth`. `check_auth` returns its standard 401
response for these failures. Public paths continue to bypass authentication.
Tokens with padded or other non-base64url segments, non-object JSON, or invalid
time-claim types must be corrected by their issuer before use.

## Token Status Check (revocation)

A resource server that validates JWTs on its own (signature and expiry) cannot see a revocation recorded at the
token issuer, so a revoked token keeps working until it expires. With `TOKEN_STATUS_URL` set, `require_auth`
asks the issuer whether the token is still active after `validate_jwt` succeeds, and caches the answer briefly.
`validate_jwt` stays a pure signature and expiry check, and `check_auth` gets the check through `require_auth`.

While the issuer answers, a revocation takes effect within 30 seconds plus one call. While it cannot answer,
`TOKEN_REVOCATION_FAIL` decides whether tokens are admitted.

### How a check runs

```text
require_auth(event)
  validate_jwt                                    signature and expiry, unchanged
  TOKEN_STATUS_URL unset or mode off  -> claims   no cache read, no call, no token fetch
  check(jti, exp)                                 t0 = when the check starts
    cached `revoked`                              -> revoked (hit)
    cached `active`, active_until > t0            -> active (hit)
    breaker open                                  -> unavailable (no call)
    this process's machine token                  inside the 2 s deadline
    POST {"jti": ...} to TOKEN_STATUS_URL         inside the same deadline, no redirects
      401  -> drop the machine token, fetch a new one, retry once
      200 {"active": true}   -> cache `active` until t0 + 30, only if no entry exists  -> active
      200 {"active": false}  -> cache `revoked`, overwriting                           -> revoked
      anything else          -> cache nothing; open the breaker if the issuer failed   -> unavailable
  mode enforce and (revoked, or unavailable with fail closed) -> AuthenticationError
```

| Result | Meaning | Cached |
| --- | --- | --- |
| `active` | 200 with `"active": true` | Until `t0 + 30` seconds, never past the token's `exp`. Written only when no entry exists, so a slow positive answer cannot replace a newer `revoked`. Not written once the token has expired. |
| `revoked` | 200 with `"active": false`, the only result that is a revocation | Overwrites any entry, for `max(60, exp + 60 - now)` seconds |
| `unavailable` | Anything else: a connection failure or timeout, a status other than 200 (redirects included, never followed), a body that is not a JSON object with a boolean `active`, a body over 1 KB, no machine token, a spent deadline, the breaker open, or a token with no string `jti` | Nothing. An outcome that says the issuer, or the path to it, failed opens the breaker for 10 seconds (below), so cache misses skip the call until it lapses. Cached answers keep being served meanwhile. |

The breaker opens only on an outcome that says the issuer, or the path to it, failed, so that the next miss would
most likely fail the same way:

| Opens the breaker for 10 seconds | Opens nothing |
| --- | --- |
| A connection failure, or a timeout on the status call (`connection`) | No machine token (`machine_token`), whatever the token endpoint answered |
| A 5xx, a 429, a redirect or any other status that is neither 200 nor a 4xx (`http_<status>`) | The deadline spent before the status call (`deadline`) |
| A 200 that is no answer (`malformed`) or is over 1 KB (`oversized`) | Any other 4xx (`http_<status>`), 403 included: a refusal of this token or this request |
| A 401 after the retry with a new machine token (`http_401`): the issuer refuses this service's client, so every check in the process would end the same way | A token with no string `jti` (`missing_jti`), or an error inside the check (`unexpected`) |

The status decides before the body cap: only a 200's body is read, so an oversized 502 is `http_502` and opens the
breaker, an oversized 404 is `http_404` and does not, and an oversized 401 is retried like any 401. The rule
depends on the reason alone, so a logged `reason` says whether the breaker opened.

The cache, the breaker and the machine token live in the process, so each warm Lambda container keeps its own.
The cache holds at most 10,000 answers and drops the least recently used first.

The 2 second deadline is wall clock from the start of the check and covers DNS, the machine token (including a
Secrets Manager read of its client), the status call and its body. Each TCP connection attempt is also capped at
1 second. The HTTP exchange is a minimal HTTP/1.1 request with `Connection: close` over a socket, TLS for
`https`, and the socket timeout is set to the time left before every receive, so a server that stalls or drips
bytes cannot hold a request past the deadline. DNS lookups and the Secrets Manager read run on a daemon thread
that the check stops waiting for at the deadline. The status body is read up to 1 KB and the token endpoint's
up to 16 KB, since an RS256 access token alone can pass 1 KB. The client connects to the URL's host directly
and does not read `HTTP_PROXY`, `HTTPS_PROXY` or `NO_PROXY`, so an egress path that needs a proxy reports every
call as `unavailable`.

The check never raises. A refusal in `enforce` is an `AuthenticationError` from `require_auth`, so `check_auth`
answers 401 as for any other invalid token.

### Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `TOKEN_STATUS_URL` | unset | The issuer's status endpoint. Unset, the check is off whatever the mode. |
| `TOKEN_REVOCATION_MODE` | `off` | `off`, `shadow` or `enforce`, case-insensitive. An unrecognised value is `off` and logs a warning. |
| `TOKEN_REVOCATION_FAIL` | `open` | `open` admits a token whose status is `unavailable`; `closed` refuses it. An unrecognised value is `open` and logs a warning. |
| `TOKEN_STATUS_TOKEN_URL` | unset | OAuth 2 token endpoint for this service's client-credentials grant |
| `TOKEN_STATUS_CLIENT_ID` | unset | OAuth client ID. Used with `TOKEN_STATUS_CLIENT_SECRET` when both are set. |
| `TOKEN_STATUS_CLIENT_SECRET` | unset | OAuth client secret |
| `TOKEN_STATUS_CREDENTIALS_SECRET` | unset | When the two above are not both set: a Secrets Manager secret, `{"client_id": "...", "client_secret": "..."}`, read through `get_secret` and cached for the life of the process |

| Mode | Cache read and call | Refuses |
| --- | --- | --- |
| `off` | Neither | Nothing |
| `shadow` | Both, writing the cache as `enforce` would | Nothing; logs `would reject` instead |
| `enforce` | Both | `revoked`, and `unavailable` when `TOKEN_REVOCATION_FAIL` is `closed` |

With `TOKEN_STATUS_URL` unset the check is off whatever `TOKEN_REVOCATION_FAIL` says, so `fail=closed` without a
URL refuses nothing; a mode of `shadow` or `enforce` without a URL logs a `not_configured` warning. The 30 second active TTL, 60 second revoked grace,
10 second breaker, 2 second deadline, 1 second connection timeout and 1% sample rate are fixed in this module.

The machine token is fetched on the first check that misses the cache, with a form-encoded
`grant_type=client_credentials` request, and reused until a day before it expires (from `expires_in`). A
refetch while the current token is still valid gets only half the time left, and if it fails the current one is
used and a warning is logged, so a stalled token endpoint still leaves time for the status call. A client
whose tokens live a day or less is therefore refetched on every check that misses the cache. A new client secret
in Secrets Manager reaches a warm process only after it restarts.

With a Secrets Manager secret, the Lambda role needs `secretsmanager:GetSecretValue` on it, as for the JWT
public key.

### The issuer's endpoint

The check expects an endpoint that:

- accepts `POST` with `Authorization: Bearer <machine token>` and the JSON body `{"jti": "<token id>"}`;
- answers `200` with `{"active": true}` or `{"active": false}`, and nothing else counts as an answer;
- does not evaluate expiry, since each validator owns expiry and its own leeway;
- answers a database or other infrastructure error with a 5xx, never `"active": false` or a 4xx, since a 5xx
  opens the breaker and a 4xx does not;
- answers 401 when it refuses the caller's machine token. The check then drops that token, fetches a new one
  and retries once within the deadline; a second 401 opens the breaker. A 403 is `unavailable` like any other
  status, with no new token: a new token from the same client cannot change a refusal of its scope, or a refusal
  from a gateway or firewall in front of the endpoint.

### Logging

Lines go to the `nui_shared_utils.token_status` logger, with their fields passed as `extra`. Every line carries
`feature: token_revocation` and an `action`.

| Message | Level | `action` | When | Fields |
| --- | --- | --- | --- | --- |
| `Token revocation: check enabled` | info | `startup` | First check in a process that misses the cache, then hourly, and again after the mode or fail setting changes. Not logged when off. | `mode`, `fail`, `url_host`, `sample_rate` |
| `Token revocation: would reject` | warning | `would_reject` | `shadow`, a check `enforce` would refuse | `jti`, `sub`, `user_id`, `aud`, `grant_type`, `validator` (`AWS_LAMBDA_FUNCTION_NAME`), `result`, `reason`, `mode`, `fail` |
| `Token revocation: rejected` | warning | `rejected` | `enforce`, a refused check | as `would reject` |
| `Token revocation: status unavailable` | warning | `unavailable` | An `unavailable` result other than the breaker, at most once a minute per process | `result`, `reason`, `detail` (for `machine_token`), `admitted`, `mode`, `fail` |
| `Token revocation: breaker open, status not checked` | warning | `breaker_open` | A miss skipped by the breaker, at most once a minute per process | `result`, `reason`, `admitted`, `mode`, `fail` |
| `Token revocation: check` | info | `check` | 1% of checks | `result`, `reason`, `cache.hit` (boolean), `duration_ms`, `mode`, `fail` |
| `Token revocation: machine token refresh failed, using the current one` | warning | `token_refresh_failed` | A failed refetch while the current token is valid, at most once a minute per process | `detail`, `token_expires_at` |
| `Token revocation: unrecognised setting, using the default` | warning | `invalid_setting` | Mode or fail setting not recognised, at most once a minute per process | `mode_setting`, `fail_setting` |
| `Token revocation: mode set without TOKEN_STATUS_URL, check off` | warning | `not_configured` | A mode other than `off` with no URL, at most once a minute per process | `mode_setting` |

`reason` on an `unavailable` result is one of `missing_jti`, `breaker_open`, `machine_token`, `deadline`,
`connection`, `oversized`, `http_<status>`, `malformed` or `unexpected`, plus `not_configured` from a direct
`check_token_status(jti, exp)` call with no URL set. `detail` on a `machine_token` result is
`not_configured`, `credentials_secret`, `connection`, `oversized`, `http_<status>`, `malformed` or `unexpected`.
No line carries the client secret, the machine token or the caller's bearer token.

Two settings decide whether these lines arrive with their fields:

- **Fields**: a formatter that drops `extra` (the Lambda runtime's plain-text format) keeps only the message. Use
  the Lambda JSON log format, or copy the handler's Powertools logger configuration onto library loggers with
  `aws_lambda_powertools.logging.utils.copy_config_to_registered_loggers(source_logger=logger)`.
- **Level**: the startup and sampled check lines are `INFO`. A process whose root logger stays at the Python
  default (`WARNING`) drops them, so set `nui_shared_utils.token_status` (or a parent) to `INFO`.

### Testing a service that uses it

Reset the per-process state between tests and stand in for the issuer by patching `send`, which takes
`(method, url, headers, body, deadline, max_body)` and returns an `HttpResponse(status, body)`:

```python
import json

import pytest

from nui_shared_utils import token_status
from nui_shared_utils.token_status import HttpResponse


@pytest.fixture(autouse=True)
def fresh_token_status():
    token_status.reset_token_status_state()
    yield
    token_status.reset_token_status_state()


def test_revoked_token_is_refused(monkeypatch, signed_event):
    monkeypatch.setenv("TOKEN_STATUS_URL", "https://issuer.example/token-status")
    monkeypatch.setenv("TOKEN_STATUS_TOKEN_URL", "https://issuer.example/oauth/token")
    monkeypatch.setenv("TOKEN_STATUS_CLIENT_ID", "client")
    monkeypatch.setenv("TOKEN_STATUS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TOKEN_REVOCATION_MODE", "enforce")
    calls = []

    def issuer(method, url, headers, body, deadline, max_body):
        calls.append(url)
        if url.endswith("/oauth/token"):
            return HttpResponse(200, json.dumps({"access_token": "machine", "expires_in": 3600}).encode())
        return HttpResponse(200, b'{"active": false}')

    monkeypatch.setattr(token_status, "send", issuer)

    response = handler(signed_event, None)

    assert response["statusCode"] == 401
    assert calls[-1] == "https://issuer.example/token-status"
```

## Dependency Details

The module uses `rsa` (pure Python, ~100KB) for signature verification at runtime. This avoids the ~35MB `cryptography` C extension that PyJWT requires for RS256. The `cryptography` package is only needed in dev for generating test key pairs and signing test tokens.
