"""
Base client class providing common functionality for AWS service clients.
"""

import logging
import warnings
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

from .config import get_config
from .secrets_helper import get_secret
from .utils import resolve_config_value, validate_required_param, handle_client_errors

log = logging.getLogger(__name__)


class BaseClient(ABC):
    """
    Base class for AWS service clients providing standardized:
    - Credential resolution and management
    - Configuration integration
    - Error handling patterns
    - Logging context
    """

    def __init__(
        self,
        secret_name: Optional[str] = None,
        config_key_prefix: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize base client with standardized credential and config resolution.

        Credential resolution precedence:
        1. Explicit ``credentials`` dict — skip other credential sources
           (an Elasticsearch host key still follows the client's host priority)
        2. Environment variables (per-client patterns) — skip Secrets Manager
        3. Secrets Manager (existing behavior) — unchanged

        Args:
            secret_name: Override secret name for credentials
            config_key_prefix: Prefix for config keys (e.g., 'slack', 'es', 'db')
            credentials: Direct credentials dict, bypasses Secrets Manager entirely
            **kwargs: Additional client-specific parameters
        """
        self.config = get_config()
        self.config_key_prefix = config_key_prefix or self._get_default_config_prefix()

        # Resolve and store credentials
        self.credentials = self._resolve_credentials(secret_name, credentials)

        # Store additional configuration
        self.client_config = kwargs

        # Initialize service-specific client
        self._service_client = self._create_service_client()

        log.info(
            f"Initialized {self.__class__.__name__}",
            extra={
                "client_type": self.__class__.__name__,
                "config_prefix": self.config_key_prefix,
                "has_credentials": bool(self.credentials),
            },
        )

    @abstractmethod
    def _get_default_config_prefix(self) -> str:
        """Return the default configuration key prefix for this client type."""
        pass

    @abstractmethod
    def _create_service_client(self) -> Any:
        """Create and return the underlying service client (e.g., WebClient, Elasticsearch)."""
        pass

    @abstractmethod
    def _get_default_secret_name(self) -> str:
        """Return the default secret name for this client type."""
        pass

    def _resolve_credentials_from_env(self) -> Optional[Dict[str, Any]]:
        """
        Resolve credentials from environment variables.

        Subclasses override this to check client-specific env vars
        (e.g. SLACK_BOT_TOKEN, ES_PASSWORD).  Return a credentials dict
        if the required env vars are present, or ``None`` to fall through
        to Secrets Manager.

        Returns:
            Credentials dict or None
        """
        return None

    def _resolve_credentials(
        self,
        secret_name: Optional[str],
        explicit_credentials: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Resolve credentials using standardized precedence.

        Precedence:
        1. Explicit credentials dict (constructor param)
        2. Environment variables (per-client ``_resolve_credentials_from_env``)
        3. AWS Secrets Manager (via ``_fetch_credentials_from_sm``)

        Args:
            secret_name: Optional override for secret name
            explicit_credentials: Direct credentials dict from constructor

        Returns:
            Dictionary containing resolved credentials
        """
        # 1. Explicit credentials dict — skip everything
        if explicit_credentials is not None:
            log.debug("Using explicitly provided credentials")
            return explicit_credentials

        # 2. Environment variables — skip Secrets Manager
        env_credentials = self._resolve_credentials_from_env()
        if env_credentials is not None:
            log.debug("Using credentials from environment variables")
            return env_credentials

        # 3. Secrets Manager
        return self._fetch_credentials_from_sm(secret_name)

    def _resolve_secret_name(self, secret_name: Optional[str]) -> str:
        """
        Resolve the Secrets Manager secret name using standard precedence:
        explicit param > env var > config default.

        Args:
            secret_name: Optional explicit override

        Returns:
            Resolved secret name string
        """
        resolved = resolve_config_value(
            secret_name,
            [
                f"{self.config_key_prefix.upper()}_CREDENTIALS_SECRET",
                f"{self.config_key_prefix.upper()}CREDENTIALS_SECRET",  # Alternative format
            ],
            getattr(self.config, f"{self.config_key_prefix}_credentials_secret", self._get_default_secret_name()),
        )
        validate_required_param(resolved, "secret_name")
        return resolved

    def _fetch_credentials_from_sm(self, secret_name: Optional[str]) -> Dict[str, Any]:
        """
        Fetch credentials from AWS Secrets Manager.

        Subclasses override this to customize the SM fetch (e.g. DB clients
        normalize database secret fields).

        Args:
            secret_name: Optional override for secret name

        Returns:
            Dictionary containing resolved credentials
        """
        resolved_secret_name = self._resolve_secret_name(secret_name)

        try:
            credentials = get_secret(resolved_secret_name)
            log.debug(f"Retrieved credentials from secret: {resolved_secret_name}")
            return credentials
        except Exception as e:
            log.error(
                f"Failed to retrieve credentials from secret: {resolved_secret_name}",
                extra={"secret_name": resolved_secret_name, "error": str(e)},
            )
            raise

    def _get_config_value(self, key: str, env_vars: Optional[list] = None, default: Any = None) -> Any:
        """
        Get configuration value with standardized precedence.

        Args:
            key: Configuration key (without prefix)
            env_vars: Environment variable names to check
            default: Default value

        Returns:
            Resolved configuration value
        """
        # Get from client config (constructor kwargs) first
        if key in self.client_config:
            return self.client_config[key]

        # Build full config key
        config_key = f"{self.config_key_prefix}_{key}"
        config_value = getattr(self.config, config_key, default)

        # Use utility for full resolution
        return resolve_config_value(
            None, env_vars or [], config_value  # No explicit parameter (already checked client_config)
        )

    @handle_client_errors(reraise=True)
    def _execute_with_error_handling(self, operation_name: str, operation_func, **log_context):
        """
        Execute an operation with standardized error handling and logging.

        Args:
            operation_name: Name of the operation for logging
            operation_func: Function to execute
            **log_context: Additional logging context

        Returns:
            Result of operation_func
        """
        context = {"client_type": self.__class__.__name__, "operation": operation_name, **log_context}

        log.debug(f"Executing {operation_name}", extra=context)

        try:
            result = operation_func()
            log.debug(f"Successfully completed {operation_name}", extra=context)
            return result
        except Exception as e:
            context["error_type"] = type(e).__name__
            context["error_message"] = str(e)
            log.error(f"Failed to execute {operation_name}: {e}", extra=context)
            raise

    def get_client_info(self) -> Dict[str, Any]:
        """
        Get information about this client instance.

        Returns:
            Dictionary with client information
        """
        return {
            "client_type": self.__class__.__name__,
            "config_prefix": self.config_key_prefix,
            "has_credentials": bool(self.credentials),
            "client_config": {k: v for k, v in self.client_config.items() if not k.startswith("_")},
        }


class ServiceHealthMixin:
    """
    Mixin providing common health check functionality for service clients.
    """

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a basic health check for the service.

        Returns:
            Dictionary with health status information
        """
        try:
            self._perform_health_check()
            return {
                "status": "healthy",
                "client_type": self.__class__.__name__,
                "timestamp": log.time.time() if hasattr(log, "time") else None,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "client_type": self.__class__.__name__,
                "error": str(e),
                "error_type": type(e).__name__,
                "timestamp": log.time.time() if hasattr(log, "time") else None,
            }

    @abstractmethod
    def _perform_health_check(self):
        """Perform service-specific health check. Should raise exception if unhealthy."""
        pass


class RetryableOperationMixin:
    """
    Deprecated mixin providing retry functionality for operations.

    Use error_handler.with_retry directly for new code.
    """

    def execute_with_retry(self, operation_func, operation_name: str, max_attempts: int = 3, **retry_kwargs):
        """
        Execute operation with retry logic.

        Args:
            operation_func: Function to execute
            operation_name: Name for logging
            max_attempts: Maximum retry attempts
            **retry_kwargs: Additional retry configuration

        Returns:
            Result of operation_func
        """
        warnings.warn(
            "RetryableOperationMixin is deprecated; use nui_shared_utils.with_retry directly instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from .error_handler import with_retry

        # Apply retry decorator dynamically
        retried_operation = with_retry(max_attempts=max_attempts, **retry_kwargs)(operation_func)

        executor = getattr(self, "_execute_with_error_handling", None)
        if executor is None:
            raise AttributeError(
                f"{self.__class__.__name__} must implement _execute_with_error_handling "
                "to use RetryableOperationMixin"
            )
        return executor(operation_name, retried_operation)
