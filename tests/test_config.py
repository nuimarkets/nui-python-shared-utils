"""
Tests for config module.
"""

import os
from unittest.mock import patch
from nui_shared_utils.config import (
    Config,
    get_config,
    set_config,
    configure,
    get_es_host,
    get_es_credentials_secret,
    get_db_credentials_secret,
    get_slack_credentials_secret,
)


class TestConfig:
    """Tests for Config class initialization and behavior."""

    @patch.dict(os.environ, {}, clear=True)
    def test_init_all_defaults(self):
        """Test Config initialization with all default values."""
        config = Config()

        assert config.es_host == "localhost:9200"
        assert config.es_credentials_secret == "elasticsearch-credentials"
        assert config.db_credentials_secret == "database-credentials"
        assert config.slack_credentials_secret == "slack-credentials"
        assert config.aws_region == "us-east-1"

    @patch.dict(
        os.environ,
        {
            "ES_HOST": "custom-es:9200",
            "ELASTICSEARCH_HOST": "alt-es:9200",  # Should not be used when ES_HOST is set
            "ES_CREDENTIALS_SECRET": "custom-es-secret",
            "DB_CREDENTIALS_SECRET": "custom-db-secret",
            "SLACK_CREDENTIALS_SECRET": "custom-slack-secret",
            "AWS_REGION": "eu-west-1",
        },
    )
    def test_init_env_var_precedence(self):
        """Test that environment variables override defaults."""
        config = Config()

        assert config.es_host == "custom-es:9200"  # ES_HOST takes precedence
        assert config.es_credentials_secret == "custom-es-secret"
        assert config.db_credentials_secret == "custom-db-secret"
        assert config.slack_credentials_secret == "custom-slack-secret"
        assert config.aws_region == "eu-west-1"

    @patch.dict(
        os.environ,
        {
            "ELASTICSEARCH_HOST": "env-es:9200",
            "ELASTICSEARCH_CREDENTIALS_SECRET": "env-es-secret",
            "DATABASE_CREDENTIALS_SECRET": "env-db-secret",
            "AWS_DEFAULT_REGION": "ap-southeast-2",
        },
        clear=True,
    )
    def test_init_alternative_env_vars(self):
        """Test alternative environment variable names."""
        config = Config()

        # Should use alternative env var when primary not set
        assert config.es_host == "env-es:9200"
        assert config.es_credentials_secret == "env-es-secret"
        assert config.db_credentials_secret == "env-db-secret"
        assert config.aws_region == "ap-southeast-2"  # AWS_DEFAULT_REGION fallback

    @patch.dict(os.environ, {"ES_HOST": "env-host"})
    def test_init_param_precedence(self):
        """Test that constructor parameters override everything."""
        config = Config(
            es_host="param-host:9200",
            es_credentials_secret="param-es-secret",
            db_credentials_secret="param-db-secret",
            slack_credentials_secret="param-slack-secret",
            aws_region="us-west-2",
        )

        # Parameters should override environment variables
        assert config.es_host == "param-host:9200"
        assert config.es_credentials_secret == "param-es-secret"
        assert config.db_credentials_secret == "param-db-secret"
        assert config.slack_credentials_secret == "param-slack-secret"
        assert config.aws_region == "us-west-2"

    @patch.dict(os.environ, {}, clear=True)
    def test_init_mixed_sources(self):
        """Test initialization with mixed parameter and environment sources."""
        with patch.dict(os.environ, {"ES_HOST": "env-es:9200", "AWS_REGION": "eu-central-1"}):
            config = Config(es_credentials_secret="param-secret", db_credentials_secret="param-db-secret")

            # Should use environment for es_host and aws_region
            assert config.es_host == "env-es:9200"
            assert config.aws_region == "eu-central-1"

            # Should use parameters for secrets
            assert config.es_credentials_secret == "param-secret"
            assert config.db_credentials_secret == "param-db-secret"

            # Should use defaults for unspecified values
            assert config.slack_credentials_secret == "slack-credentials"

    def test_to_dict(self):
        """Test configuration serialization to dictionary."""
        config = Config(
            es_host="test-host:9200",
            es_credentials_secret="test-es-secret",
            db_credentials_secret="test-db-secret",
            slack_credentials_secret="test-slack-secret",
            aws_region="test-region",
        )

        config_dict = config.to_dict()

        expected = {
            "es_host": "test-host:9200",
            "es_credentials_secret": "test-es-secret",
            "db_credentials_secret": "test-db-secret",
            "slack_credentials_secret": "test-slack-secret",
            "aws_region": "test-region",
        }

        assert config_dict == expected


