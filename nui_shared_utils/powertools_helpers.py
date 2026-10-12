"""
AWS Powertools integration utilities for Lambda functions.

Provides standardized logging, metrics, and error handling patterns using AWS Lambda Powertools.
"""

import functools
import logging
import os
from typing import Any, Callable, Dict, Optional, Union

# Optional imports with graceful degradation
try:
    from aws_lambda_powertools import Logger, Metrics

    POWERTOOLS_AVAILABLE = True
except ImportError:
    POWERTOOLS_AVAILABLE = False
    Logger = None  # type: ignore
    Metrics = None  # type: ignore

try:
    import coloredlogs

    COLOREDLOGS_AVAILABLE = True
except ImportError:
    COLOREDLOGS_AVAILABLE = False

from .lambda_helpers import get_lambda_environment_info

# SlackClient is loaded lazily on first use to keep this module's import
# cost low for callers that don't enable Slack alerting (it transitively
# pulls in slack_sdk, which is the dominant cost).
SLACK_CLIENT_AVAILABLE = False
SlackClient = None  # type: ignore[assignment]


def _ensure_slack_client_loaded() -> None:
    """Lazy-import :class:`SlackClient` and update module-level flags.

    Idempotent: returns immediately if ``SlackClient`` has already been
    populated (real import or test mock).
    """
    global SLACK_CLIENT_AVAILABLE, SlackClient
    if SlackClient is not None:
        # Already populated (real import or test mock) — keep the availability
        # flag in sync so callers don't see a stale False.
        SLACK_CLIENT_AVAILABLE = True
        return
    try:
        from .slack_client import SlackClient as _SC

        SlackClient = _SC
        SLACK_CLIENT_AVAILABLE = True
    except ImportError:
        SLACK_CLIENT_AVAILABLE = False


__all__ = ["get_powertools_logger", "powertools_handler"]


def get_powertools_logger(
    service_name: str,
    level: str = "INFO",
    local_dev_colors: bool = True,
) -> Union[Logger, logging.Logger]:
    """
    Create AWS Powertools Logger with Elasticsearch-compatible formatting.

    Automatically detects Lambda environment and configures appropriate logging:
    - Lambda environment: AWS Powertools Logger with JSON structured logging
    - Local environment: Standard Python logger with coloredlogs (if available)

    The logger uses Elasticsearch-compatible timestamp format (%Y-%m-%dT%H:%M:%SZ)
    and enforces UTC timezone for consistency with log aggregation systems.

    Args:
        service_name: Service identifier (e.g., "nui-tender-analyser", "connect-email-ingest")
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Default: INFO
        local_dev_colors: Enable coloredlogs for local development. Default: True

    Returns:
        Logger instance with inject_lambda_context decorator method.
        - In Lambda: AWS Powertools Logger with JSON formatting
        - Locally: Python Logger with mock inject_lambda_context method

    Raises:
        ImportError: If aws-lambda-powertools is not installed when running in Lambda environment

    Example:
        >>> logger = get_powertools_logger("my-service", level="INFO")
        >>> @logger.inject_lambda_context
        ... def handler(event, context):
        ...     logger.info("Processing event", extra={"event_type": event.get("type")})
        ...     return {"statusCode": 200}
    """
    # Detect Lambda environment
    env_info = get_lambda_environment_info()
    is_sam_local = os.getenv("AWS_SAM_LOCAL") is not None

    # Local development environment (or SAM local for dev-friendly logging)
    if env_info["is_local"] or is_sam_local:
        logging.captureWarnings(True)

        # Use coloredlogs for local development if available and enabled
        if COLOREDLOGS_AVAILABLE and local_dev_colors:
            # Clear root logger handlers before coloredlogs to avoid duplicates
            logging.getLogger().handlers = []
            coloredlogs.install(level=level, isatty=True)

        # Create standard Python logger
        logger = logging.getLogger(service_name)
        logger.setLevel(level)

        # Add mock inject_lambda_context decorator for local compatibility
        # Must handle both @logger.inject_lambda_context and @logger.inject_lambda_context(log_event=False)
        def _mock_inject_lambda_context(func=None, **_kwargs):
            if func is not None:
                return func
            return lambda f: f

        logger.inject_lambda_context = _mock_inject_lambda_context  # type: ignore

        return logger

    # Lambda environment - use AWS Powertools
    if not POWERTOOLS_AVAILABLE:
        raise ImportError(
            "aws-lambda-powertools is required for Lambda environment. "
            "Install with: pip install nui-python-shared-utils[powertools]"
        )

    # Create Powertools Logger with ES-compatible timestamp format
    # Powertools default: '2025-01-18 04:39:27,788+0000'
    # Elasticsearch expects: '2025-01-18T04:39:27Z' (ISO 8601)
    # Note: %f (microseconds) is not supported by time.strftime() which logging uses internally
    powertools_logger = Logger(
        service=service_name,
        level=level,
        sampling_rate=1,
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        utc=True,
    )

    return powertools_logger


def _is_sqs_event(event: Any) -> bool:
    """True when ``event`` is a batch delivered by an SQS event source mapping."""
    if not isinstance(event, dict):
        return False
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return False
    first = records[0]
    return isinstance(first, dict) and first.get("eventSource") == "aws:sqs"


