"""
AWS Secrets Manager helper for retrieving credentials.
Shared across all AWS Lambda functions.
"""

import os
import json
import logging
from urllib.parse import urlparse
from typing import Any, Dict, Optional

from .config import get_config

log = logging.getLogger(__name__)

# Cache for secrets to avoid repeated API calls
_secrets_cache = {}


def get_secret(secret_name: str) -> Dict:
    """
    Retrieve secret from AWS Secrets Manager.

    Args:
        secret_name: Name of the secret in Secrets Manager

    Returns:
        Dict containing the secret values

    Raises:
        Exception if secret cannot be retrieved
    """
    # Check cache first
    if secret_name in _secrets_cache:
        return _secrets_cache[secret_name]

    import boto3
    from botocore.exceptions import ClientError

    # Create a Secrets Manager client
    session = boto3.session.Session()
    region = session.region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise Exception("AWS region not configured for Secrets Manager lookup; set AWS_REGION or AWS_DEFAULT_REGION")

    client = session.client(service_name="secretsmanager", region_name=region)

    try:
        response = client.get_secret_value(SecretId=secret_name)

        # Secrets Manager stores either a string or binary
        if "SecretString" in response:
            secret = json.loads(response["SecretString"])
        else:
            # Binary secret (not typically used for credentials)
            secret = json.loads(response["SecretBinary"].decode("utf-8"))

        # Cache the secret
        _secrets_cache[secret_name] = secret

        log.info(f"Successfully retrieved secret: {secret_name}")
        return secret

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "DecryptionFailureException":
            log.error(f"Cannot decrypt secret {secret_name}: {e}")
            raise Exception(f"Cannot decrypt secret {secret_name}")
        elif error_code == "InternalServiceErrorException":
            log.error(f"Internal service error retrieving {secret_name}: {e}")
            raise Exception(f"Internal service error retrieving {secret_name}")
        elif error_code == "InvalidParameterException":
            log.error(f"Invalid parameter for {secret_name}: {e}")
            raise Exception(f"Invalid parameter for {secret_name}")
        elif error_code == "InvalidRequestException":
            log.error(f"Invalid request for {secret_name}: {e}")
            raise Exception(f"Invalid request for {secret_name}")
        elif error_code == "ResourceNotFoundException":
            log.error(f"Secret {secret_name} not found: {e}")
            raise Exception(f"Secret {secret_name} not found")
        else:
            log.error(f"Unknown error retrieving {secret_name}: {e}")
            raise Exception(f"Error retrieving secret {secret_name}: {error_code}")
    except Exception as e:
        log.error(f"Unexpected error retrieving {secret_name}: {e}")
        raise Exception(f"Unexpected error retrieving secret {secret_name}: {str(e)}")


# Per-engine defaults for database credentials. The env tier fills all three;
# the Secrets Manager tier fills port and database only, so a secret missing its
# username stays visibly broken rather than borrowing a guessed one.
_DB_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "mysql": {"port": 3306, "username": "root", "database": "app"},
    "postgresql": {"port": 5432, "username": "postgres", "database": "postgres"},
}


def _db_credentials_from_env(engine: str = "mysql") -> Optional[Dict[str, Any]]:
    """
    Resolve database credentials from DB_* environment variables.

    Both DB_HOST and DB_PASSWORD must be set; otherwise returns None so the
    caller falls through to Secrets Manager. Shared by the database clients and
    get_database_credentials.

    Args:
        engine: Key into the per-engine defaults ("mysql" or "postgresql")

    Raises:
        ValueError: DB_PORT is set but is not an integer
    """
    host = os.environ.get("DB_HOST")
    password = os.environ.get("DB_PASSWORD")
    if not host or not password:
        return None
    defaults = _DB_DEFAULTS[engine]
    port_str = os.environ.get("DB_PORT", str(defaults["port"]))
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"DB_PORT must be an integer, got: {port_str!r}")
    return {
        "host": host,
        "port": port,
        "username": os.environ.get("DB_USERNAME", defaults["username"]),
        "password": password,
        "database": os.environ.get("DB_DATABASE", defaults["database"]),
    }


def _normalise_db_secret(creds: Dict[str, Any], engine: str = "mysql") -> Dict[str, Any]:
    """
    Map a database secret onto host, port, username, password, database.

    Accepts the common alternative field names (endpoint/hostname, user, dbname)
    and fills a missing port or database from the engine's defaults.
    """
    defaults = _DB_DEFAULTS[engine]
    return {
        "host": creds.get("host", creds.get("endpoint", creds.get("hostname"))),
        "port": int(creds.get("port", defaults["port"])),
        "username": creds.get("username", creds.get("user")),
        "password": creds.get("password"),
        "database": creds.get("database", creds.get("dbname", defaults["database"])),
    }


def _es_credentials_from_env() -> Optional[Dict[str, Any]]:
    """
    Resolve Elasticsearch username and password from environment variables.

    Accepts ES_PASSWORD/ES_USERNAME and ES_PASS/ES_USER. A password is required
    to trigger; the username defaults to "elastic". Returns None otherwise.
    """
    password = os.environ.get("ES_PASSWORD") or os.environ.get("ES_PASS")
    if not password:
        return None
    return {
        "username": os.environ.get("ES_USERNAME") or os.environ.get("ES_USER") or "elastic",
        "password": password,
    }


