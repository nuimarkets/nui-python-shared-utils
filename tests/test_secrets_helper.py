"""
Tests for secrets_helper module.
"""

import pytest
import json
import os
from unittest.mock import patch, Mock
from botocore.exceptions import ClientError
from nui_shared_utils import secrets_helper


class TestGetSecret:
    """Tests for get_secret function."""

    def test_get_secret_success(self, mock_secrets_manager):
        """Test successful secret retrieval."""
        result = secrets_helper.get_secret("test-secret")

        assert result == {
            "username": "test_user",
            "password": "test_pass",
            "host": "test_host",
            "port": 3306,
            "database": "test_db",
        }
        mock_secrets_manager.get_secret_value.assert_called_once_with(SecretId="test-secret")

    @pytest.mark.unit
    def test_get_secret_uses_aws_region_when_session_region_missing(self, mock_boto3_session, mock_secrets_manager):
        """Test AWS_REGION fallback when boto3 session has no region."""
        mock_boto3_session.region_name = None

        with patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}, clear=True):
            secrets_helper.get_secret("test-secret")

        mock_boto3_session.client.assert_called_once_with(service_name="secretsmanager", region_name="eu-west-1")

    @pytest.mark.unit
    def test_get_secret_requires_region_when_session_and_env_missing(self, mock_boto3_session):
        """Test clear error when no Secrets Manager region is configured."""
        mock_boto3_session.region_name = None

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(Exception) as exc_info:
                secrets_helper.get_secret("test-secret")

        assert "AWS region not configured for Secrets Manager lookup" in str(exc_info.value)
        mock_boto3_session.client.assert_not_called()

    def test_get_secret_cached(self, mock_secrets_manager):
        """Test that secrets are cached after first retrieval."""
        # First call
        result1 = secrets_helper.get_secret("test-secret")
        # Second call should use cache
        result2 = secrets_helper.get_secret("test-secret")

        assert result1 == result2
        # Should only call AWS once
        assert mock_secrets_manager.get_secret_value.call_count == 1

    def test_get_secret_binary(self, mock_secrets_manager):
        """Test retrieving binary secret."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretBinary": json.dumps({"key": "value"}).encode("utf-8")
        }

        result = secrets_helper.get_secret("binary-secret")
        assert result == {"key": "value"}

    def test_get_secret_not_found(self, mock_secrets_manager):
        """Test handling of non-existent secret."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("missing-secret")

        assert "Secret missing-secret not found" in str(exc_info.value)

    def test_get_secret_decryption_failure(self, mock_secrets_manager):
        """Test handling of decryption failure."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "DecryptionFailureException"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("encrypted-secret")

        assert "Cannot decrypt secret encrypted-secret" in str(exc_info.value)

    def test_get_secret_internal_error(self, mock_secrets_manager):
        """Test handling of internal service error."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceErrorException"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("test-secret")

        assert "Internal service error retrieving test-secret" in str(exc_info.value)

    def test_get_secret_invalid_parameter(self, mock_secrets_manager):
        """Test handling of invalid parameter."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InvalidParameterException"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("test-secret")

        assert "Invalid parameter for test-secret" in str(exc_info.value)

    def test_get_secret_invalid_request(self, mock_secrets_manager):
        """Test handling of invalid request."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequestException"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("test-secret")

        assert "Invalid request for test-secret" in str(exc_info.value)

    def test_get_secret_unknown_error(self, mock_secrets_manager):
        """Test handling of unknown AWS error."""
        mock_secrets_manager.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "UnknownError"}}, "GetSecretValue"
        )

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("test-secret")

        assert "Error retrieving secret test-secret: UnknownError" in str(exc_info.value)

    def test_get_secret_unexpected_error(self, mock_secrets_manager):
        """Test handling of non-AWS errors."""
        mock_secrets_manager.get_secret_value.side_effect = ValueError("Unexpected error")

        with pytest.raises(Exception) as exc_info:
            secrets_helper.get_secret("test-secret")

        assert "Unexpected error retrieving secret test-secret" in str(exc_info.value)