class TestGlobalConfig:
    """Tests for global configuration management functions."""

    def setup_method(self):
        """Reset global config before each test."""
        # Reset the global config
        from nui_shared_utils import config as config_module

        config_module._default_config = None

    @patch.dict(os.environ, {}, clear=True)
    def test_get_config_creates_default(self):
        """Test that get_config creates default config when none exists."""
        config = get_config()

        assert isinstance(config, Config)
        assert config.es_host == "localhost:9200"  # Default value

    def test_get_config_returns_same_instance(self):
        """Test that get_config returns the same instance on subsequent calls."""
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2  # Same object instance

    def test_set_config(self):
        """Test setting a custom configuration."""
        custom_config = Config(es_host="custom:9200")
        set_config(custom_config)

        retrieved_config = get_config()

        assert retrieved_config is custom_config
        assert retrieved_config.es_host == "custom:9200"

    def test_configure_convenience_function(self):
        """Test the configure convenience function."""
        config = configure(es_host="configured:9200", aws_region="configured-region")

        assert isinstance(config, Config)
        assert config.es_host == "configured:9200"
        assert config.aws_region == "configured-region"

        # Should be set as global config
        global_config = get_config()
        assert global_config is config


class TestLegacyCompatibilityFunctions:
    """Tests for legacy compatibility functions."""

    def setup_method(self):
        """Reset global config before each test."""
        from nui_shared_utils import config as config_module

        config_module._default_config = None

    def test_get_es_host(self):
        """Test get_es_host legacy function."""
        set_config(Config(es_host="legacy-es:9200"))

        result = get_es_host()

        assert result == "legacy-es:9200"

    def test_get_es_credentials_secret(self):
        """Test get_es_credentials_secret legacy function."""
        set_config(Config(es_credentials_secret="legacy-es-secret"))

        result = get_es_credentials_secret()

        assert result == "legacy-es-secret"

    def test_get_db_credentials_secret(self):
        """Test get_db_credentials_secret legacy function."""
        set_config(Config(db_credentials_secret="legacy-db-secret"))

        result = get_db_credentials_secret()

        assert result == "legacy-db-secret"

    def test_get_slack_credentials_secret(self):
        """Test get_slack_credentials_secret legacy function."""
        set_config(Config(slack_credentials_secret="legacy-slack-secret"))

        result = get_slack_credentials_secret()

        assert result == "legacy-slack-secret"


class TestConfigurationPrecedence:
    """Tests for configuration precedence edge cases."""

    @patch.dict(
        os.environ,
        {
            "ES_HOST": "primary-es:9200",
            "ELASTICSEARCH_HOST": "secondary-es:9200",
        },
    )
    def test_primary_env_var_wins(self):
        """Test that primary environment variable takes precedence."""
        config = Config()

        assert config.es_host == "primary-es:9200"

    @patch.dict(
        os.environ,
        {
            "ELASTICSEARCH_HOST": "secondary-es:9200",
        },
        clear=True,
    )
    def test_secondary_env_var_used_when_primary_missing(self):
        """Test that secondary environment variable is used when primary is missing."""
        config = Config()

        assert config.es_host == "secondary-es:9200"

    @patch.dict(
        os.environ,
        {
            "AWS_REGION": "primary-region",
            "AWS_DEFAULT_REGION": "secondary-region",
        },
    )
    def test_aws_region_precedence(self):
        """Test AWS region environment variable precedence."""
        config = Config()

        assert config.aws_region == "primary-region"

    def test_aws_default_region_fallback(self, monkeypatch):
        """Test AWS_DEFAULT_REGION fallback."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "secondary-region")
        config = Config()

        assert config.aws_region == "secondary-region"

    @patch.dict(os.environ, {}, clear=True)
    def test_none_parameter_uses_default(self):
        """Test that None parameters fall back to environment/defaults."""
        with patch.dict(os.environ, {"ES_HOST": "env-host"}):
            config = Config(es_host=None)

            # None should fall back to environment
            assert config.es_host == "env-host"

    @patch.dict(os.environ, {}, clear=True)
    def test_empty_string_parameter_overrides(self):
        """Test that empty string parameters override defaults."""
        config = Config(es_host="")

        # Empty string should be used, not fall back to default
        assert config.es_host == ""
