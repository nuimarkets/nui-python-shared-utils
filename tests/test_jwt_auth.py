"""Tests for JWT authentication module."""

import json
import time
import base64
import pytest
from typing import Any, Optional
from unittest.mock import patch, Mock

from cryptography.hazmat.primitives.asymmetric import rsa as crypto_rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

import rsa as rsa_lib

from nui_shared_utils.jwt_auth import (
    validate_jwt,
    require_auth,
    check_auth,
    get_jwt_public_key,
    JWTValidationError,
    AuthenticationError,
    _base64url_decode,
    _normalize_path,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rsa_keypair():
    """Generate an RSA key pair using cryptography (dev-only dependency).

    Returns (private_key, public_key_pem, rsa_public_key) where:
    - private_key: cryptography private key for signing test tokens
    - public_key_pem: PEM string for storage in mock secrets
    - rsa_public_key: rsa.PublicKey for direct use in validate_jwt()
    """
    private_key = crypto_rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    rsa_public_key = rsa_lib.PublicKey.load_pkcs1_openssl_pem(public_key_pem.encode("utf-8"))
    return private_key, public_key_pem, rsa_public_key


@pytest.fixture
def second_rsa_keypair():
    """A second, different RSA key pair for wrong-key tests."""
    private_key = crypto_rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key


@pytest.fixture
def mock_jwt_secret(rsa_keypair, mock_secrets_manager):
    """Configure mock Secrets Manager to return the test public key PEM."""
    _, public_key_pem, _ = rsa_keypair
    mock_secrets_manager.get_secret_value.return_value = {
        "SecretString": json.dumps({"TOKEN_PUBLIC_KEY": public_key_pem})
    }
    return mock_secrets_manager


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_jwt(private_key, claims: Any, header: Optional[dict] = None) -> str:
    """Create an RS256-signed JWT using the cryptography library."""
    if header is None:
        header = {"alg": "RS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header).encode())
    payload_b64 = _b64url_encode(json.dumps(claims).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signature_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


# ---------------------------------------------------------------------------
# validate_jwt tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateJwt:
    def test_valid_token(self, rsa_keypair):
        """RS256 sign + verify round-trip succeeds."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "user123", "exp": time.time() + 3600, "role": "admin"}
        token = _sign_jwt(private_key, claims)

        result = validate_jwt(token, public_key)

        assert result["sub"] == "user123"
        assert result["role"] == "admin"

    def test_expired_token(self, rsa_keypair):
        """Token with past exp claim is rejected."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "user123", "exp": time.time() - 60}
        token = _sign_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="expired"):
            validate_jwt(token, public_key)

    def test_invalid_signature(self, rsa_keypair, second_rsa_keypair):
        """Token signed with a different key is rejected."""
        wrong_key = second_rsa_keypair
        _, _, public_key = rsa_keypair
        claims = {"sub": "user123", "exp": time.time() + 3600}
        token = _sign_jwt(wrong_key, claims)

        with pytest.raises(JWTValidationError, match="signature verification failed"):
            validate_jwt(token, public_key)

    def test_wrong_algorithm(self, rsa_keypair):
        """Token with alg != RS256 is rejected before signature verification."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "user123"}
        token = _sign_jwt(private_key, claims, header={"alg": "HS256", "typ": "JWT"})

        with pytest.raises(JWTValidationError, match=r"Unsupported algorithm.*HS256"):
            validate_jwt(token, public_key)

    def test_malformed_token_missing_segments(self, rsa_keypair):
        """Token with wrong number of segments raises JWTValidationError."""
        _, _, public_key = rsa_keypair

        with pytest.raises(JWTValidationError, match="expected 3 segments"):
            validate_jwt("only.two", public_key)

        with pytest.raises(JWTValidationError, match="expected 3 segments"):
            validate_jwt("nosegments", public_key)

    def test_malformed_token_bad_base64(self, rsa_keypair):
        """Token with invalid base64 in header raises JWTValidationError."""
        _, _, public_key = rsa_keypair

        with pytest.raises(JWTValidationError, match="Invalid JWT header"):
            validate_jwt("!!!.payload.sig", public_key)

    def test_no_exp_claim_accepted(self, rsa_keypair):
        """Token without exp claim is accepted (exp is optional)."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "service-account", "scope": "internal"}
        token = _sign_jwt(private_key, claims)

        result = validate_jwt(token, public_key)
        assert result["sub"] == "service-account"

    def test_nbf_future_rejected(self, rsa_keypair):
        """Token with nbf far in the future is rejected."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "user123", "nbf": time.time() + 3600}
        token = _sign_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="not yet valid"):
            validate_jwt(token, public_key)

    def test_nbf_past_accepted(self, rsa_keypair):
        """Token with nbf in the past is accepted."""
        private_key, _, public_key = rsa_keypair
        claims = {"sub": "user123", "nbf": time.time() - 60}
        token = _sign_jwt(private_key, claims)

        result = validate_jwt(token, public_key)
        assert result["sub"] == "user123"

    def test_clock_skew_tolerance(self, rsa_keypair):
        """Token expired within clock skew window is still accepted."""
        private_key, _, public_key = rsa_keypair
        # Expired 15 seconds ago — within 30s tolerance
        claims = {"sub": "user123", "exp": time.time() - 15}
        token = _sign_jwt(private_key, claims)

        result = validate_jwt(token, public_key)
        assert result["sub"] == "user123"


def _assert_token_rejected(token: str, public_key, message: str) -> None:
    """Exercise the real validator through every public authentication entry point."""
    with pytest.raises(JWTValidationError, match=message):
        validate_jwt(token, public_key)

    event = {"path": "/private", "headers": {"Authorization": f"Bearer {token}"}}
    with patch("nui_shared_utils.jwt_auth.get_jwt_public_key", return_value=public_key):
        with pytest.raises(AuthenticationError, match=message):
            require_auth(event)
        claims, response = check_auth(event)

    assert claims is None
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["errors"][0]["detail"] == "Authentication required"


@pytest.mark.unit
class TestMalformedJwt:
    @pytest.mark.parametrize("header", [[], None, True, 42, "RS256"])
    def test_non_object_header_rejected_without_signature(self, rsa_keypair, header):
        _, _, public_key = rsa_keypair
        header_b64 = _b64url_encode(json.dumps(header).encode())
        _assert_token_rejected(f"{header_b64}.e30.AA", public_key, "header")

    @pytest.mark.parametrize("claims", [[], None, True, 42, "claims"])
    def test_signed_non_object_payload_rejected(self, rsa_keypair, claims):
        private_key, _, public_key = rsa_keypair
        token = _sign_jwt(private_key, claims)
        _assert_token_rejected(token, public_key, "payload")

    def test_signature_verified_before_payload_shape(self, rsa_keypair, second_rsa_keypair):
        _, _, public_key = rsa_keypair
        token = _sign_jwt(second_rsa_keypair, [])
        _assert_token_rejected(token, public_key, "signature verification failed")

    @pytest.mark.parametrize("name", ["exp", "nbf"])
    @pytest.mark.parametrize(
        "value",
        [None, False, True, "1000", [], {}, float("nan"), float("inf"), float("-inf")],
        ids=["null", "false", "true", "string", "array", "object", "nan", "infinity", "negative-infinity"],
    )
    def test_invalid_present_time_claim_rejected(self, rsa_keypair, name, value):
        private_key, _, public_key = rsa_keypair
        token = _sign_jwt(private_key, {"sub": "user123", name: value})
        _assert_token_rejected(token, public_key, name)

    def test_non_ascii_payload_rejected(self, rsa_keypair):
        _, _, public_key = rsa_keypair
        _assert_token_rejected("eyJhbGciOiJSUzI1NiJ9.é.AA", public_key, "payload")

    @pytest.mark.parametrize("segment", [0, 1, 2], ids=["header", "payload", "signature"])
    @pytest.mark.parametrize("suffix", ["!", "=", "\n", "+", "/", "é"])
    def test_non_base64url_segments_rejected(self, rsa_keypair, segment, suffix):
        private_key, _, public_key = rsa_keypair
        parts = _sign_jwt(private_key, {"sub": "user123"}).split(".")
        parts[segment] += suffix
        if segment != 2:
            signing_input = ".".join(parts[:2]).encode("utf-8")
            signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
            parts[2] = _b64url_encode(signature)
        _assert_token_rejected(".".join(parts), public_key, ["header", "payload", "signature"][segment])

    @pytest.mark.parametrize("segment", [0, 1, 2], ids=["header", "payload", "signature"])
    def test_empty_segments_rejected(self, rsa_keypair, segment):
        private_key, _, public_key = rsa_keypair
        parts = _sign_jwt(private_key, {"sub": "user123"}).split(".")
        parts[segment] = ""
        if segment != 2:
            signature = private_key.sign(".".join(parts[:2]).encode(), padding.PKCS1v15(), hashes.SHA256())
            parts[2] = _b64url_encode(signature)
        _assert_token_rejected(".".join(parts), public_key, ["header", "payload", "signature"][segment])

    @pytest.mark.parametrize(
        "claims",
        [
            {"sub": "user123"},
            {"exp": 970},
            {"exp": 970.0},
            {"nbf": 1030},
            {"nbf": 1030.0},
            {"exp": 10**400, "nbf": -(10**400)},
        ],
        ids=["absent", "exp-int-boundary", "exp-float-boundary", "nbf-int-boundary", "nbf-float-boundary", "large-int"],
    )
    def test_supported_time_claims_and_skew_boundaries(self, rsa_keypair, claims):
        private_key, _, public_key = rsa_keypair
        token = _sign_jwt(private_key, claims)
        event = {"path": "/private", "headers": {"Authorization": f"Bearer {token}"}}
        with patch("nui_shared_utils.jwt_auth.time.time", return_value=1000):
            assert validate_jwt(token, public_key) == claims
            with patch("nui_shared_utils.jwt_auth.get_jwt_public_key", return_value=public_key):
                assert require_auth(event) == claims
                assert check_auth(event) == (claims, None)

    @pytest.mark.parametrize("claims, message", [({"exp": 969.5}, "expired"), ({"nbf": 1030.5}, "not yet valid")])
    def test_outside_clock_skew_rejected(self, rsa_keypair, claims, message):
        private_key, _, public_key = rsa_keypair
        token = _sign_jwt(private_key, claims)
        with patch("nui_shared_utils.jwt_auth.time.time", return_value=1000):
            _assert_token_rejected(token, public_key, message)


# ---------------------------------------------------------------------------
# require_auth tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequireAuth:
    def test_valid_header(self, rsa_keypair, mock_jwt_secret):
        """Authorization: Bearer <token> is extracted and validated."""
        private_key, _, _ = rsa_keypair
        claims = {"sub": "user123", "exp": time.time() + 3600}
        token = _sign_jwt(private_key, claims)

        event = {"headers": {"Authorization": f"Bearer {token}"}}
        with patch.dict("os.environ", {"JWT_PUBLIC_KEY_SECRET": "test/jwt-key"}):
            result = require_auth(event)

        assert result["sub"] == "user123"

    def test_missing_header(self):
        """No Authorization header raises AuthenticationError."""
        event = {"headers": {}}
        with pytest.raises(AuthenticationError, match="Missing Authorization header"):
            require_auth(event)

    def test_missing_headers_key(self):
        """Event with no headers key raises AuthenticationError."""
        event = {}
        with pytest.raises(AuthenticationError, match="Missing Authorization header"):
            require_auth(event)

    def test_malformed_header_basic(self):
        """Authorization: Basic ... raises AuthenticationError."""
        event = {"headers": {"Authorization": "Basic dXNlcjpwYXNz"}}
        with pytest.raises(AuthenticationError, match="Bearer scheme"):
            require_auth(event)

    def test_empty_bearer(self):
        """Authorization: Bearer (no token) raises AuthenticationError."""
        event = {"headers": {"Authorization": "Bearer "}}
        with pytest.raises(AuthenticationError, match="Empty Bearer token"):
            require_auth(event)

    def test_case_insensitive_header(self, rsa_keypair, mock_jwt_secret):
        """Lowercase 'authorization' header works (API Gateway v2)."""
        private_key, _, _ = rsa_keypair
        claims = {"sub": "user456", "exp": time.time() + 3600}
        token = _sign_jwt(private_key, claims)

        event = {"headers": {"authorization": f"Bearer {token}"}}
        with patch.dict("os.environ", {"JWT_PUBLIC_KEY_SECRET": "test/jwt-key"}):
            result = require_auth(event)

        assert result["sub"] == "user456"


# ---------------------------------------------------------------------------
# get_jwt_public_key tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetJwtPublicKey:
    def test_caching_via_secrets_helper(self, mock_jwt_secret):
        """get_secret() is called once for multiple key fetches (secrets_helper cache)."""
        key1 = get_jwt_public_key(secret_name="test/jwt-key")
        key2 = get_jwt_public_key(secret_name="test/jwt-key")

        assert key1 == key2
        # get_secret_value should only be called once due to _secrets_cache
        mock_jwt_secret.get_secret_value.assert_called_once()

    def test_from_env_var(self, mock_jwt_secret):
        """JWT_PUBLIC_KEY_SECRET env var used as default secret name."""
        with patch.dict("os.environ", {"JWT_PUBLIC_KEY_SECRET": "env/jwt-key"}):
            key = get_jwt_public_key()

        assert isinstance(key, rsa_lib.PublicKey)
        mock_jwt_secret.get_secret_value.assert_called_once_with(SecretId="env/jwt-key")

    def test_no_secret_name_raises(self):
        """Missing secret name (no param, no env var) raises JWTValidationError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(JWTValidationError, match="No JWT public key secret name"):
                get_jwt_public_key()

    def test_missing_key_field_raises(self, mock_secrets_manager):
        """Secret exists but missing the expected key field raises JWTValidationError."""
        mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"OTHER_FIELD": "some_value"})}

        with pytest.raises(JWTValidationError, match="Field 'TOKEN_PUBLIC_KEY' not found"):
            get_jwt_public_key(secret_name="test/jwt-key")


