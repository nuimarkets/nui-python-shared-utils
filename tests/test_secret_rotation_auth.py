"""Prove cache expiry adopts a rotated signing key through actual JWT auth."""

import base64
import json
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nui_shared_utils.jwt_auth import (
    AuthenticationError,
    check_auth,
    get_jwt_public_key,
    require_auth,
    validate_jwt,
)


def _key_and_token(subject: str):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    header = encode(b'{"alg":"RS256"}')
    payload = encode(json.dumps({"sub": subject}).encode())
    message = f"{header}.{payload}"
    signature = private_key.sign(message.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return public_pem, f"{message}.{encode(signature)}"


def _event(token: str) -> dict:
    return {"path": "/private", "headers": {"Authorization": f"Bearer {token}"}}


def _accept(token: str, subject: str) -> None:
    assert require_auth(_event(token), secret_name="rotation-test") == {"sub": subject}
    assert check_auth(_event(token), secret_name="rotation-test") == ({"sub": subject}, None)


def _reject(token: str) -> None:
    with pytest.raises(AuthenticationError, match="signature verification failed"):
        require_auth(_event(token), secret_name="rotation-test")
    claims, response = check_auth(_event(token), secret_name="rotation-test")
    assert claims is None
    assert response["statusCode"] == 401


@pytest.mark.unit
def test_rotated_key_adopted_at_default_cache_expiry(mock_secrets_manager):
    pem_a, token_a = _key_and_token("key-a")
    pem_b, token_b = _key_and_token("key-b")
    mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"TOKEN_PUBLIC_KEY": pem_a})}
    with patch("time.monotonic", return_value=0) as clock:
        original_key = get_jwt_public_key(secret_name="rotation-test")
        _accept(token_a, "key-a")
        mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"TOKEN_PUBLIC_KEY": pem_b})}
        clock.return_value = 299.999
        _accept(token_a, "key-a")
        _reject(token_b)
        assert mock_secrets_manager.get_secret_value.call_count == 1

        clock.return_value = 300
        _reject(token_a)
        _accept(token_b, "key-b")
        assert mock_secrets_manager.get_secret_value.call_count == 2

        # A previously fetched key is a retained reference, outside retrieval TTL.
        assert validate_jwt(token_a, original_key) == {"sub": "key-a"}


@pytest.mark.unit
def test_expired_key_retrieval_failure_surfaces_through_auth(mock_secrets_manager):
    public_pem, token = _key_and_token("key-a")
    mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"TOKEN_PUBLIC_KEY": public_pem})}
    with patch("time.monotonic", return_value=0) as clock:
        _accept(token, "key-a")
        clock.return_value = 300
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceErrorException"}}, "GetSecretValue"
        )
        with pytest.raises(Exception, match="Internal service error"):
            require_auth(_event(token), secret_name="rotation-test")
        with pytest.raises(Exception, match="Internal service error"):
            check_auth(_event(token), secret_name="rotation-test")
        assert mock_secrets_manager.get_secret_value.call_count == 3
