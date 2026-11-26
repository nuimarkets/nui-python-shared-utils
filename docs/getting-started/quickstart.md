# Quick Start Guide

Get up and running with `nui-python-shared-utils` in minutes.

## Installation

```bash
# Install with all integrations
pip install nui-python-shared-utils[all]
```

## Basic Configuration

```python
import nui_shared_utils as nui

# Configure the package
nui.configure(
    es_host="your-elasticsearch:9200",
    es_credentials_secret="your-es-secret",
    db_credentials_secret="your-db-secret",
    slack_credentials_secret="your-slack-secret",
    aws_region="us-east-1"
)
```

## Common Usage Patterns

### 1. Simple Lambda Handler with Error Handling

```python
import nui_shared_utils as nui

# Configure once at module level
nui.configure(
    slack_credentials_secret="prod/slack-token",
    aws_region="us-east-1"
)

@nui.with_retry(max_attempts=3)
def process_event(event):
    """Process an event, retrying failures before formatting the response."""
    slack = nui.SlackClient()
    
    # Process your data
    processed_count = len(event.get('records', []))
    
    # Send notification
    slack.send_message(
        channel="#alerts",
        text=f"Processed {processed_count} records successfully"
    )
    
    return {"statusCode": 200, "processed": processed_count}

def lambda_handler(event, context):
    try:
        return process_event(event)
    except Exception as error:
        return nui.handle_lambda_error(error, {
            "function_name": context.function_name,
            "aws_request_id": context.aws_request_id,
        })
```

`handle_lambda_error(error, context_dict)` returns the standard error response.
Pass a dictionary with the Lambda context attributes shown above. Keep the retry
operation inside the handler's `try` so exhausted retries reach the error helper.

Returning the error response suits a handler behind API Gateway or a function URL,
where the response is how the caller learns of the failure. For SQS, EventBridge
or asynchronous invocations, re-raise instead: a returned response counts as a
successful invocation, so Lambda neither retries the event nor sends it to a
dead-letter queue.

### 2. Database Operations

```python
import logging
import pymysql
import nui_shared_utils as nui

nui.configure(db_credentials_secret="prod/database")

def get_user_data(user_id: int):
    """Return matching rows; let the caller handle database failures."""
    db = nui.DatabaseClient(connect_timeout=2, read_timeout=4)
    try:
        rows = db.query("SELECT * FROM users WHERE id = %s", (user_id,))
    except pymysql.MySQLError:
        logging.exception("User lookup failed")
        raise  # Allow the invocation or caller's retry policy to handle the failure.
    if not rows:
        return None  # A successful SELECT found no matching user.
    return rows[0]
```

**Migration:** both `DatabaseClient.query()` and `PostgreSQLClient.query()` raise the
original connection, execution or fetch exception after logging it. Add explicit error
handling where a caller previously relied on failures returning `[]`; catch
`psycopg2.Error` for PostgreSQL driver errors. A successful MySQL query preserves the
PyMySQL result (including `()` for no rows); PostgreSQL returns a list (`[]` for no rows).
Use `if not rows` to test successful empty results across both clients.