# ---------------------------------------------------------------------------
# _normalize_path tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizePath:
    def test_simple_path(self):
        assert _normalize_path("/health") == "/health"

    def test_strips_trailing_slash(self):
        assert _normalize_path("/health/") == "/health"

    def test_ensures_leading_slash(self):
        assert _normalize_path("health") == "/health"

    def test_collapses_duplicate_slashes(self):
        assert _normalize_path("//health///check//") == "/health/check"

    def test_url_decodes(self):
        assert _normalize_path("/he%61lth") == "/health"

    def test_root_path(self):
        assert _normalize_path("/") == "/"

    def test_empty_string(self):
        assert _normalize_path("") == "/"

    def test_full_api_gateway_path(self):
        assert _normalize_path("/v4/fields/static-lists") == "/v4/fields/static-lists"


# ---------------------------------------------------------------------------
# check_auth tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckAuth:
    def test_public_path_skips_auth(self):
        """Public paths return (None, None) without checking JWT."""
        event = {"path": "/health", "headers": {}}
        claims, error = check_auth(event, public_paths={"/health"})
        assert claims is None
        assert error is None

    def test_public_path_with_trailing_slash(self):
        """Public path matching normalizes trailing slashes."""
        event = {"path": "/health/", "headers": {}}
        claims, error = check_auth(event, public_paths={"/health"})
        assert claims is None
        assert error is None

    def test_non_public_path_without_token_returns_401(self):
        """Missing auth on protected path returns 401 response."""
        event = {"path": "/static-lists", "headers": {}}
        claims, error = check_auth(event, public_paths={"/health"})
        assert claims is None
        assert error is not None
        assert error["statusCode"] == 401
        body = json.loads(error["body"])
        assert body["errors"][0]["status"] == "401"
        assert body["errors"][0]["detail"] == "Authentication required"

    @pytest.mark.usefixtures("mock_jwt_secret")
    def test_valid_auth_returns_claims(self, rsa_keypair):
        """Valid JWT returns (claims, None)."""
        private_key, _, _ = rsa_keypair
        token = _sign_jwt(private_key, {"sub": "user1", "exp": time.time() + 3600})
        event = {"path": "/static-lists", "headers": {"Authorization": f"Bearer {token}"}}

        with patch.dict("os.environ", {"JWT_PUBLIC_KEY_SECRET": "test/jwt-key"}):
            claims, error = check_auth(event, public_paths={"/health"})

        assert error is None
        assert claims["sub"] == "user1"

    @pytest.mark.usefixtures("mock_jwt_secret")
    def test_invalid_token_returns_401(self):
        """Invalid JWT returns 401 response, not an exception."""
        event = {"path": "/static-lists", "headers": {"Authorization": "Bearer bad.token.here"}}

        with patch.dict("os.environ", {"JWT_PUBLIC_KEY_SECRET": "test/jwt-key"}):
            claims, error = check_auth(event, public_paths={"/health"})

        assert claims is None
        assert error["statusCode"] == 401

    def test_path_traversal_does_not_bypass_auth(self):
        """Crafted paths like /anything/health don't bypass auth."""
        event = {"path": "/anything/health", "headers": {}}
        _, error = check_auth(event, public_paths={"/health"})
        assert error is not None
        assert error["statusCode"] == 401

    def test_uses_rawPath_fallback(self):
        """Falls back to rawPath when path is missing (API Gateway v2)."""
        event = {"rawPath": "/health", "headers": {}}
        claims, error = check_auth(event, public_paths={"/health"})
        assert claims is None
        assert error is None

    def test_empty_public_paths_requires_auth_on_all(self):
        """Empty public_paths means all paths require auth."""
        event = {"path": "/health", "headers": {}}
        _, error = check_auth(event)
        assert error is not None
        assert error["statusCode"] == 401
