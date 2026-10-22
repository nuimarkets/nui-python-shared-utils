# Shared Types & Data Structures

This document describes the core types, interfaces, and data structures provided by `nui-python-shared-utils`.

## Configuration Types

### Config

Central configuration class for environment-based settings.

```python
from nui_shared_utils import Config, configure

# Configuration fields
Config(
    es_host: str                     # Elasticsearch host (default: localhost:9200)
    es_credentials_secret: str       # AWS secret name for ES credentials
    db_credentials_secret: str       # AWS secret name for database credentials
    slack_credentials_secret: str    # AWS secret name for Slack credentials
    aws_region: str                  # AWS region (default: us-east-1)
)

# Environment variable precedence
# ES_HOST or ELASTICSEARCH_HOST → es_host
# ES_CREDENTIALS_SECRET or ELASTICSEARCH_CREDENTIALS_SECRET → es_credentials_secret
# DB_CREDENTIALS_SECRET or DATABASE_CREDENTIALS_SECRET → db_credentials_secret
# SLACK_CREDENTIALS_SECRET → slack_credentials_secret
# AWS_REGION or AWS_DEFAULT_REGION → aws_region
```

## Client Base Classes

### BaseClient (ABC)

Abstract base class providing standardized patterns for all service clients.

```python
from nui_shared_utils.base_client import BaseClient

class BaseClient(ABC):
    config: Config                    # Global configuration instance
    config_key_prefix: str            # Prefix for config keys (e.g., 'slack', 'es', 'db')
    credentials: Dict[str, Any]       # Resolved credentials from AWS Secrets Manager
    client_config: Dict[str, Any]     # Additional client-specific configuration
    _service_client: Any              # Underlying service client instance

    # Abstract methods (must implement in subclasses)
    def _get_default_config_prefix(self) -> str: ...
    def _create_service_client(self) -> Any: ...
    def _get_default_secret_name(self) -> str: ...
```

### ServiceHealthMixin

Mixin providing standardized health check functionality.

```python
# Health check response structure
{
    "status": "healthy" | "unhealthy",
    "client_type": str,               # Class name
    "error": str,                     # Only if unhealthy
    "error_type": str,                # Only if unhealthy
    "timestamp": float | None
}
```

### RetryableOperationMixin

Mixin providing retry functionality for operations.

```python
execute_with_retry(
    operation_func: Callable,
    operation_name: str,
    max_attempts: int = 3,
    **retry_kwargs
) -> Any
```

## Database Types

### DatabaseClient

MySQL client with connection pooling.

```python
from nui_shared_utils import DatabaseClient

client = DatabaseClient(
    secret_name: str = None,          # Override secret name
    use_pool: bool = True,            # Enable connection pooling
    pool_size: int = 5,               # Maximum pooled connections
    pool_recycle: int = 3600          # Recycle connections after seconds
)

# Methods
client.query(sql, params=None, database=None) -> List[Dict]
client.execute(sql, params=None, database=None) -> int  # affected rows
client.bulk_insert(table, records, database=None, batch_size=1000, ignore_duplicates=False) -> int
client.health_check() -> Dict
client.get_connection_info() -> Dict
```

**Connection Pool Entry Structure:**

```python
{
    "connection": pymysql.Connection,
    "timestamp": float                # time.time() when added to pool
}
```

**Connection Info Response:**

```python
{
    "host": str,
    "port": int,
    "database": str,
    "username": str,
    "pool_enabled": bool,
    "pool_size": int,
    "pool_recycle_seconds": int
}
```

### PostgreSQLClient

PostgreSQL client with connection management.

```python
from nui_shared_utils import PostgreSQLClient

client = PostgreSQLClient(
    secret_name: str = None,
    use_auth_credentials: bool = True  # Use auth-specific credentials from secret
)

# Methods
client.query(sql, params=None, database=None) -> List[Dict]
client.health_check() -> Dict
```

## Elasticsearch Types

### ElasticsearchClient

Elasticsearch client with standardized query patterns.

```python
from nui_shared_utils import ElasticsearchClient

client = ElasticsearchClient(
    host: str = None,                 # Override ES host
    secret_name: str = None,          # Override secret name
    scheme: str = "http",             # URL scheme
    request_timeout: int = 30,
    max_retries: int = 3,
    retry_on_timeout: bool = True
)

# Methods
client.search(index, body, size=100) -> List[Dict]        # Returns _source documents
client.aggregate(index, body) -> Dict[str, Any]           # Returns aggregations
client.count(index, body=None) -> int
client.get_service_stats(service, hours=24, index_prefix="logs") -> Dict
client.get_recent_errors(service, hours=1, limit=10, index_prefix="logs") -> List[Dict]
client.get_cluster_info() -> Dict
client.get_indices_info(pattern="*") -> List[Dict]
```

