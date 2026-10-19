# AWS Powertools Integration Guide

This guide covers the AWS Powertools integration utilities provided by `nui-python-shared-utils`. These utilities standardize logging, metrics, and error handling patterns across Lambda functions using [AWS Lambda Powertools for Python](https://docs.powertools.aws.dev/lambda/python/).

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Logger Setup](#logger-setup)
- [Handler Decorator](#handler-decorator)
- [Local vs Lambda Environment](#local-vs-lambda-environment)
- [Migration Guide](#migration-guide)
- [Troubleshooting](#troubleshooting)

## Installation

Install the package with Powertools support:

```bash
# Install with Powertools integration
pip install nui-python-shared-utils[powertools]

# Or install everything
pip install nui-python-shared-utils[all]
```

This installs:

- `aws-lambda-powertools>=3.6.0,<4.0.0` - AWS Lambda Powertools framework
- `coloredlogs>=15.0` - Colored logging for local development

## Quick Start

Here's a complete example using both Powertools utilities:

```python
from nui_shared_utils import get_powertools_logger, powertools_handler

# Create logger (works in both local and Lambda environments)
logger = get_powertools_logger("my-service", level="INFO")

# Decorate your Lambda handler
@powertools_handler(
    service_name="my-lambda",
    metrics_namespace="MyApp/Lambda",
    slack_alert_channel="#errors"
)
@logger.inject_lambda_context
def handler(event, context):
    """Lambda handler with logging, metrics, and error handling."""

    logger.info("Processing event", extra={"event_type": event.get("type")})

    # Your business logic here
    result = process_data(event)

    logger.info("Processing complete", extra={"result_count": len(result)})

    return {
        "statusCode": 200,
        "body": json.dumps({"results": result})
    }
```

## Logger Setup

### Function: `get_powertools_logger()`

Creates an AWS Powertools Logger with Elasticsearch-compatible formatting.

**Signature:**

```python
def get_powertools_logger(
    service_name: str,
    level: str = "INFO",
    local_dev_colors: bool = True,
) -> Union[Logger, logging.Logger]:
    """
    Create AWS Powertools Logger with Elasticsearch-compatible formatting.

    Args:
        service_name: Service identifier (e.g., "nui-tender-analyser")
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        local_dev_colors: Enable coloredlogs for local development

    Returns:
        Logger instance with inject_lambda_context decorator method
    """
```

### Basic Usage

```python
from nui_shared_utils import get_powertools_logger

# Create logger with default INFO level
logger = get_powertools_logger("my-service")

# Create logger with DEBUG level
logger = get_powertools_logger("my-service", level="DEBUG")

# Disable coloredlogs for local development
logger = get_powertools_logger("my-service", local_dev_colors=False)
```

### Structured Logging

The logger supports structured logging with the `extra` parameter. Use the shared
**[structured logging contract](structured-logging-contract.md)** so field names are
consistent and queryable across the fleet: dotted, snake_case keys via the `F`
constants (and consumer-defined domain fields like `order.*`), not ad-hoc flat keys.

```python
from nui_shared_utils import F

logger.info(
    "Order processed successfully",
    extra={
        "order.id": "ORD-12345",        # consumer domain field (order.* namespace)
        F.USER_ID: 789,
        F.RESPONSE_DURATION_MS: 123,
    },
)
```

Better still, bind request/user context once per request with the contract's binding
helpers so every log line carries it automatically. See the
[contract guide](structured-logging-contract.md) for `bind_request` / `bind_user` /
`log_exception` and the full field registry.

### Elasticsearch-Compatible Timestamps

The logger automatically formats timestamps for Elasticsearch compatibility:

**Powertools default:** `2025-01-18 04:39:27,788+0000`
**Our format:** `2025-01-18T04:39:27.788Z`

This ensures proper indexing and querying in Elasticsearch log aggregation systems.

### Using Lambda Context Injection

```python
logger = get_powertools_logger("my-service")

@logger.inject_lambda_context
def handler(event, context):
    # Automatically includes Lambda context in all logs:
    # - function_name
    # - function_version
    # - function_arn
    # - request_id
    # - cold_start
    logger.info("Event received")
    return {"statusCode": 200}
```

## Handler Decorator

### Function: `powertools_handler()`

Decorator combining logging, metrics, and error handling for Lambda handlers.

**Signature:**

```python
def powertools_handler(
    service_name: str,
    metrics_namespace: Optional[str] = None,
    slack_alert_channel: Optional[str] = None,
    slack_account_names: Optional[Dict[str, str]] = None,
    slack_account_names_config: Optional[str] = None,
    reraise_exceptions: bool = False,
):
    """
    Decorator for Lambda handlers with logging, metrics, and error handling.

    Args:
        service_name: Service identifier for logging and metrics dimensions
        metrics_namespace: CloudWatch namespace (e.g., "NUI/TenderAnalyser")
        slack_alert_channel: Slack channel for error alerts (e.g., "#alerts")
        slack_account_names: AWS account ID to display-name mapping for alerts
        slack_account_names_config: YAML file containing account-name defaults
        reraise_exceptions: Re-raise after logging and alerting. Defaults to False.
    """
```

### When the Handler Raises

The decorator logs the exception and sends the Slack alert (if configured), then:

- **SQS events** (`Records[0].eventSource == "aws:sqs"`, checked before the handler runs):
  re-raises. A returned error dict tells the event source mapping the batch succeeded (with
  `ReportBatchItemFailures`, a response without `batchItemFailures` means every record did),
  so the batch would be deleted. Re-raising leaves it on the queue for redelivery; it moves
  to a DLQ after `maxReceiveCount` only if the queue has a redrive policy. The whole batch
  comes back, records the handler already processed included, so a handler with
  `batchSize` above 1 must be idempotent or catch per record. On a FIFO queue the message
  group waits until the batch succeeds or redrives, and the Slack alert fires on every
  receive. For per-record retries, enable `ReportBatchItemFailures` on the mapping and
  return `{"batchItemFailures": [...]}` from the handler; the decorator passes it through.
- **Every other event, by default**: returns
  `{"statusCode": 500, "body": "Internal Server Error"}` to preserve existing behavior.
- **Every other event with `reraise_exceptions=True`**: re-raises after logging the
  exception and sending the configured Slack alert. Use this for Kinesis and DynamoDB
  Streams, where a raised exception prevents the checkpoint from advancing, and for
  asynchronous sources such as SNS, S3, and EventBridge, where Lambda failure handling
  depends on the invocation failing.

The option is handler-wide rather than an event-source list because the handler's trigger
configuration determines whether Lambda failure semantics are appropriate. SQS does not
require the option and continues to re-raise unconditionally.

For synchronous triggers such as API Gateway, enabling this option changes failures from
the decorator's 500 response into an unhandled Lambda invocation error. Enable it only when
that caller-visible behavior is intended. Every retry invokes the decorator again, so a
configured Slack alert can be sent once per attempt.

### Re-raise Failures for Stream and Async Sources

```python
@powertools_handler(
    service_name="event-processor",
    slack_alert_channel="#production-alerts",
    reraise_exceptions=True,
)
def handler(event, context):
    process_event(event)
```

Logging and Slack alerting run before the original exception is re-raised. Configure the
trigger's retry, bisect, failure destination, or dead-letter behavior separately in AWS.

### Basic Usage (Logging Only)

```python
from nui_shared_utils import powertools_handler

@powertools_handler(service_name="simple-lambda")
def handler(event, context):
    return {"statusCode": 200, "body": "Success"}
```

### With Metrics Publishing

```python
@powertools_handler(
    service_name="data-processor",
    metrics_namespace="MyApp/DataProcessor"
)
def handler(event, context):
    # Metrics are automatically published at end of execution
    return {"statusCode": 200}
```

### With Slack Error Alerts

```python
@powertools_handler(
    service_name="critical-service",
    slack_alert_channel="#production-alerts"
)
def handler(event, context):
    # Exceptions automatically sent to Slack
    if error_condition:
        raise ValueError("Critical error occurred")
    return {"statusCode": 200}
```

### Complete Example (All Features)

```python
from nui_shared_utils import get_powertools_logger, powertools_handler, F

logger = get_powertools_logger("order-processor")

@powertools_handler(
    service_name="order-processor",
    metrics_namespace="Ecommerce/Orders",
    slack_alert_channel="#order-alerts"
)
@logger.inject_lambda_context
def handler(event, context):
    """
    Process order events with full observability.

    Features:
    - Structured logging with Lambda context
    - CloudWatch metrics publishing
    - Slack alerts on failures
    - Proper error response formatting
    """

    logger.info("Processing order", extra={F.REQUEST_ID: event.get("id")})

    try:
        order = validate_order(event)
        result = process_order(order)

        logger.info(
            "Order processed successfully",
            extra={
                "order.id": order["id"],                 # consumer domain field
                F.RESPONSE_DURATION_MS: result["duration"],
            }
        )

        return {
            "statusCode": 200,
            "body": json.dumps({"order_id": order["id"]})
        }

    except ValidationError as e:
        logger.error("Order validation failed", extra={F.ERROR_TYPE: type(e).__name__, F.ERROR_MESSAGE: str(e)})
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid order"})
        }
```

## Local vs Lambda Environment

The utilities automatically detect the execution environment and adapt accordingly:

### Lambda Environment Detection

Environment is considered "Lambda" when:

- `AWS_LAMBDA_RUNTIME_API` environment variable is set
- AND `AWS_SAM_LOCAL` is NOT set (to support SAM local testing)

### Logger Behavior by Environment

| Feature | Local Environment | Lambda Environment |
|---------|-------------------|-------------------|
| **Logger Type** | Python `logging.Logger` | AWS Powertools `Logger` |
| **Output Format** | Human-readable text | JSON structured logs |
| **Colorization** | Coloredlogs (if available) | No colors |
| **Timestamp Format** | System default | ES-compatible ISO format |
| **Lambda Context** | Mock (no-op decorator) | Real context injection |

### Testing Locally

```python
# In your local development environment
# NO environment variables set

from nui_shared_utils import get_powertools_logger, powertools_handler

logger = get_powertools_logger("test-service")

@powertools_handler(service_name="test-service")
@logger.inject_lambda_context  # Works locally as no-op
def handler(event, context):
    logger.info("Testing locally")
    return {"statusCode": 200}

# Test the handler
if __name__ == "__main__":
    from unittest.mock import MagicMock

    mock_context = MagicMock()
    mock_context.function_name = "test-function"

    result = handler({"test": "data"}, mock_context)
    print(result)
```

## Migration Guide

### From Custom Logger Wrappers

**Before:**

```python
# Old custom logger setup
import logging
import os
from aws_lambda_powertools import Logger

def get_logger(name, level="DEBUG"):
    if os.getenv("AWS_LAMBDA_RUNTIME_API") is None:
        import coloredlogs
        coloredlogs.install(level=level)
        log = logging.getLogger(name)
        log.inject_lambda_context = lambda func: func
        return log

    logger = Logger(
        level=level,
        service=name,
        datefmt="%Y-%m-%dT%H:%M:%S.%fZ",
        utc=True
    )
    return logger

logger = get_logger("my-service")
```

**After:**

```python
# New simplified approach
from nui_shared_utils import get_powertools_logger

logger = get_powertools_logger("my-service")
```

### From Manual Error Handling

**Before:**

```python
def handler(event, context):
    try:
        result = process_event(event)
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as e:
        logger.exception(f"Handler failed: {str(e)}")

        # Manual Slack notification
        try:
            slack_client = SlackClient()
            slack_client.send_message(
                channel="#errors",
                text=f"Lambda Error: {str(e)}"
            )
        except:
            pass

        return {"statusCode": 500, "body": "Internal Server Error"}
```

**After:**

```python
from nui_shared_utils import powertools_handler

@powertools_handler(
    service_name="my-service",
    slack_alert_channel="#errors"
)
def handler(event, context):
    result = process_event(event)
    return {"statusCode": 200, "body": json.dumps(result)}
```

### From Manual Metrics Decorators

**Before:**

```python
from aws_lambda_powertools import Logger, Metrics

logger = Logger(service="my-service")
metrics = Metrics(namespace="MyApp", service="my-service")

@logger.inject_lambda_context
@metrics.log_metrics
def handler(event, context):
    return {"statusCode": 200}
```

**After:**

```python
from nui_shared_utils import get_powertools_logger, powertools_handler

logger = get_powertools_logger("my-service")

@powertools_handler(
    service_name="my-service",
    metrics_namespace="MyApp"
)
@logger.inject_lambda_context
def handler(event, context):
    return {"statusCode": 200}
```

## Troubleshooting

### ImportError: aws-lambda-powertools is required

**Problem:** Running in Lambda environment without Powertools installed.

**Solution:**

```bash
# Add to requirements.txt
nui-python-shared-utils[powertools]

# Or install directly
pip install aws-lambda-powertools>=3.6.0
```

### Logs not appearing in CloudWatch

**Problem:** Logger not properly configured or context not injected.

**Checklist:**

- ✓ Using `@logger.inject_lambda_context` decorator
- ✓ Handler returns proper dict with statusCode
- ✓ CloudWatch log group exists for Lambda function
- ✓ Lambda execution role has `logs:CreateLogGroup` and `logs:PutLogEvents` permissions

### Slack alerts not working

**Problem:** Slack client initialization or message sending failures.

**Possible Causes:**

1. **Slack credentials not configured**
   - Ensure `SLACK_CREDENTIALS_SECRET` environment variable is set
   - Verify secret exists in AWS Secrets Manager

2. **Slack client not installed**

   ```bash
   pip install nui-python-shared-utils[slack]
   ```

3. **Invalid channel name**
   - Use `#channel-name` format (with `#` prefix)
   - Verify bot has access to the channel

**Graceful Degradation:** The decorator will log warnings but continue execution if Slack fails.

### Metrics not publishing to CloudWatch

**Problem:** Metrics not appearing in CloudWatch.

**Checklist:**

- ✓ `metrics_namespace` parameter provided to decorator
- ✓ Lambda execution role has `cloudwatch:PutMetricData` permission
- ✓ Using `@metrics.log_metrics` pattern correctly
- ✓ Check CloudWatch Metrics console (5-15 min delay is normal)

### Local development shows JSON logs instead of colored output

**Problem:** Getting JSON-formatted logs locally instead of human-readable output.

**Cause:** `AWS_LAMBDA_RUNTIME_API` environment variable is set locally.

**Solution:**

```bash
# Unset the environment variable
unset AWS_LAMBDA_RUNTIME_API

# Or disable in your terminal profile
```

### Type hints not working

**Problem:** IDE not recognizing Logger type hints.

**Solution:**

```python
# Add explicit type hints
from aws_lambda_powertools import Logger as PowertoolsLogger
from logging import Logger as PythonLogger
from typing import Union

logger: Union[PowertoolsLogger, PythonLogger] = get_powertools_logger("my-service")
```

## Best Practices

### 1. Use Structured Logging

Use the [structured logging contract](structured-logging-contract.md)'s dotted field
names (via `F` and consumer domain fields), not ad-hoc flat keys:

```python
from nui_shared_utils import F

# ✓ Good - Searchable, filterable, consistent field names
logger.info("Order created", extra={
    "order.id": order.id,       # consumer domain field
    F.USER_ID: user.id,
    F.RESPONSE_STATUS: 200,
})

# ✗ Bad - Unstructured string
logger.info(f"Order {order.id} created for user {user.id} amount {order.total}")
```

### 2. Set Appropriate Log Levels

```python
# Development
logger = get_powertools_logger("my-service", level="DEBUG")

# Production
logger = get_powertools_logger("my-service", level="INFO")

# Use environment variables
import os
log_level = os.getenv("LOG_LEVEL", "INFO")
logger = get_powertools_logger("my-service", level=log_level)
```

### 3. Combine with Existing Slack Integration

```python
from nui_shared_utils import get_powertools_logger, powertools_handler, SlackClient

logger = get_powertools_logger("my-service")

@powertools_handler(
    service_name="my-service",
    slack_alert_channel="#critical-errors"  # Errors only
)
@logger.inject_lambda_context
def handler(event, context):
    # Use SlackClient for business notifications
    slack = SlackClient()
    slack.send_message(
        channel="#business-events",
        text="Daily report ready"
    )

    return {"statusCode": 200}
```

### 4. Graceful Error Handling

```python
from nui_shared_utils import F

@powertools_handler(service_name="my-service", slack_alert_channel="#errors")
@logger.inject_lambda_context
def handler(event, context):
    try:
        # Business logic
        result = process_data(event)
        return {"statusCode": 200, "body": json.dumps(result)}

    except ValidationError as e:
        # Expected errors - log and return 400
        logger.warning("Validation failed", extra={F.ERROR_TYPE: type(e).__name__, F.ERROR_MESSAGE: str(e)})
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}

    # Unexpected errors caught by decorator -> 500 + Slack alert
```

## Related Documentation

- [Slack Integration Guide](slack-integration.md) - Slack messaging and formatting
- [Quick Start Guide](../getting-started/quickstart.md) - Package usage patterns
- [AWS Lambda Powertools Documentation](https://docs.powertools.aws.dev/lambda/python/) - Official Powertools docs

## Support

For issues or questions:

- [GitHub Issues](https://github.com/nuimarkets/nui-python-shared-utils/issues)
- [Package Documentation](https://github.com/nuimarkets/nui-python-shared-utils)
