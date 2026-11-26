# Shared Types & Data Structures

This document describes the core types, interfaces, and data structures provided by `nui-python-shared-utils`.

## Configuration Types

### Config

Central configuration class for environment-based settings, imported with
`from nui_shared_utils import Config, configure`. The client and function blocks in this
guide are signature references, written as stubs; an example call follows the
signature where the call shape is not obvious from it.

```python
class Config:
    def __init__(
        self,
        es_host: Optional[str] = None,                   # Elasticsearch host (default: localhost:9200)
        es_credentials_secret: Optional[str] = None,     # AWS secret name for ES credentials
        db_credentials_secret: Optional[str] = None,     # AWS secret name for database credentials
        slack_credentials_secret: Optional[str] = None,  # AWS secret name for Slack credentials
        aws_region: Optional[str] = None,                # AWS region (default: us-east-1)
    ): ...

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

Deprecated mixin providing retry functionality for operations. Each call to
`execute_with_retry()` emits a `DeprecationWarning`. Use the public `with_retry`
decorator directly for new code. Existing calls retain their retry and error-handling
behavior unless the caller treats `DeprecationWarning` as an error; those callers must
migrate to `with_retry`, since the warning raises before the operation executes.
Deprecated in 2.7.0; it will not be removed before 3.0.

```python
class RetryableOperationMixin:
    def execute_with_retry(
        self,
        operation_func,
        operation_name: str,
        max_attempts: int = 3,
        **retry_kwargs,
    ): ...