**Service Stats Response:**

```python
{
    "service": str,
    "time_window_hours": int,
    "total_count": int,
    "error_count": int,
    "error_rate": float,              # Percentage
    "p50_response_time": float,
    "p95_response_time": float,
    "p99_response_time": float
}
```

**Cluster Info Response:**

```python
{
    "version": str,
    "cluster_name": str,
    "cluster_status": "green" | "yellow" | "red",
    "number_of_nodes": int,
    "number_of_data_nodes": int,
    "active_primary_shards": int,
    "active_shards": int
}
```

## Slack Types

### SlackClient

Slack messaging client with Lambda context integration.

```python
from nui_shared_utils import SlackClient

client = SlackClient(
    secret_name: str = None,
    account_names: Dict[str, str] = None,      # AWS account ID → display name
    account_names_config: str = None,          # Path to YAML config
    service_name: str = None                   # Display name for messages
)

# Methods
client.send_message(channel, text, blocks=None, include_lambda_header=True, event_type=None) -> bool
client.send_file(channel, content, filename, title=None) -> bool
client.send_thread_reply(channel, thread_ts, text, blocks=None, include_lambda_header=False, event_type=None) -> bool
client.update_message(channel, ts, text, blocks=None) -> bool
client.add_reaction(channel, ts, emoji) -> bool
client.health_check() -> Dict
client.get_bot_info() -> Dict
```

**Lambda Context Structure:**

```python
{
    "function_name": str,
    "function_version": str,
    "log_group": str,
    "log_stream": str,
    "aws_region": str,
    "stage": str,
    "execution_env": str,
    "aws_account_id": str,
    "aws_account_arn": str,
    "aws_account_name": str,
    "deploy_time": str,               # Human-readable age (e.g., "2h ago")
    "deploy_config_type": str         # "lambda-deploy v3.0+" | "serverless.yml" | "Unknown"
}
```

**Bot Info Response:**

```python
{
    "bot_id": str,
    "user_id": str,
    "team": str,
    "team_id": str,
    "url": str
}
```

## Error Handling Types

### Error Classes

```python
from nui_shared_utils.error_handler import RetryableError, NonRetryableError

# Exceptions for retry control
raise RetryableError("Temporary failure")      # Will trigger retry
raise NonRetryableError("Permanent failure")   # Will NOT trigger retry
```

### ErrorPatternMatcher

Pattern-based error categorization.

```python
from nui_shared_utils.error_handler import ErrorPatternMatcher

matcher = ErrorPatternMatcher()
result = matcher.categorize_error(exception)
```

**Categorized Error Response:**

```python
{
    "error": str,
    "category": str,                  # "network" | "database" | "authentication" | etc.
    "severity": str,                  # "critical" | "warning" | "info"
    "description": str,
    "pattern_matched": str | None,
    "is_retryable": bool
}
```

**Built-in Error Categories:**

- `data_format` - JSON parsing errors
- `authentication` - Auth failures
- `database` - DB timeouts
- `network` - Connection refused
- `rate_limit` - API rate limits
- `not_found` - Resource not found
- `authorization` - Permission denied
- `elasticsearch` - ES timeouts
- `resource` - Memory errors
- `security` - SSL/TLS errors

### ErrorAggregator

Batch error collection for reporting.

```python
from nui_shared_utils.error_handler import ErrorAggregator

aggregator = ErrorAggregator(max_errors=100)
aggregator.add_error(exception, context={"key": "value"})
summary = aggregator.get_summary()
```

**Error Summary Response:**

```python
{
    "total_errors": int,
    "by_category": Dict[str, int],    # category → count
    "by_severity": Dict[str, int],    # severity → count
    "recent_errors": List[Dict]       # Last 5 errors
}
```

### Retry Decorator

```python
from nui_shared_utils import with_retry

@with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
    non_retryable_exceptions: tuple = (NonRetryableError,),
    on_retry: Callable = None         # callback(func_name, attempt, error, delay)
)
def my_function(): ...
```

## Metrics Types

