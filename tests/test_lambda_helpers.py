"""
Tests for Lambda context helpers.
"""

import pytest


# Test markers
pytestmark = pytest.mark.unit


class TestGetLambdaEnvironmentInfo:
    """Tests for get_lambda_environment_info function"""

    def test_local_environment_detection(self, monkeypatch):
        """Test is_local=True when AWS_LAMBDA_RUNTIME_API not set"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["is_local"] is True

    def test_lambda_environment_detection(self, monkeypatch):
        """Test is_local=False when AWS_LAMBDA_RUNTIME_API is set"""
        monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["is_local"] is False

    def test_returns_all_expected_keys(self, monkeypatch):
        """Test all expected keys are present in result"""
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        expected_keys = {
            "environment",
            "aws_region",
            "function_name",
            "function_version",
            "memory_limit",
            "is_local",
        }
        assert set(env_info.keys()) == expected_keys

    def test_extracts_environment_variable(self, monkeypatch):
        """Test ENVIRONMENT env var is extracted"""
        monkeypatch.setenv("ENVIRONMENT", "prod")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "prod"

    def test_extracts_env_fallback(self, monkeypatch):
        """Test ENV env var fallback when ENVIRONMENT not set"""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("ENV", "staging")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "staging"

    def test_extracts_stage_fallback(self, monkeypatch):
        """Test STAGE env var fallback when ENVIRONMENT and ENV not set"""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setenv("STAGE", "dev")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "dev"

    def test_default_values_for_missing_vars(self, monkeypatch):
        """Test default values when environment variables are not set"""
        # Clear all relevant environment variables
        for var in [
            "AWS_LAMBDA_RUNTIME_API",
            "ENVIRONMENT",
            "ENV",
            "STAGE",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "AWS_LAMBDA_FUNCTION_NAME",
            "AWS_LAMBDA_FUNCTION_VERSION",
            "AWS_LAMBDA_FUNCTION_MEMORY_SIZE",
        ]:
            monkeypatch.delenv(var, raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "unknown"
        assert env_info["aws_region"] == ""
        assert env_info["function_name"] == ""
        assert env_info["function_version"] == ""
        assert env_info["memory_limit"] == ""
        assert env_info["is_local"] is True

    def test_extracts_aws_region(self, monkeypatch):
        """Test AWS_REGION is extracted"""
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["aws_region"] == "ap-southeast-2"

    def test_extracts_aws_default_region_fallback(self, monkeypatch):
        """Test AWS_DEFAULT_REGION fallback when AWS_REGION not set"""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["aws_region"] == "us-west-2"

    def test_extracts_function_name(self, monkeypatch):
        """Test AWS_LAMBDA_FUNCTION_NAME is extracted"""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "my-lambda")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["function_name"] == "my-lambda"

    def test_extracts_function_version(self, monkeypatch):
        """Test AWS_LAMBDA_FUNCTION_VERSION is extracted"""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["function_version"] == "$LATEST"

    def test_extracts_memory_limit(self, monkeypatch):
        """Test AWS_LAMBDA_FUNCTION_MEMORY_SIZE is extracted"""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "512")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["memory_limit"] == "512"

    def test_normalizes_environment_production(self, monkeypatch):
        """Test 'production' is normalized to 'prod'"""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "prod"

    def test_normalizes_environment_prd(self, monkeypatch):
        """Test 'prd' is normalized to 'prod'"""
        monkeypatch.setenv("ENVIRONMENT", "prd")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "prod"

    def test_normalizes_environment_development(self, monkeypatch):
        """Test 'development' is normalized to 'dev'"""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "dev"

    def test_environment_is_lowercased(self, monkeypatch):
        """Test environment value is lowercased"""
        monkeypatch.setenv("ENVIRONMENT", "PROD")
        monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info["environment"] == "prod"

    def test_full_lambda_environment(self, monkeypatch):
        """Test with full Lambda environment variables set"""
        monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")
        monkeypatch.setenv("ENVIRONMENT", "prod")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "order-processor")
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "12")
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1024")

        from nui_shared_utils.lambda_helpers import get_lambda_environment_info

        env_info = get_lambda_environment_info()

        assert env_info == {
            "environment": "prod",
            "aws_region": "ap-southeast-2",
            "function_name": "order-processor",
            "function_version": "12",
            "memory_limit": "1024",
            "is_local": False,
        }


class TestExportedAPI:
    """Tests for module's public API"""

    def test_all_exports_defined(self):
        """Test __all__ exports match expected API"""
        from nui_shared_utils.lambda_helpers import __all__

        assert "get_lambda_environment_info" in __all__

    def test_importable_from_package_root(self):
        """Test function is accessible from package root"""
        from nui_shared_utils import get_lambda_environment_info

        assert callable(get_lambda_environment_info)
