# Lambda Context Helpers Guide

This guide covers the Lambda context helpers provided by `nui-python-shared-utils`. These utilities provide standardized environment info extraction for logging context, metric dimensions, and conditional behavior based on execution environment.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Function Reference](#function-reference)
- [Usage Patterns](#usage-patterns)
- [Environment Detection](#environment-detection)
- [Related Documentation](#related-documentation)

## Installation

No additional dependencies required. Lambda helpers use only Python standard library:

```bash
# Base package includes lambda_helpers
pip install nui-python-shared-utils
```

## Quick Start

```python
from nui_shared_utils import get_lambda_environment_info

def handler(event, context):
    env_info = get_lambda_environment_info()

    # Use for conditional behavior
    if env_info["is_local"]:
        print("Running locally")
    else:
        print(f"Running in Lambda: {env_info['function_name']}")

    return {"statusCode": 200}
```

## Function Reference

### `get_lambda_environment_info()`

Extracts standard Lambda environment info from environment variables.

**Signature:**

```python
def get_lambda_environment_info() -> Dict[str, str | bool]:
    """
    Extract standard Lambda environment info.

    Returns:
        Dict with keys:
            - environment: "prod" | "dev" | "staging" | "unknown"
            - aws_region: AWS region (e.g., "ap-southeast-2")
            - function_name: Lambda function name
            - function_version: Lambda function version (e.g., "$LATEST")
            - memory_limit: Memory limit in MB (e.g., "512")
            - is_local: True if running outside Lambda environment
    """
```

**Return Value Example:**

```python
{
    "environment": "prod",
    "aws_region": "ap-southeast-2",
    "function_name": "order-processor",
    "function_version": "$LATEST",
    "memory_limit": "512",
    "is_local": False
}
```

**Environment Variable Sources:**

| Return Key | Primary Env Var | Fallback | Default |
|------------|-----------------|----------|---------|
| `environment` | `ENVIRONMENT` | `ENV`, `STAGE` | `"unknown"` |
| `aws_region` | `AWS_REGION` | `AWS_DEFAULT_REGION` | `""` |
| `function_name` | `AWS_LAMBDA_FUNCTION_NAME` | - | `""` |
| `function_version` | `AWS_LAMBDA_FUNCTION_VERSION` | - | `""` |
| `memory_limit` | `AWS_LAMBDA_FUNCTION_MEMORY_SIZE` | - | `""` |
| `is_local` | (absence of `AWS_LAMBDA_RUNTIME_API`) | - | `True` |

**Environment Normalization:**

The `environment` value is normalized for consistency:

- `"production"`, `"prd"` → `"prod"`
- `"development"` → `"dev"`
- All values are lowercased

## Usage Patterns

### With Powertools Logger

Add environment context to structured logs:

```python
from nui_shared_utils import get_powertools_logger, get_lambda_environment_info

logger = get_powertools_logger("my-service")

def handler(event, context):
    env_info = get_lambda_environment_info()

    logger.info(
        "Processing event",
        extra={
            **env_info,
            "event_type": event.get("type")
        }
    )

    return {"statusCode": 200}
```

### With CloudWatch Metrics

Add standard dimensions to metrics:

```python
from nui_shared_utils import MetricsPublisher, get_lambda_environment_info

def handler(event, context):
    env_info = get_lambda_environment_info()

    with MetricsPublisher(
        namespace="MyService",
        dimensions={
            "Environment": env_info["environment"],
            "FunctionName": env_info["function_name"],
            "Region": env_info["aws_region"],
        },
    ) as metrics:
        # Publish metrics with consistent dimensions and flush on context exit
        metrics.put_metric("ProcessedEvents", 1, "Count")

    return {"statusCode": 200}
```

### Conditional Behavior

Execute different code paths based on environment:

```python
from nui_shared_utils import get_lambda_environment_info

def handler(event, context):
    env_info = get_lambda_environment_info()

    # Use different configuration for local vs Lambda
    if env_info["is_local"]:
        config = load_local_config()
    else:
        config = load_lambda_config()

    # Environment-specific behavior
    if env_info["environment"] == "prod":
        send_to_production_queue(event)
    else:
        send_to_dev_queue(event)

    return {"statusCode": 200}
```

### Error Context Enhancement

Include environment info in error reports:

```python
from nui_shared_utils import (
    get_powertools_logger,
    get_lambda_environment_info,
    SlackClient
)

logger = get_powertools_logger("my-service")

def handler(event, context):
    env_info = get_lambda_environment_info()

    try:
        process_event(event)
    except Exception as e:
        logger.exception(
            "Handler failed",
            extra={
                **env_info,
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )

        # Include environment context in Slack alerts
        slack = SlackClient()
        slack.send_message(
            channel="#errors",
            text=(
                f"*Error in {env_info['function_name']}*\n"
                f"Environment: {env_info['environment']}\n"
                f"Region: {env_info['aws_region']}\n"
                f"Error: {str(e)}"
            )
        )
        raise

    return {"statusCode": 200}
```

## Environment Detection

### How `is_local` Works

The `is_local` flag detects whether code is running in a Lambda environment:

```python
# Detection logic
is_local = os.getenv("AWS_LAMBDA_RUNTIME_API") is None
```

**Detection Rules:**

| `AWS_LAMBDA_RUNTIME_API` | Result | Scenario |
|--------------------------|--------|----------|
| Not set | `is_local = True` | Local dev, unit tests |
| Set to any value | `is_local = False` | Lambda runtime |

**Note:** SAM Local sets `AWS_LAMBDA_RUNTIME_API` during `sam local invoke`. Use `AWS_SAM_LOCAL` to detect SAM specifically if needed.

### Testing Considerations

For unit tests, mock environment variables:

```python
import pytest

def test_handler_local_behavior(monkeypatch):
    """Test handler behavior when running locally"""
    monkeypatch.delenv("AWS_LAMBDA_RUNTIME_API", raising=False)

    from nui_shared_utils import get_lambda_environment_info

    env_info = get_lambda_environment_info()
    assert env_info["is_local"] is True


def test_handler_lambda_behavior(monkeypatch):
    """Test handler behavior when running in Lambda"""
    monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "127.0.0.1:9001")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "my-function")

    from nui_shared_utils import get_lambda_environment_info

    env_info = get_lambda_environment_info()
    assert env_info["is_local"] is False
    assert env_info["environment"] == "prod"
    assert env_info["function_name"] == "my-function"
```

## Related Documentation

- [Powertools Integration Guide](powertools-integration.md) - Logger and handler decorators
- [Quick Start Guide](../getting-started/quickstart.md) - Package usage patterns
- [CloudWatch Metrics](../getting-started/quickstart.md#5-cloudwatch-metrics) - Metrics publishing

## Support

For issues or questions:

- [GitHub Issues](https://github.com/nuimarkets/nui-python-shared-utils/issues)
- [Package Documentation](https://github.com/nuimarkets/nui-python-shared-utils)