### MetricsPublisher

CloudWatch metrics publisher with batching.

```python
from nui_shared_utils import MetricsPublisher

publisher = MetricsPublisher(
    namespace: str,
    dimensions: Dict[str, str] = None,
    auto_flush_size: int = 20,
    region: str = None
)

# Methods
publisher.put_metric(metric_name, value, unit="None", timestamp=None, dimensions=None, storage_resolution=60)
publisher.put_metric_with_statistics(metric_name, values: List, unit="None", timestamp=None, dimensions=None)
publisher.flush() -> bool
```

**Metric Data Structure (internal buffer):**

```python
{
    "MetricName": str,
    "Value": float,
    "Unit": str,
    "Timestamp": datetime,
    "StorageResolution": int,         # 1 (high-res) or 60 (standard)
    "Dimensions": List[{"Name": str, "Value": str}]
}
```

### StandardMetrics

Predefined metric name constants.

```python
from nui_shared_utils import StandardMetrics

# Service health
StandardMetrics.SERVICE_HEALTH
StandardMetrics.ERROR_RATE
StandardMetrics.RESPONSE_TIME
StandardMetrics.REQUEST_COUNT

# Business
StandardMetrics.RECORDS_CREATED
StandardMetrics.RECORDS_PROCESSED
StandardMetrics.USERS_ACTIVE
StandardMetrics.REVENUE_PROCESSED

# Lambda
StandardMetrics.LAMBDA_DURATION
StandardMetrics.LAMBDA_ERRORS
StandardMetrics.LAMBDA_THROTTLES
StandardMetrics.LAMBDA_COLD_STARTS

# Database
StandardMetrics.DB_QUERY_TIME
StandardMetrics.DB_CONNECTION_ERRORS
StandardMetrics.DB_ACTIVE_CONNECTIONS

# Elasticsearch
StandardMetrics.ES_QUERY_TIME
StandardMetrics.ES_QUERY_ERRORS
StandardMetrics.ES_DOCUMENT_COUNT

# External API
StandardMetrics.API_CALL_DURATION
StandardMetrics.API_CALL_ERRORS
StandardMetrics.API_RATE_LIMIT_HITS
```

### TimedMetric

Context manager for timing operations.

```python
from nui_shared_utils.cloudwatch_metrics import TimedMetric

with TimedMetric(publisher, "DatabaseQuery", unit="Milliseconds", dimensions=None):
    # Operation to time
    pass
```

## Secrets Types

### Credential Structures

**Database Credentials:**

```python
{
    "host": str,
    "port": int,                      # Default: 3306
    "username": str,
    "password": str,
    "database": str                   # Default: "app"
}
```

**Elasticsearch Credentials:**

```python
{
    "host": str,                      # With port (e.g., "elastic:9200")
    "username": str,                  # Default: "elastic"
    "password": str
}
```

**Slack Credentials:**

```python
{
    "bot_token": str,
    "webhook_url": str | None         # Optional
}
```

### Secrets Helper Functions

```python
from nui_shared_utils import (
    get_secret,
    get_database_credentials,
    get_elasticsearch_credentials,
    get_slack_credentials,
    get_api_key,
    clear_cache
)

# Generic secret retrieval (cached)
get_secret(secret_name: str) -> Dict

# Typed credential retrieval with field normalization
get_database_credentials(secret_name: str = None) -> Dict
get_elasticsearch_credentials(secret_name: str = None) -> Dict
get_slack_credentials(secret_name: str = None) -> Dict

# Simple API key retrieval
get_api_key(secret_name: str, key_field: str = "api_key") -> str

# Cache management
clear_cache()  # Clear secrets cache for long-running Lambdas
```

## Connection Pool Statistics

```python
from nui_shared_utils.db_client import get_pool_stats

stats = get_pool_stats()
```

**Pool Stats Response:**

```python
{
    "total_pools": int,
    "pools": {
        "pool_key": {
            "active_connections": int,
            "healthy_connections": int,
            "aged_connections": int     # Connections older than 1 hour
        }
    }
}
```

## Lambda Error Response

```python
from nui_shared_utils.error_handler import handle_lambda_error

response = handle_lambda_error(error, context)
```

**Lambda Error Response:**

```python
{
    "statusCode": 500,
    "body": {
        "error": str,                 # Human-readable description
        "category": str,
        "request_id": str,
        "timestamp": str              # ISO format
    }
}
```