class TestGetDatabaseCredentials:
    """Tests for get_database_credentials function."""

    @pytest.fixture(autouse=True)
    def _clear_credential_env(self, monkeypatch):
        """The helper reads these before Secrets Manager; clear them so the SM path runs."""
        for name in ["DB_HOST", "DB_PASSWORD", "DB_PORT", "DB_USERNAME", "DB_DATABASE"]:
            monkeypatch.delenv(name, raising=False)

    def test_get_database_credentials_success(self, mock_secrets_manager):
        """Test successful database credentials retrieval."""
        result = secrets_helper.get_database_credentials("db-secret")

        assert result == {
            "host": "test_host",
            "port": 3306,
            "username": "test_user",
            "password": "test_pass",
            "database": "test_db",
        }

    def test_get_database_credentials_from_env(self, mock_secrets_manager):
        """Test using environment variable for secret name."""
        with patch.dict("os.environ", {"DB_CREDENTIALS_SECRET": "env-db-secret"}):
            result = secrets_helper.get_database_credentials()

        mock_secrets_manager.get_secret_value.assert_called_with(SecretId="env-db-secret")

    @patch.dict(os.environ, {}, clear=True)  # Clear environment variables
    @patch("nui_shared_utils.secrets_helper.get_config")
    def test_get_database_credentials_no_secret(self, mock_get_config):
        """Test error when no secret name provided."""
        # Mock config to return empty/None secret name
        mock_config = Mock()
        mock_config.db_credentials_secret = None
        mock_get_config.return_value = mock_config

        with pytest.raises(ValueError) as exc_info:
            secrets_helper.get_database_credentials()

        assert "No database secret name provided" in str(exc_info.value)

    def test_get_database_credentials_field_mapping(self, mock_secrets_manager):
        """Test field name normalization."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {"endpoint": "db.example.com", "user": "dbuser", "password": "dbpass", "dbname": "mydb", "port": "5432"}
            )
        }

        result = secrets_helper.get_database_credentials("db-secret")

        assert result == {
            "host": "db.example.com",
            "port": 5432,
            "username": "dbuser",
            "password": "dbpass",
            "database": "mydb",
        }


class TestGetElasticsearchCredentials:
    """Tests for get_elasticsearch_credentials function."""

    @patch.dict("os.environ", {"ES_HOST": "localhost:9200"}, clear=True)
    def test_get_elasticsearch_credentials_success(self, mock_secrets_manager):
        """Test successful Elasticsearch credentials retrieval."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "elastic", "password": "es_pass"})
        }

        result = secrets_helper.get_elasticsearch_credentials("es-secret")

        assert result == {"host": "localhost:9200", "username": "elastic", "password": "es_pass"}

    def test_get_elasticsearch_credentials_custom_host(self, mock_secrets_manager):
        """Test using custom host from environment."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "elastic", "password": "es_pass"})
        }

        with patch.dict("os.environ", {"ES_HOST": "custom-es.example.com:9200"}):
            result = secrets_helper.get_elasticsearch_credentials("es-secret")

        assert result["host"] == "custom-es.example.com:9200"

    def test_get_elasticsearch_credentials_from_env(self, mock_secrets_manager):
        """Test using environment variable for secret name."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "elastic", "password": "es_pass"})
        }

        with patch.dict("os.environ", {"ES_CREDENTIALS_SECRET": "env-es-secret"}):
            result = secrets_helper.get_elasticsearch_credentials()

        mock_secrets_manager.get_secret_value.assert_called_with(SecretId="env-es-secret")

    @patch.dict(os.environ, {}, clear=True)  # Clear environment variables
    @patch("nui_shared_utils.secrets_helper.get_config")
    def test_get_elasticsearch_credentials_no_secret(self, mock_get_config):
        """Test error when no secret name provided."""
        # Mock config to return empty/None secret name
        mock_config = Mock()
        mock_config.es_credentials_secret = None
        mock_get_config.return_value = mock_config

        with pytest.raises(ValueError) as exc_info:
            secrets_helper.get_elasticsearch_credentials()

        assert "No Elasticsearch secret name provided" in str(exc_info.value)

    @patch.dict(
        "os.environ", {"ES_PASSWORD": "env_pass", "ES_USERNAME": "env_user", "ES_HOST": "env-host:9200"}, clear=True
    )
    def test_get_elasticsearch_credentials_env_vars_first(self, mock_secrets_manager):
        """Env vars take precedence over Secrets Manager."""
        result = secrets_helper.get_elasticsearch_credentials("es-secret")

        assert result == {"host": "env-host:9200", "username": "env_user", "password": "env_pass"}
        mock_secrets_manager.get_secret_value.assert_not_called()

    @patch.dict("os.environ", {"ES_PASS": "env_pass2", "ES_USER": "env_user2", "ES_HOST": "env-host:9200"}, clear=True)
    def test_get_elasticsearch_credentials_short_env_vars(self, mock_secrets_manager):
        """ES_PASS/ES_USER convention also works."""
        result = secrets_helper.get_elasticsearch_credentials()

        assert result == {"host": "env-host:9200", "username": "env_user2", "password": "env_pass2"}
        mock_secrets_manager.get_secret_value.assert_not_called()

    @patch.dict("os.environ", {"ES_PASSWORD": "env_pass"}, clear=True)
    @patch("nui_shared_utils.secrets_helper.get_config")
    def test_get_elasticsearch_credentials_partial_env_vars(self, mock_get_config):
        """Password-only env var works, username defaults to 'elastic'."""
        mock_config = Mock()
        mock_config.es_host = "default-host"
        mock_get_config.return_value = mock_config

        result = secrets_helper.get_elasticsearch_credentials()

        assert result == {"host": "default-host:9200", "username": "elastic", "password": "env_pass"}

    @patch.dict("os.environ", {"ES_PASSWORD": "env_pass", "ES_HOST": "env-host:9200"}, clear=True)
    def test_get_elasticsearch_credentials_env_vars_override_secret_name(self, mock_secrets_manager):
        """Env vars win even when secret_name is explicitly passed."""
        result = secrets_helper.get_elasticsearch_credentials(secret_name="my-specific-secret")

        assert result["password"] == "env_pass"
        mock_secrets_manager.get_secret_value.assert_not_called()


