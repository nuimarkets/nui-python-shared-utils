"""
Tests for AWS utility functions.
"""

from unittest.mock import patch, Mock


class TestAWSClientFactory:
    """Tests for AWS client factory functionality."""

    @patch("boto3.session.Session")
    def test_create_aws_client_default_region(self, mock_session_class):
        """Test AWS client creation with default region."""
        from nui_shared_utils.utils import create_aws_client

        mock_session = Mock()
        mock_session.region_name = None
        mock_session_class.return_value = mock_session

        mock_client = Mock()
        mock_session.client.return_value = mock_client

        # Test creating a client without explicit region
        result = create_aws_client("secretsmanager")

        # Should use default region
        mock_session.client.assert_called_with(service_name="secretsmanager", region_name="us-east-1")
        assert result == mock_client

    @patch("boto3.session.Session")
    def test_create_aws_client_explicit_region(self, mock_session_class):
        """Test AWS client creation with explicit region."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_client = Mock()
        mock_session.client.return_value = mock_client

        # Test create_aws_client("s3", region="eu-west-1")
        # Should use explicit region over defaults
        from nui_shared_utils.utils import create_aws_client

        service_name = "s3"
        explicit_region = "eu-west-1"

        result = create_aws_client(service_name, region=explicit_region)

        # This is the expected behavior
        mock_session.client.assert_called_with(service_name=service_name, region_name=explicit_region)
        assert result == mock_client

    @patch("boto3.session.Session")
    @patch("nui_shared_utils.utils.get_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_create_aws_client_session_region_fallback(self, mock_get_config, mock_session_class):
        """Test fallback to session region when available."""
        # Mock config without aws_region
        mock_config = Mock()
        mock_config.aws_region = None
        mock_get_config.return_value = mock_config

        # Mock session with region
        mock_session = Mock()
        mock_session.region_name = "session-region"
        mock_session_class.return_value = mock_session
        mock_client = Mock()
        mock_session.client.return_value = mock_client

        # When no explicit region provided, should use session region
        # before falling back to hardcoded default
        from nui_shared_utils.utils import create_aws_client

        result = create_aws_client("dynamodb")

        # Expected: session region > hardcoded default
        expected_region = "session-region"

        mock_session.client.assert_called_with(service_name="dynamodb", region_name=expected_region)
        assert result == mock_client


class TestAWSCredentialsHandling:
    """Tests for AWS credentials handling."""

    @patch("boto3.session.Session")
    def test_credentials_from_environment(self, mock_session_class):
        """Test credentials resolution from environment variables."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_client = Mock()
        mock_session.client.return_value = mock_client

        with patch.dict(
            "os.environ", {"AWS_ACCESS_KEY_ID": "test-access-key", "AWS_SECRET_ACCESS_KEY": "test-secret-key"}
        ):
            # Test that create_aws_client works with environment credentials
            from nui_shared_utils.utils import create_aws_client

            create_aws_client("s3")

            # Session should pick up environment credentials automatically
            mock_session_class.assert_called_once()

    @patch("boto3.session.Session")
    def test_credentials_from_role(self, mock_session_class):
        """Test credentials from IAM role (Lambda execution role)."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_client = Mock()
        mock_session.client.return_value = mock_client

        # Test that create_aws_client works with IAM role credentials
        from nui_shared_utils.utils import create_aws_client

        result = create_aws_client("lambda")

        # In Lambda, credentials come from execution role
        # No explicit credential handling needed
        mock_session_class.assert_called_once()
        assert result == mock_client