def powertools_handler(
    service_name: str,
    metrics_namespace: Optional[str] = None,
    slack_alert_channel: Optional[str] = None,
    slack_account_names: Optional[Dict[str, str]] = None,
    slack_account_names_config: Optional[str] = None,
):
    """
    Decorator for Lambda handlers with logging, metrics, and error handling.

    Combines AWS Powertools Logger and Metrics decorators with automatic exception
    handling and optional Slack alerting. Provides consistent error responses and
    structured logging for Lambda functions.

    Features:
    - Automatic logger.inject_lambda_context integration
    - Optional metrics.log_metrics integration (if metrics_namespace provided)
    - Structured exception logging with traceback
    - Optional Slack alerts on failures (if slack_alert_channel provided)
    - Graceful degradation if Slack client unavailable
    - Proper Lambda error response formatting

    Args:
        service_name: Service identifier for logging and metrics dimensions
        metrics_namespace: CloudWatch namespace for metrics (e.g., "NUI/TenderAnalyser").
                          If None, metrics publishing is disabled.
        slack_alert_channel: Slack channel for error alerts (e.g., "#alerts", "#errors").
                            If None, Slack alerting is disabled.
        slack_account_names: Dict mapping AWS account IDs to display names
        slack_account_names_config: Path to YAML file with account_names mapping.
            The file must be bundled in the Lambda deployment package/zip.
            Values loaded from this file are used as defaults, but any keys
            provided in ``slack_account_names`` take precedence and override
            the YAML-loaded values.

    Returns:
        Decorator function for Lambda handlers

    Example:
        >>> @powertools_handler(
        ...     service_name="my-lambda",
        ...     metrics_namespace="MyApp/Lambda",
        ...     slack_alert_channel="#errors"
        ... )
        ... def handler(event, context):
        ...     logger.info("Processing event")
        ...     return {"statusCode": 200, "body": "Success"}

    Example (minimal):
        >>> @powertools_handler(service_name="simple-lambda")
        ... def handler(event, context):
        ...     return {"statusCode": 200}

    Note:
        The decorated handler must return a dict with statusCode and optional body.
        On exception, returns: {"statusCode": 500, "body": "Internal Server Error"}

        SQS events are the exception: after logging and alerting, the exception is
        re-raised. Any value returned to an SQS event source mapping counts as
        success (with ``ReportBatchItemFailures``, a response without
        ``batchItemFailures`` means every record succeeded), so returning the 500
        dict would delete the batch. Re-raising leaves it on the queue for
        redelivery and, after ``maxReceiveCount``, the DLQ.
    """

    def decorator(func: Callable) -> Callable:
        # Create logger
        logger = get_powertools_logger(service_name)

        # Create metrics publisher if namespace provided
        metrics = None
        if metrics_namespace and POWERTOOLS_AVAILABLE:
            metrics = Metrics(namespace=metrics_namespace, service=service_name)

        # Create Slack client if channel provided
        slack_client = None
        if slack_alert_channel:
            _ensure_slack_client_loaded()
            if SLACK_CLIENT_AVAILABLE and SlackClient is not None:
                try:
                    slack_client = SlackClient(
                        account_names=slack_account_names,
                        account_names_config=slack_account_names_config,
                    )
                except Exception as e:
                    logger.warning("Failed to initialize Slack client: %s", e)

        @functools.wraps(func)
        def wrapper(event: dict, context: Any) -> dict:
            # Populate SlackClient account info from Lambda context ARN
            if slack_client:
                slack_client.set_handler_context(context)

            try:
                # Apply logger context injection
                # Note: inject_lambda_context is added dynamically to logging.Logger (line 95)
                # and is native to Powertools Logger. Type checker can't verify this union.
                handler_with_logging = logger.inject_lambda_context(func)  # type: ignore[union-attr, attr-defined]

                # Apply metrics if configured
                if metrics:
                    handler_with_metrics = metrics.log_metrics(handler_with_logging)
                    result = handler_with_metrics(event, context)
                else:
                    result = handler_with_logging(event, context)

                return result

            except Exception as e:
                # Log exception with full context
                logger.exception(
                    "Lambda handler failed: %s",
                    str(e),
                    extra={
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "service": service_name,
                    },
                )

                # Send Slack alert if configured
                if slack_client and slack_alert_channel:
                    try:
                        error_message = f"*Lambda Error: {service_name}*\n\n"
                        error_message += f"Error: `{type(e).__name__}: {str(e)}`\n"
                        error_message += (
                            f"Function: `{context.function_name if hasattr(context, 'function_name') else 'unknown'}`"
                        )

                        slack_client.send_message(
                            channel=slack_alert_channel,
                            text=error_message,
                        )
                    except Exception as slack_error:
                        logger.warning("Failed to send Slack alert: %s", slack_error)

                # Re-raise for SQS so the batch stays on the queue; see the Note above.
                if _is_sqs_event(event):
                    raise

                # Return proper Lambda error response
                return {
                    "statusCode": 500,
                    "body": "Internal Server Error",
                }

        return wrapper

    return decorator