class TestGetSlackCredentials:
    """Tests for get_slack_credentials function."""

    @pytest.fixture(autouse=True)
    def _clear_credential_env(self, monkeypatch):
        """The helper reads these before Secrets Manager; clear them so the SM path runs."""
        for name in ["SLACK_BOT_TOKEN", "SLACK_WEBHOOK_URL"]:
            monkeypatch.delenv(name, raising=False)

    def test_get_slack_credentials_success(self, mock_secrets_manager):
        """Test successful Slack credentials retrieval."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"bot_token": "xoxb-test-token", "webhook_url": "https://hooks.slack.com/test"})
        }

        result = secrets_helper.get_slack_credentials("slack-secret")

        assert result == {"bot_token": "xoxb-test-token", "webhook_url": "https://hooks.slack.com/test"}

    def test_get_slack_credentials_token_field(self, mock_secrets_manager):
        """Test using 'token' field instead of 'bot_token'."""
        mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"token": "xoxb-test-token"})}

        result = secrets_helper.get_slack_credentials("slack-secret")

        assert result == {"bot_token": "xoxb-test-token", "webhook_url": None}

    def test_get_slack_credentials_from_env(self, mock_secrets_manager):
        """Test using environment variable for secret name."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"bot_token": "xoxb-test-token"})
        }

        with patch.dict("os.environ", {"SLACK_CREDENTIALS_SECRET": "env-slack-secret"}):
            result = secrets_helper.get_slack_credentials()

        mock_secrets_manager.get_secret_value.assert_called_with(SecretId="env-slack-secret")

    @patch.dict(os.environ, {}, clear=True)  # Clear environment variables
    @patch("nui_shared_utils.secrets_helper.get_config")
    def test_get_slack_credentials_no_secret(self, mock_get_config):
        """Test error when no secret name provided."""
        # Mock config to return empty/None secret name
        mock_config = Mock()
        mock_config.slack_credentials_secret = None
        mock_get_config.return_value = mock_config

        with pytest.raises(ValueError) as exc_info:
            secrets_helper.get_slack_credentials()

        assert "No Slack secret name provided" in str(exc_info.value)