Timeouts limit connection attempts or socket reads, not the total query or Lambda
invocation. See [database timeout settings](configuration.md#database-timeouts) for
supported values and PostgreSQL's connection-only limit.

### 3. Elasticsearch Search

```python
import nui_shared_utils as nui

# Configure Elasticsearch
nui.configure(
    es_host="your-cluster:9200",
    es_credentials_secret="prod/elasticsearch"
)

def search_logs(service: str, hours: int = 24):
    """Search application logs."""
    es = nui.ElasticsearchClient()
    
    # Use query builder for complex searches
    query = nui.ElasticsearchQueryBuilder()
    
    results = es.search(
        index=f"logs-{service}-*",
        body=query.time_range(hours=hours)
                  .match("level", "ERROR")
                  .build()
    )
    
    return results['hits']['hits']
```

### 4. Rich Slack Messages

```python
import nui_shared_utils as nui

def send_report(data: dict):
    """Send a formatted report to Slack."""
    slack = nui.SlackClient()
    builder = nui.SlackBlockBuilder()
    
    # Build rich message blocks
    blocks = (builder
        .add_header("Daily Report", emoji="📊")
        .add_context(f"Generated at {nui.format_nz_time()}")
        .add_divider()
        .add_fields([
            ("Total Records", nui.format_number(data['total'])),
            ("Processed", nui.format_number(data['processed'])),
            ("Success Rate", nui.format_percentage(data['success_rate']))
        ])
        .build()
    )
    
    slack.send_message(channel="#reports", blocks=blocks)
```

### 5. CloudWatch Metrics

```python
import nui_shared_utils as nui

def track_business_metrics(records_processed: int, processing_time: float) -> bool:
    """Publish custom metrics and return whether every batch succeeds."""
    metrics = nui.MetricsPublisher(
        namespace="MyApplication",
        dimensions={"Environment": "production"}
    )
    
    # Publish metrics
    metrics.put_metric("RecordsProcessed", records_processed, unit="Count")
    metrics.put_metric("ProcessingTime", processing_time, unit="Milliseconds")
    
    return metrics.flush()
```

Call `flush()` explicitly before returning from a Lambda invocation. A
`with nui.MetricsPublisher(...) as metrics:` block also flushes on exit, but does
not raise when `flush()` returns `False`; use an explicit call when the caller
needs to handle that result. Object destruction does not flush metrics.

`flush()` returns `True` when all buffered batches succeed, including an empty
buffer, and `False` on a CloudWatch `ClientError`. Each successful request removes
its batch immediately. A failed request leaves its batch and unattempted samples
buffered in order, so a later `flush()` retries only those samples. Other exceptions
propagate. If CloudWatch accepts a request but its response is lost, retrying that
batch can duplicate samples; the publisher does not provide exactly-once delivery
or thread safety.

### 6. Complete Lambda Function Example

```python
import os
from datetime import datetime, timezone
import nui_shared_utils as nui

# Configure once at module level
nui.configure(
    es_host=os.environ.get('ES_HOST'),
    es_credentials_secret=f"{os.environ.get('STAGE', 'dev')}/elasticsearch",
    db_credentials_secret=f"{os.environ.get('STAGE', 'dev')}/database",
    slack_credentials_secret=f"{os.environ.get('STAGE', 'dev')}/slack",
    aws_region=os.environ.get('AWS_REGION', 'us-east-1')
)

@nui.track_lambda_performance()
def process_records(event, context):
    """Complete example with all integrations; raises on failure."""
    # Initialize clients
    db = nui.DatabaseClient()
    es = nui.ElasticsearchClient()
    slack = nui.SlackClient()

    # Process records
    records = event.get('records', [])
    processed = 0

    for record in records:
        # Database operation
        with db.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO processed_records (data) VALUES (%s)",
                    (record,)
                )

        processed += 1

    # Log to Elasticsearch
    indexed, failed = es.streaming_bulk(iter([{
        "_index": "application-logs",
        "_source": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "message": f"Processed {processed} records",
            "function_name": context.function_name
        }
    }]), raise_on_error=True)
    if indexed != 1 or failed:
        raise RuntimeError("Application log indexing failed")

    # Send notification
    slack.send_message(
        channel="#notifications",
        text=f"Successfully processed {processed} records"
    )

    # Custom metrics, flushed before returning
    with nui.MetricsPublisher("MyService") as metrics:
        metrics.put_metric("RecordsProcessed", processed, unit="Count")

    return {
        "statusCode": 200,
        "body": f"Processed {processed} records"
    }

def lambda_handler(event, context):
    try:
        return process_records(event, context)
    except Exception as error:
        return nui.handle_lambda_error(error, {
            "function_name": context.function_name,
            "aws_request_id": context.aws_request_id,
        })
```

`track_lambda_performance()` counts an invocation as an error only when the
function it wraps raises. Apply it to the function that does the work and catch
outside it, as above; a `try` inside the decorated function hides every failure
from the `LambdaErrors` metric.

### 7. JWT Authentication for API Gateway

```python
import nui_shared_utils as nui

def lambda_handler(event, context):
    """API Gateway Lambda with JWT auth."""
    # Validate Bearer token from Authorization header
    # Fetches public key from Secrets Manager (cached automatically)
    try:
        claims = nui.require_auth(event)
    except nui.AuthenticationError:
        return {"statusCode": 401, "body": "Unauthorized"}

    # Access token claims
    user_id = claims["sub"]
    role = claims.get("role", "user")

    return {"statusCode": 200, "body": f"Hello {user_id} ({role})"}
```

**Installation:**

```bash
pip install nui-python-shared-utils[jwt]
```

**Requirements:**

- `JWT_PUBLIC_KEY_SECRET` env var pointing to Secrets Manager secret
- Secret contains `{"TOKEN_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\n..."}` (PKCS#8 PEM)
- IAM permission: `secretsmanager:GetSecretValue`

**See:** [JWT Authentication Guide](../guides/jwt-authentication.md) for key management and advanced usage.

### 8. AWS Powertools Integration

For production Lambda functions, use AWS Powertools for standardized logging, metrics, and error handling:

```python
from nui_shared_utils import get_powertools_logger, powertools_handler

# Create logger with ES-compatible formatting
logger = get_powertools_logger("order-processor", level="INFO")

@powertools_handler(
    service_name="order-processor",
    metrics_namespace="Ecommerce/Orders",
    slack_alert_channel="#production-alerts"
)
@logger.inject_lambda_context
def lambda_handler(event, context):
    """
    Lambda handler with:
    - Structured JSON logging
    - Automatic CloudWatch metrics
    - Slack error alerting
    - Proper error responses
    """

    logger.info("Processing order", extra={"order_id": event.get("id")})

    # Your business logic
    order = process_order(event)

    logger.info(
        "Order completed",
        extra={
            "order_id": order["id"],
            "total": order["total"],
            "processing_time_ms": order["duration"]
        }
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"order_id": order["id"]})
    }
```

**Installation:**

```bash
pip install nui-python-shared-utils[powertools]
```

**Benefits:**

- ✅ Elasticsearch-compatible timestamp format (`2025-01-18T04:39:27.788Z`)
- ✅ Automatic Lambda context injection (function name, request ID, etc.)
- ✅ Local development with colored logs
- ✅ CloudWatch metrics publishing
- ✅ Slack error alerts with graceful degradation
- ✅ Proper Lambda error responses

**See:** [Powertools Integration Guide](../guides/powertools-integration.md) for complete documentation.

## Next Steps

1. **Read the Configuration Guide** - [configuration.md](configuration.md) for detailed setup
2. **Explore Components** - Check individual component docs for advanced usage
3. **Set up AWS Resources** - Review [aws-infrastructure.md](aws-infrastructure.md) for required resources
4. **Testing** - See [testing.md](testing.md) for testing strategies

## Common Patterns

### Environment-Based Configuration

```python
import os
import nui_shared_utils as nui

stage = os.environ.get('STAGE', 'dev')

nui.configure(
    es_credentials_secret=f"{stage}/elasticsearch",
    db_credentials_secret=f"{stage}/database",
    slack_credentials_secret=f"{stage}/slack"
)
```

### Error Notifications

```python
import nui_shared_utils as nui

def lambda_handler(event, context):
    try:
        # Your logic here
        pass
    except Exception as e:
        # Send error notification
        slack = nui.SlackClient()
        slack.send_message(
            channel="#alerts",
            text=f"❌ Lambda error in {context.function_name}: {str(e)}"
        )
        # Re-raise so Lambda records the failure; an API Gateway handler can
        # return nui.handle_lambda_error(e, {...}) instead (see section 1)
        raise
```

### Retry with Backoff

```python
@nui.with_retry(max_attempts=3, backoff_factor=2)
def external_api_call():
    """Call with exponential backoff retry."""
    # API call logic here
    pass
```

This quick start should get you productive with the package immediately. For detailed documentation on each component, see the specific guides linked in the main documentation.
