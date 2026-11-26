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
- **Raises** — `AuthenticationError` on any failure (missing header, bad token, expired, bad signature, missing key)

### `validate_jwt(token, public_key) -> dict`

Lower-level validation when you already have the public key. Verifies token structure, RS256 signature, and expiration.

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
| Token structure | Must be 3 dot-separated base64url segments |
| Algorithm | Header `alg` must be `RS256` |
| Signature | RSA PKCS#1 v1.5 with SHA-256 |
| Expiration | `exp` claim checked against current time (optional — tokens without `exp` are accepted) |

## Dependency Details

The module uses `rsa` (pure Python, ~100KB) for signature verification at runtime. This avoids the ~35MB `cryptography` C extension that PyJWT requires for RS256. The `cryptography` package is only needed in dev for generating test key pairs and signing test tokens.