class TestGetApiKey:
    """Tests for get_api_key function."""

    def test_get_api_key_success(self, mock_secrets_manager):
        """Test successful API key retrieval."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "test-api-key-123"})
        }

        result = secrets_helper.get_api_key("api-secret")

        assert result == "test-api-key-123"

    def test_get_api_key_custom_field(self, mock_secrets_manager):
        """Test retrieving API key from custom field."""
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"custom_key": "custom-api-key-456"})
        }

        result = secrets_helper.get_api_key("api-secret", key_field="custom_key")

        assert result == "custom-api-key-456"

    def test_get_api_key_missing_field(self, mock_secrets_manager):
        """Test error when key field not found."""
        mock_secrets_manager.get_secret_value.return_value = {"SecretString": json.dumps({"other_field": "value"})}

        with pytest.raises(KeyError) as exc_info:
            secrets_helper.get_api_key("api-secret")

        assert "Field 'api_key' not found in secret api-secret" in str(exc_info.value)


class TestClearCache:
    """Tests for clear_cache function."""

    def test_clear_cache(self, mock_secrets_manager):
        """Test cache clearing functionality."""
        # Populate cache
        secrets_helper.get_secret("test-secret")
        assert "test-secret" in secrets_helper._secrets_cache

        # Clear cache
        secrets_helper.clear_cache()
        assert len(secrets_helper._secrets_cache) == 0

        # Verify next call hits AWS again
        secrets_helper.get_secret("test-secret")
        assert mock_secrets_manager.get_secret_value.call_count == 2


class TestHelperEnvBeforeSecretsManager:
    """The DB and Slack helpers check env vars before Secrets Manager, like the clients."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in [
            "DB_HOST",
            "DB_PASSWORD",
            "DB_PORT",
            "DB_USERNAME",
            "DB_DATABASE",
            "SLACK_BOT_TOKEN",
            "SLACK_WEBHOOK_URL",
        ]:
            monkeypatch.delenv(name, raising=False)

    def test_database_helper_uses_env_without_secrets_manager(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")
        monkeypatch.setenv("DB_CREDENTIALS_SECRET", "db")
        with patch("nui_shared_utils.secrets_helper.get_secret") as get_secret:
            result = secrets_helper.get_database_credentials()

        get_secret.assert_not_called()
        assert result == {"host": "h", "port": 3306, "username": "root", "password": "p", "database": "app"}

    def test_slack_helper_uses_env_without_secrets_manager(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        monkeypatch.setenv("SLACK_CREDENTIALS_SECRET", "sl")
        with patch("nui_shared_utils.secrets_helper.get_secret") as get_secret:
            result = secrets_helper.get_slack_credentials()

        get_secret.assert_not_called()
        assert result == {"bot_token": "xoxb-env", "webhook_url": None}

    def test_database_helper_env_needs_no_secret_name(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")
        monkeypatch.setenv("DB_PORT", "3307")
        monkeypatch.setenv("DB_USERNAME", "app-user")
        monkeypatch.setenv("DB_DATABASE", "reports")
        monkeypatch.delenv("DB_CREDENTIALS_SECRET", raising=False)
        with patch("nui_shared_utils.secrets_helper.get_config", return_value=Mock(db_credentials_secret=None)):
            result = secrets_helper.get_database_credentials()

        assert result == {"host": "h", "port": 3307, "username": "app-user", "password": "p", "database": "reports"}

    def test_database_helper_host_without_password_uses_secrets_manager(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "h")
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value={"host": "sm", "password": "x"}) as gs:
            result = secrets_helper.get_database_credentials("db")

        gs.assert_called_once_with("db")
        assert result["host"] == "sm"
        assert result["port"] == 3306

    def test_database_helper_invalid_port_env_raises(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")
        monkeypatch.setenv("DB_PORT", "abc")
        with pytest.raises(ValueError, match="DB_PORT must be an integer"):
            secrets_helper.get_database_credentials()

    def test_slack_helper_env_includes_webhook(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.com/x")
        monkeypatch.delenv("SLACK_CREDENTIALS_SECRET", raising=False)
        with patch("nui_shared_utils.secrets_helper.get_config", return_value=Mock(slack_credentials_secret=None)):
            result = secrets_helper.get_slack_credentials()

        assert result == {"bot_token": "xoxb-env", "webhook_url": "https://hooks.example.com/x"}

    def test_slack_client_and_helper_agree_on_secret_shape(self):
        from nui_shared_utils.slack_client import SlackClient

        secret = {"token": "xoxb-legacy"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            helper = secrets_helper.get_slack_credentials("sl")
        with (
            patch("nui_shared_utils.base_client.get_secret", return_value=dict(secret)),
            patch("nui_shared_utils.slack_client.WebClient"),
        ):
            client = SlackClient(secret_name="sl")

        assert helper == {"bot_token": "xoxb-legacy", "webhook_url": None}
        assert client.credentials == {"token": "xoxb-legacy", **helper}

    def test_slack_client_keeps_extra_secret_keys(self):
        from nui_shared_utils.slack_client import SlackClient

        secret = {"bot_token": "xoxb-1", "signing_secret": "s", "app_token": "xapp-1"}
        with (
            patch("nui_shared_utils.base_client.get_secret", return_value=dict(secret)),
            patch("nui_shared_utils.slack_client.WebClient"),
        ):
            client = SlackClient(secret_name="sl")

        assert client.credentials == {**secret, "webhook_url": None}

    def test_database_client_and_helper_agree_on_env(self, monkeypatch):
        from nui_shared_utils.db_client import DatabaseClient

        monkeypatch.setenv("DB_HOST", "h")
        monkeypatch.setenv("DB_PASSWORD", "p")

        assert DatabaseClient().credentials == secrets_helper.get_database_credentials()

    @pytest.mark.parametrize("bot_token", [None, ""])
    def test_slack_empty_bot_token_falls_back_to_legacy_token(self, bot_token):
        from nui_shared_utils.slack_client import SlackClient

        secret = {"bot_token": bot_token, "token": "xoxb-legacy"}
        with patch("nui_shared_utils.secrets_helper.get_secret", return_value=dict(secret)):
            helper = secrets_helper.get_slack_credentials("sl")
        with (
            patch("nui_shared_utils.base_client.get_secret", return_value=dict(secret)),
            patch("nui_shared_utils.slack_client.WebClient") as web_client,
        ):
            SlackClient(secret_name="sl")

        assert helper["bot_token"] == "xoxb-legacy"
        web_client.assert_called_once_with(token="xoxb-legacy")
