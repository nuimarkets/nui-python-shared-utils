"""
Lambda context helpers for extracting environment information.

Provides standardized environment info extraction for logging and metrics context.
"""

import os
from contextvars import ContextVar
from typing import Dict, Optional, Union

__all__ = ["get_lambda_environment_info"]


class _InvocationScope:
    """Share an invocation's lifetime with copied execution contexts."""

    def __init__(self, context: object) -> None:
        self.context = context
        self.active = True


_handler_context: ContextVar[Optional[_InvocationScope]] = ContextVar("lambda_handler_context", default=None)


def get_lambda_environment_info() -> Dict[str, Union[str, bool]]:
    """
    Extract standard Lambda environment info.

    Returns a dict with Lambda runtime information useful for logging context,
    metric dimensions, and conditional behavior based on environment.

    Detection logic:
    - `is_local`: True if AWS_LAMBDA_RUNTIME_API is not set (local dev or tests)
    - `environment`: Derived from ENVIRONMENT env var, falls back to "unknown"

    Returns:
        Dict with keys:
            - environment: "prod" | "dev" | "staging" | "unknown"
            - aws_region: AWS region (e.g., "ap-southeast-2")
            - function_name: Lambda function name
            - function_version: Lambda function version (e.g., "$LATEST")
            - memory_limit: Memory limit in MB (e.g., "512")
            - is_local: True if running outside Lambda environment

    Example:
        >>> from nui_shared_utils import get_lambda_environment_info
        >>> env_info = get_lambda_environment_info()
        >>> env_info
        {
            "environment": "prod",
            "aws_region": "ap-southeast-2",
            "function_name": "my-lambda",
            "function_version": "$LATEST",
            "memory_limit": "512",
            "is_local": False
        }

    Usage with Powertools logger:
        >>> logger = get_powertools_logger("my-service")
        >>> env_info = get_lambda_environment_info()
        >>> logger.info("Starting handler", extra=env_info)

    Usage with CloudWatch metrics dimensions:
        >>> from nui_shared_utils import MetricsPublisher
        >>> env_info = get_lambda_environment_info()
        >>> metrics = MetricsPublisher(
        ...     namespace="MyService",
        ...     dimensions={
        ...         "Environment": env_info["environment"],
        ...         "FunctionName": env_info["function_name"],
        ...     },
        ... )
    """
    # Detect if running in Lambda environment
    # AWS_LAMBDA_RUNTIME_API is set by the Lambda runtime, not available locally
    is_local = os.getenv("AWS_LAMBDA_RUNTIME_API") is None

    # Environment detection
    # Support common env var patterns: ENVIRONMENT, ENV, STAGE
    environment = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or os.getenv("STAGE") or "unknown").lower()

    # Normalize common environment names
    if environment in ("production", "prd"):
        environment = "prod"
    elif environment in ("development",):
        environment = "dev"

    return {
        "environment": environment,
        "aws_region": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")),
        "function_name": os.getenv("AWS_LAMBDA_FUNCTION_NAME", ""),
        "function_version": os.getenv("AWS_LAMBDA_FUNCTION_VERSION", ""),
        "memory_limit": os.getenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", ""),
        "is_local": is_local,
    }