```

## Database Types

### DatabaseClient

MySQL client with connection pooling, imported with `from nui_shared_utils import DatabaseClient`.

```python
class DatabaseClient(BaseClient, ServiceHealthMixin):
    def __init__(
        self,
        secret_name: Optional[str] = None,              # Override secret name
        use_pool: bool = True,                          # Enable connection pooling
        pool_size: int = 5,                             # Maximum pooled connections
        pool_recycle: int = 3600,                       # Discard age from creation, checked at borrow and return; <= 0 disables
        credentials: Optional[Dict[str, Any]] = None,   # Explicit credentials, skips env vars and Secrets Manager
        *,
        connect_timeout: float = 10,                    # Seconds to establish a connection
        read_timeout: Optional[float] = 30,             # Seconds per socket read; None disables the limit
        **kwargs,                                       # config_key_prefix for BaseClient; the rest is kept in client_config, with no effect
    ): ...

    def query(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> Sequence[Dict]: ...
    def execute(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> int: ...  # affected rows
    def bulk_insert(
        self,
        table: str,
        records: List[Dict],
        database: Optional[str] = None,
        batch_size: int = 1000,
        ignore_duplicates: bool = False,
    ) -> int: ...
    def health_check(self) -> Dict[str, Any]: ...
    def get_connection_info(self) -> Dict: ...
```

Example call:

```python
from nui_shared_utils import DatabaseClient

client = DatabaseClient(secret_name="prod/db-credentials", pool_size=10, read_timeout=60)
rows = client.query("SELECT id, name FROM users WHERE id = %s", (42,))
```

**Connection Pool Entry Structure:**

```python
{
    "connection": pymysql.Connection,
    "created_at": float               # time.monotonic() when the connection is created
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

PostgreSQL client with connection management, imported with `from nui_shared_utils import PostgreSQLClient`.

```python
class PostgreSQLClient(BaseClient, ServiceHealthMixin):
    def __init__(
        self,
        secret_name: Optional[str] = None,
        use_auth_credentials: bool = True,              # Use auth-specific credentials from secret
        credentials: Optional[Dict[str, Any]] = None,   # Explicit credentials, skips env vars and Secrets Manager
        *,
        connect_timeout: int = 5,                       # Integer seconds to connect, per host
        **kwargs,                                       # config_key_prefix for BaseClient; the rest is kept in client_config, with no effect
    ): ...

    def query(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None) -> List[Dict]: ...
    def health_check(self) -> Dict[str, Any]: ...
```

## Elasticsearch Types

### ElasticsearchClient

Elasticsearch client with standardized query patterns, imported with
`from nui_shared_utils import ElasticsearchClient`.

```python
class ElasticsearchClient(BaseClient, ServiceHealthMixin):
    def __init__(
        self,
        host: Optional[str] = None,                     # Override ES host
        secret_name: Optional[str] = None,              # Override secret name
        credentials: Optional[Dict[str, Any]] = None,   # Explicit credentials, skips credential env vars and
        # Secrets Manager; a "host" key still ranks below ES_HOST
        **kwargs,                                       # config_key_prefix for BaseClient; the rest is kept in
        # client_config and read with these defaults: scheme="http", request_timeout=30, max_retries=3,
        # retry_on_timeout=True
    ): ...

    def search(self, index: str, body: Dict, size: int = 100) -> List[Dict]: ...  # Returns _source documents
    def aggregate(self, index: str, body: Dict) -> Dict[str, Any]: ...  # Returns aggregations
    def count(self, index: str, body: Optional[Dict[str, Any]] = None) -> int: ...
    def get_service_stats(self, service: str, hours: int = 24, index_prefix: str = "logs") -> Dict[str, Any]: ...
    def get_recent_errors(self, service: str, hours: int = 1, limit: int = 10, index_prefix: str = "logs") -> List[Dict]: ...
    def get_cluster_info(self) -> Dict: ...
    def get_indices_info(self, pattern: str = "*") -> List[Dict]: ...
```

The full host and credential order is in
[credential resolution precedence](../getting-started/configuration.md#credential-resolution-precedence).

Example call:

```python
from nui_shared_utils import ElasticsearchClient

client = ElasticsearchClient(host="localhost:9200", request_timeout=60)
hits = client.search("logs-*", {"query": {"match": {"level": "ERROR"}}}, size=20)
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

Slack messaging client with Lambda context integration, imported with
`from nui_shared_utils import SlackClient`.

```python
class SlackClient(BaseClient, ServiceHealthMixin):
    def __init__(
        self,
        secret_name: Optional[str] = None,
        account_names: Optional[Dict[str, str]] = None,  # AWS account ID → display name
        account_names_config: Optional[str] = None,      # Path to YAML config
        service_name: Optional[str] = None,              # Display name for messages
        credentials: Optional[Dict[str, Any]] = None,    # Explicit credentials, skips env vars and Secrets Manager
        **kwargs,                                        # config_key_prefix for BaseClient; the rest is kept in client_config, with no effect
    ): ...

    def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[List[Dict]] = None,
        attachments: Optional[List[Dict]] = None,
        include_lambda_header: bool = True,
        event_type: Optional[str] = None,
        unfurl_links: Optional[bool] = None,
        unfurl_media: Optional[bool] = None,
    ) -> bool: ...
    def send_file(
        self,
        channel: str,
        content: Union[str, bytes],
        filename: str,
        title: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> bool: ...
    def send_thread_reply(
        self,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: Optional[List[Dict]] = None,
        include_lambda_header: bool = False,
        event_type: Optional[str] = None,
    ) -> bool: ...
    def update_message(self, channel: str, ts: str, text: str, blocks: Optional[List[Dict]] = None) -> bool: ...
    def add_reaction(self, channel: str, ts: str, emoji: str) -> bool: ...
    def health_check(self) -> Dict[str, Any]: ...
    def get_bot_info(self) -> Dict: ...
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
from nui_shared_utils.error_handler import NonRetryableError

@with_retry(
    max_attempts=3,
    initial_delay=1.0,
    max_delay=60.0,
    exponential_base=2.0,
    jitter=True,
    retryable_exceptions=(Exception,),
    non_retryable_exceptions=(NonRetryableError,),
    on_retry=None                     # callback(func_name, attempt, error, delay)
)
def my_function(): ...
```

## Metrics Types

### MetricsPublisher

CloudWatch metrics publisher with batching, imported with `from nui_shared_utils import MetricsPublisher`.

```python
class MetricsPublisher:
    def __init__(
        self,
        namespace: str,
        dimensions: Optional[Dict[str, str]] = None,
        auto_flush_size: int = 20,
        region: Optional[str] = None,
    ): ...

    def put_metric(
        self,
        metric_name: str,
        value: Union[int, float],
        unit: str = "None",
        timestamp: Optional[datetime] = None,
        dimensions: Optional[Dict[str, str]] = None,
        storage_resolution: int = 60,
    ) -> None: ...
    def put_metric_with_statistics(
        self,
        metric_name: str,
        values: List[Union[int, float]],
        unit: str = "None",
        timestamp: Optional[datetime] = None,
        dimensions: Optional[Dict[str, str]] = None,
    ) -> None: ...
    def flush(self) -> bool: ...
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

All six are importable from `nui_shared_utils`.

```python
# Generic secret retrieval (300-second cache, per-call freshness controls)
def get_secret(secret_name: str, *, ttl_seconds: float = 300, force_refresh: bool = False) -> Dict: ...

# Typed credential retrieval with field normalization
def get_database_credentials(secret_name: Optional[str] = None) -> Dict: ...
def get_elasticsearch_credentials(secret_name: Optional[str] = None) -> Dict: ...
def get_slack_credentials(secret_name: Optional[str] = None) -> Dict: ...

# Simple API key retrieval
def get_api_key(secret_name: str, key_field: str = "api_key") -> str: ...

# Cache management
def clear_cache() -> None: ...  # Clear cached values and retrieval ages
```

Each `get_secret` caller applies its own finite, nonnegative TTL to the entry's
actual retrieval age; `ttl_seconds=0` bypasses cached reads and `force_refresh=True`
fetches regardless of age. Refresh errors surface without serving stale values.
The cache is process-local and keyed by secret name, not AWS account or region.
Existing clients and previously fetched RSA keys retain their credentials until
recreated or retrieved again. Fresh MySQL authentication also requires a new
physical connection, for example `DatabaseClient(use_pool=False)`, since recreating
a client can reuse a pooled connection. See
[configuration](../getting-started/configuration.md#secret-cache-freshness)
for option validation and AWS-context handling.

## Connection Pool Statistics

```python
from nui_shared_utils.db_client import get_pool_stats

stats = get_pool_stats()
```

**Pool Stats Response:**

Each pool key is an opaque process-local identifier for the snapshotted connection
settings and authenticated credentials. It contains no username or password.

```python
{
    "total_pools": int,
    "pools": {
        "pool_key": {
            "active_connections": int,
            "healthy_connections": int,
            "aged_connections": int     # Connections created more than 1 hour ago
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
