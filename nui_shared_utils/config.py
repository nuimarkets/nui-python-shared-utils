"""
Configuration system for AWS Lambda shared utilities.

This module provides configurable defaults and environment-based overrides
to make the library suitable for different deployment environments.
"""

import os
from typing import Dict, Optional, Any


class Config:
    """Configuration class for AWS Lambda shared utilities with environment-based overrides."""

    def __init__(
        self,
        # Elasticsearch configuration
        es_host: Optional[str] = None,
        es_credentials_secret: Optional[str] = None,
        # Database configuration
        db_credentials_secret: Optional[str] = None,
        # Slack configuration
        slack_credentials_secret: Optional[str] = None,
        # AWS configuration
        aws_region: Optional[str] = None,
    ):
        """
        Initialize configuration with optional overrides.

        Args:
            es_host: Elasticsearch host (default: localhost:9200)
            es_credentials_secret: AWS secret name for ES credentials
            db_credentials_secret: AWS secret name for database credentials
            slack_credentials_secret: AWS secret name for Slack credentials
            aws_region: AWS region for secrets/services
        """
        # Elasticsearch settings. An es_host passed here outranks a host carried in a
        # credentials secret; the env-derived and built-in values do not.
        self._es_host_explicit = es_host is not None
        if es_host is not None:
            self.es_host = es_host
        else:
            self.es_host = os.environ.get("ES_HOST") or os.environ.get("ELASTICSEARCH_HOST") or "localhost:9200"

        if es_credentials_secret is not None:
            self.es_credentials_secret = es_credentials_secret
        else:
            self.es_credentials_secret = (
                os.environ.get("ES_CREDENTIALS_SECRET")
                or os.environ.get("ELASTICSEARCH_CREDENTIALS_SECRET")
                or "elasticsearch-credentials"
            )

        # Database settings
        if db_credentials_secret is not None:
            self.db_credentials_secret = db_credentials_secret
        else:
            self.db_credentials_secret = (
                os.environ.get("DB_CREDENTIALS_SECRET")
                or os.environ.get("DATABASE_CREDENTIALS_SECRET")
                or "database-credentials"
            )

        # Slack settings
        if slack_credentials_secret is not None:
            self.slack_credentials_secret = slack_credentials_secret
        else:
            self.slack_credentials_secret = os.environ.get("SLACK_CREDENTIALS_SECRET") or "slack-credentials"

        # AWS settings
        if aws_region is not None:
            self.aws_region = aws_region
        else:
            self.aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    def to_dict(self) -> Dict[str, Any]:
        """Return configuration as dictionary for debugging/logging."""
        return {
            "es_host": self.es_host,
            "es_credentials_secret": self.es_credentials_secret,
            "db_credentials_secret": self.db_credentials_secret,
            "slack_credentials_secret": self.slack_credentials_secret,
            "aws_region": self.aws_region,
        }


# Global default configuration instance
_default_config = None


def get_config() -> Config:
    """Get the current global configuration instance."""
    global _default_config
    if _default_config is None:
        _default_config = Config()
    return _default_config


def set_config(config: Config) -> None:
    """Set a new global configuration instance."""
    global _default_config
    _default_config = config


def configure(**kwargs) -> Config:
    """
    Configure the global configuration with keyword arguments.

    This is a convenience function equivalent to:
    set_config(Config(**kwargs))

    Returns:
        The new configuration instance
    """
    config = Config(**kwargs)
    set_config(config)
    return config


# Legacy compatibility - environment variable checking functions
def get_es_host() -> str:
    """Get Elasticsearch host from configuration (legacy compatibility)."""
    return get_config().es_host


def get_es_credentials_secret() -> str:
    """Get Elasticsearch credentials secret name (legacy compatibility)."""
    return get_config().es_credentials_secret


def get_db_credentials_secret() -> str:
    """Get database credentials secret name (legacy compatibility)."""
    return get_config().db_credentials_secret


def get_slack_credentials_secret() -> str:
    """Get Slack credentials secret name (legacy compatibility)."""
    return get_config().slack_credentials_secret