def _normalise_es_secret(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Map an Elasticsearch secret onto host, username, password (host may be None)."""
    return {
        "host": creds.get("host"),
        "username": creds.get("username", "elastic"),
        "password": creds.get("password"),
    }


def _resolve_es_host(credentials_host: Optional[str] = None) -> str:
    """
    Pick the Elasticsearch host: ES_HOST (or ELASTICSEARCH_HOST), then a host set
    explicitly through configure(es_host=...) or Config(es_host=...), then the host
    carried by the resolved credentials, then the configured default (localhost:9200).
    """
    env_host = os.environ.get("ES_HOST") or os.environ.get("ELASTICSEARCH_HOST")
    if env_host:
        return env_host
    config = get_config()
    if getattr(config, "_es_host_explicit", False) is True:
        return config.es_host
    return credentials_host or config.es_host


def _es_host_with_port(host: str) -> str:
    """
    Return ``host`` unchanged when it is a URL (scheme://netloc) or already names a
    port; otherwise append the default port ``:9200``.
    """
    parsed = urlparse(host)
    if parsed.scheme and parsed.netloc:
        return host
    if ":" not in host:
        return f"{host}:9200"
    return host


def _slack_credentials_from_env() -> Optional[Dict[str, Any]]:
    """
    Resolve Slack credentials from SLACK_BOT_TOKEN (required to trigger) and
    SLACK_WEBHOOK_URL (optional). Returns None when no bot token is set.
    """
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        return None
    return {"bot_token": bot_token, "webhook_url": os.environ.get("SLACK_WEBHOOK_URL") or None}


def _normalise_slack_secret(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Slack secret onto bot_token (or legacy token) and optional webhook_url.

    An empty or null ``bot_token`` falls back to ``token``.
    """
    return {
        "bot_token": creds.get("bot_token") or creds.get("token"),
        "webhook_url": creds.get("webhook_url"),
    }


def get_database_credentials(secret_name: Optional[str] = None) -> Dict:
    """
    Get MySQL database credentials with standardized field names.

    Precedence (matching DatabaseClient):
    1. Environment variables (DB_HOST + DB_PASSWORD, optional DB_PORT,
       DB_USERNAME, DB_DATABASE)
    2. AWS Secrets Manager

    Args:
        secret_name: Override default secret name for Secrets Manager lookup.
            Ignored when credentials are resolved from environment variables.

    Returns:
        Dict with host, port, username, password, database
    """
    env_creds = _db_credentials_from_env("mysql")
    if env_creds is not None:
        return env_creds

    config = get_config()
    secret = secret_name or os.environ.get("DB_CREDENTIALS_SECRET") or config.db_credentials_secret
    if not secret:
        raise ValueError("No database secret name provided")

    return _normalise_db_secret(get_secret(secret), "mysql")


def get_elasticsearch_credentials(secret_name: Optional[str] = None) -> Dict:
    """
    Get Elasticsearch credentials.

    Precedence (matching ElasticsearchClient):
    1. Environment variables (ES_PASSWORD/ES_PASS + ES_USERNAME/ES_USER)
    2. AWS Secrets Manager

    The host is ES_HOST (or ELASTICSEARCH_HOST), then a host set with
    configure(es_host=...), then the secret's ``host`` field, then the default,
    with ``:9200`` appended when no port is given and it is not a URL.

    Args:
        secret_name: Override default secret name for Secrets Manager lookup.
            Ignored when credentials are resolved from environment variables.

    Returns:
        Dict with host, username, password
    """
    creds = _es_credentials_from_env()
    if creds is None:
        config = get_config()
        secret = secret_name or os.environ.get("ES_CREDENTIALS_SECRET") or config.es_credentials_secret
        if not secret:
            raise ValueError("No Elasticsearch secret name provided")
        creds = _normalise_es_secret(get_secret(secret))

    host = _es_host_with_port(_resolve_es_host(creds.get("host")))

    return {"host": host, "username": creds["username"], "password": creds["password"]}


def get_slack_credentials(secret_name: Optional[str] = None) -> Dict:
    """
    Get Slack bot credentials.

    Precedence (matching SlackClient):
    1. Environment variables (SLACK_BOT_TOKEN, optional SLACK_WEBHOOK_URL)
    2. AWS Secrets Manager

    Args:
        secret_name: Override default secret name for Secrets Manager lookup.
            Ignored when credentials are resolved from environment variables.

    Returns:
        Dict with bot_token and optional webhook_url
    """
    env_creds = _slack_credentials_from_env()
    if env_creds is not None:
        return env_creds

    config = get_config()
    secret = secret_name or os.environ.get("SLACK_CREDENTIALS_SECRET") or config.slack_credentials_secret
    if not secret:
        raise ValueError("No Slack secret name provided")

    return _normalise_slack_secret(get_secret(secret))


def get_api_key(secret_name: str, key_field: str = "api_key") -> str:
    """
    Get a simple API key from secrets.

    Args:
        secret_name: Name of the secret
        key_field: Field name containing the key (default: 'api_key')

    Returns:
        The API key string
    """
    secret = get_secret(secret_name)

    if key_field not in secret:
        raise KeyError(f"Field '{key_field}' not found in secret {secret_name}")

    return secret[key_field]


def clear_cache() -> None:
    """Clear the secrets cache. Useful for long-running Lambdas."""
    global _secrets_cache
    _secrets_cache.clear()
    log.info("Cleared secrets cache")
