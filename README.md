# NUI Python Shared Utilities

[![PyPI version](https://badge.fury.io/py/nui-python-shared-utils.svg)](https://badge.fury.io/py/nui-python-shared-utils)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Production-ready shared Python utilities for AWS Lambda functions, CLI tools, and agents. Provides standardized integrations for Slack, Elasticsearch, database, and monitoring. Built and battle-tested on the NUI platform with sensible defaults configurable for any environment.

## Table of Contents

- [Who This Package Is For](#who-this-package-is-for)
- [Key Features](#key-features)
- [Documentation](#documentation)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [Configuration](#configuration)
- [AWS Infrastructure Requirements](#aws-infrastructure-requirements)
- [Testing](#testing)
- [Contributing](#contributing)
- [Documentation & Support](#documentation--support)
- [License](#license)

## Who This Package Is For

**NUI Team**: Drop-in utilities with NUI platform defaults pre-configured. Handles common patterns like Slack notifications, Elasticsearch logging, database connections, and CloudWatch metrics out of the box.

**External Teams**: Solid AWS Lambda patterns for serverless operations. Default configurations reflect NUI conventions (Elasticsearch index patterns, AWS Secrets Manager naming, Slack workspace structure) but are fully overridable via environment variables or programmatic configuration. Consider this package as production-tested reference implementations that you can adapt to your infrastructure.

## Key Features

- **AWS Powertools Integration** - Standardized logging, metrics, and error handling for Lambda functions
- **AWS Secrets Manager Integration** - Secure credential management with caching
- **Slack Messaging** - Rich formatting, threading, file uploads, and channel management
- **Elasticsearch Operations** - Query builders, index management, and health monitoring
- **Database Connections** - Connection pooling, automatic retries, and transaction management
- **CloudWatch Metrics** - Batched publishing with custom dimensions
- **JWT Authentication** - RS256 token validation for API Gateway Lambdas (lightweight, no PyJWT needed)
- **Anthropic (Claude) Helper** - Generic client plumbing for LLM calls: API-key or Bedrock IAM auth, forced tool-use, and text calls (prompts and schemas stay in your code)
- **Error Handling** - Intelligent retry patterns with exponential backoff
- **Timezone Utilities** - Timezone handling and formatting
- **Configurable Defaults** - Environment-aware configuration system

## Documentation

**New to this package?** Start with our comprehensive guides:

- **[Quick Start Guide](docs/getting-started/quickstart.md)** - Common patterns and complete examples
- **[Installation Guide](docs/getting-started/installation.md)** - Setup and dependency management
- **[Configuration Guide](docs/getting-started/configuration.md)** - Environment setup and AWS Secrets
- **[Slack Integration Guide](docs/guides/slack-integration.md)** - Messaging, blocks, threading, and files
- **[Testing Guide](docs/development/testing.md)** - Test strategies and coverage

**Complete documentation**: See [docs/](docs/README.md) for all guides and references.

**Component Guides:**

- **[AWS Powertools Integration Guide](docs/guides/powertools-integration.md)** - Logging, metrics, error handling
- **[Slack Integration Guide](docs/guides/slack-integration.md)** - Messaging, blocks, threading, files
- **[Testing Guide](docs/development/testing.md)** - Test strategies and coverage

## Quick Start

### Installation

```bash
pip install nui-python-shared-utils

# With specific extras for optional dependencies
pip install nui-python-shared-utils[all]          # Core optional integrations (excludes Snowflake and LLM)
pip install nui-python-shared-utils[powertools]   # AWS Powertools only
pip install nui-python-shared-utils[slack]        # Slack only
pip install nui-python-shared-utils[elasticsearch] # Elasticsearch only
pip install nui-python-shared-utils[database]     # Database only
pip install nui-python-shared-utils[jwt]          # JWT authentication only
pip install nui-python-shared-utils[snowflake]    # Snowflake SQL API client
pip install nui-python-shared-utils[llm]          # Anthropic (Claude) client helper
```

### Basic Configuration

```python
import nui_shared_utils as nui

# Configure for your environment (optional - uses sensible defaults)
nui.configure(
    es_host="your-elasticsearch-host:9200",
    es_credentials_secret="your-es-secret-name",
    slack_credentials_secret="your-slack-secret-name",
    db_credentials_secret="your-database-secret-name"
)

# Or use environment variables:
# ES_HOST, ES_CREDENTIALS_SECRET, SLACK_CREDENTIALS_SECRET, etc.
```

## What's Next?

After installing the package:

1. **Configuration** → Set up environment variables or programmatic config ([guide](docs/getting-started/configuration.md))
2. **AWS Setup** → Configure Secrets Manager and IAM permissions ([guide](docs/getting-started/configuration.md#aws-infrastructure))
3. **Integration** → Choose your integration and follow the detailed guide:
   - [Slack Integration](docs/guides/slack-integration.md) - Messaging, formatting, file uploads
   - [Elasticsearch Operations](docs/getting-started/quickstart.md#elasticsearch-operations) - Query builders and search
   - [Database Connections](docs/getting-started/quickstart.md#database-connections) - Connection pooling and queries
   - [CloudWatch Metrics](docs/getting-started/quickstart.md#cloudwatch-metrics) - Performance tracking
4. **Testing** → Learn testing strategies ([guide](docs/development/testing.md))

**Complete documentation**: [docs/](docs/README.md)

## Usage Examples

Below are minimal examples to get you started. **For complete examples and detailed usage, see [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)**.

### Secrets Management

```python
from nui_shared_utils import get_secret, get_slack_credentials

# Generic secret retrieval
api_keys = get_secret("my-service/api-keys")

# Specialized getters with normalized field names
slack_creds = get_slack_credentials()  # Uses configured secret name
```

**[→ See full secrets management guide](docs/getting-started/configuration.md#aws-secrets-manager)**

### AWS Powertools Integration

```python
from nui_shared_utils import get_powertools_logger, powertools_handler

# Create logger with Elasticsearch-compatible formatting
logger = get_powertools_logger("my-service", level="INFO")

# Decorate Lambda handler with logging, metrics, and error handling
@powertools_handler(
    service_name="my-service",
    metrics_namespace="MyApp/Service",
    slack_alert_channel="#production-alerts"
)
@logger.inject_lambda_context
def lambda_handler(event, context):
    logger.info("Processing event", extra={"event_id": event.get("id")})
    return {"statusCode": 200, "body": "Success"}
```

**Features:**

- ✅ Elasticsearch-compatible timestamps (`2025-01-18T04:39:27.788Z`)
- ✅ Automatic Lambda context injection (function name, request ID, cold start)
- ✅ CloudWatch metrics publishing
- ✅ Slack error alerts with graceful degradation
- ✅ Local development with colored logs

**[→ See comprehensive Powertools integration guide](docs/guides/powertools-integration.md)**

### Slack Integration

```python
from nui_shared_utils import SlackClient, SlackBlockBuilder

slack = SlackClient()

# Simple message
slack.send_message(channel='#alerts', text='Service deployment complete')

# Rich formatted message with blocks
builder = SlackBlockBuilder()
blocks = builder.add_header("Alert", emoji="warning").add_section("Status", "Active").build()
slack.send_message(channel='#incidents', blocks=blocks)
```

**[→ See comprehensive Slack integration guide](docs/guides/slack-integration.md)**

### Elasticsearch Operations

```python
from nui_shared_utils import ElasticsearchClient, ESQueryBuilder

es = ElasticsearchClient()
query_builder = ESQueryBuilder()
query = query_builder.date_range("@timestamp", "now-1h", "now").term("level", "ERROR").build()
results = es.search(index="logs-*", body={"query": query})
```

**[→ See full Elasticsearch guide](docs/getting-started/quickstart.md#elasticsearch-operations)**

### Database Connections

```python
from nui_shared_utils import DatabaseClient

db = DatabaseClient()

# Execute queries with automatic retry and connection pooling
async with db.get_connection() as conn:
    results = await conn.execute("SELECT * FROM orders WHERE status = %s", ["confirmed"])
```

**[→ See full database guide](docs/getting-started/quickstart.md#database-connections)**

### Snowflake (SQL API)

Pure-Python Snowflake client (no `snowflake-connector-python`), keypair auth via
Secrets Manager, with NUI session defaults (`TIMEZONE=Pacific/Auckland`, role
`NUI_LAMBDA`) that you can override via `timezone=` and `role=`, plus a redacting
query-logging hook.

```python
from nui_shared_utils import create_snowflake_client

# Loads account/user/private_key from Secrets Manager ("snowflake-credentials"
# by default; override with SNOWFLAKE_CREDENTIALS_SECRET or secret_name=).
# The NUI defaults are overridable, so the client stays generic for any account.
client = create_snowflake_client(
    warehouse="COMPUTE_WH",
    database="ANALYTICS",
    timezone="UTC",          # override the Pacific/Auckland default
    role="MY_APP_ROLE",      # override the NUI_LAMBDA default
)
rows = client.query("SELECT id, name FROM orders WHERE status = ?", ["confirmed"])
```

Sync is the default; use `create_async_snowflake_client(...)` inside a Lambda
or FastAPI app already on an event loop. Snowflake SQL API bindings use
positional `?` placeholders, not connector-style `%s` placeholders. Requires
the `[snowflake]` extra.

**[→ See full Snowflake guide](docs/guides/snowflake-integration.md)**

### CloudWatch Metrics

```python
from nui_shared_utils import MetricsPublisher, track_lambda_performance

metrics = MetricsPublisher(namespace="MyApplication")

@track_lambda_performance(metrics)
def lambda_handler(event, context):
    metrics.put_metric("ProcessedItems", 150, "Count")
    return {"statusCode": 200}
```

**[→ See full metrics guide](docs/getting-started/quickstart.md#cloudwatch-metrics)**

### JWT Authentication

```python
from nui_shared_utils import require_auth, AuthenticationError

def lambda_handler(event, context):
    try:
        claims = require_auth(event)  # Validates Bearer token from Authorization header
    except AuthenticationError as e:
        return {"statusCode": 401, "body": "Unauthorized"}

    user_id = claims["sub"]
    return {"statusCode": 200, "body": f"Hello {user_id}"}
```

**[→ See JWT authentication guide](docs/guides/jwt-authentication.md)**

### Anthropic (Claude) Helper

Generic plumbing for Claude calls: build a client (API-key or Bedrock IAM), make
a forced tool-use call or a text call, and get the parsed result back. Prompts,
tool schemas, model ids, and result post-processing stay in your code.

```python
from nui_shared_utils import build_anthropic_client, call_tool

# API-key auth: explicit key -> ANTHROPIC_API_KEY env -> Secrets Manager
client = build_anthropic_client(secret_name="my/anthropic-key")
# Or Bedrock IAM (no key): build_anthropic_client(mode="bedrock", region="us-east-1")

tool = {
    "name": "classify",
    "description": "Classify the text.",
    "input_schema": {
        "type": "object",
        "properties": {"label": {"type": "string"}, "score": {"type": "number"}},
        "required": ["label", "score"],
        "additionalProperties": False,
    },
}

# Forced tool-use; returns the tool's input dict, or None on any model/parse failure.
result = call_tool(client, tool=tool, prompt="Great product!", model="claude-haiku-4-5", max_tokens=256)
if result is not None:
    print(result["label"], result["score"])
```

Requires the `[llm]` extra. `call_tool` is best-effort (returns `None`, never
raises); `call_text` returns `{text, input_tokens, output_tokens}`.

**[→ See full LLM integration guide](docs/guides/llm-integration.md)**

### Error Handling

```python
from nui_shared_utils import with_retry, handle_lambda_error

@handle_lambda_error
@with_retry(max_attempts=3)
def lambda_handler(event, context):
    # Your Lambda logic with automatic error handling and retries
    return {"statusCode": 200}
```

**[→ See full error handling guide](docs/getting-started/quickstart.md#error-handling)**

## Configuration

The package supports multiple configuration methods. **For detailed configuration options, see [docs/getting-started/configuration.md](docs/getting-started/configuration.md)**.

### Environment Variables

```bash
ES_HOST=localhost:9200                    # Elasticsearch host
ES_CREDENTIALS_SECRET=elasticsearch-creds # AWS secret name for ES
DB_CREDENTIALS_SECRET=database-creds      # AWS secret name for database
SLACK_CREDENTIALS_SECRET=slack-bot-token  # AWS secret name for Slack
AWS_REGION=us-east-1                      # AWS region
```

### Programmatic Configuration

```python
import nui_shared_utils as nui

nui.configure(
    es_host="localhost:9200",
    slack_credentials_secret="dev/slack-token"
)
```

**[→ See complete configuration guide](docs/getting-started/configuration.md)**

## AWS Infrastructure Requirements

This package requires AWS Secrets Manager for credential storage and IAM permissions for CloudWatch metrics.

**For detailed AWS setup instructions, see [docs/getting-started/configuration.md#aws-infrastructure](docs/getting-started/configuration.md)**.

### Quick Reference

**Secrets Manager** - Store credentials as JSON:

- Elasticsearch: `{"host": "...", "username": "...", "password": "..."}`
- Database: `{"host": "...", "port": 3306, "username": "...", "password": "...", "database": "..."}`
- Slack: `{"bot_token": "xoxb-...", "webhook_url": "..."}`
- JWT Public Key: `{"TOKEN_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\n..."}`

**IAM Permissions** - Lambda execution role needs:

- `secretsmanager:GetSecretValue` for credential access
- `cloudwatch:PutMetricData` for metrics publishing

**[→ See complete AWS infrastructure guide](docs/getting-started/configuration.md#aws-infrastructure)**

## Testing

**For comprehensive testing guide, see [docs/development/testing.md](docs/development/testing.md)**.

```bash
# Install with dev dependencies
pip install nui-python-shared-utils[dev]

# Run all tests
pytest

# Run with coverage
pytest --cov=nui_shared_utils --cov-report=html

# Run specific test categories
pytest -m unit           # Unit tests only
pytest -m integration    # Integration tests (requires AWS)
```

## Contributing

We welcome contributions! This package currently supports MySQL/PostgreSQL, Elasticsearch, Slack, and core AWS services (Secrets Manager, CloudWatch). **We're open to expanding support for additional databases (MongoDB, DynamoDB, etc.) and AWS services (SQS, SNS, EventBridge, etc.).**

### How to Contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Contribution Ideas

- **Database integrations**: MongoDB, DynamoDB, Redis, Cassandra
- **AWS services**: SQS, SNS, EventBridge, Step Functions, S3, SES
- **Messaging platforms**: Microsoft Teams, Discord, PagerDuty
- **Monitoring**: Datadog, New Relic, Prometheus exporters
- **Search engines**: OpenSearch, Algolia, Typesense
- **CLI enhancements**: Additional automation commands for common workflows

See our [development guide](docs/development/testing.md) for testing patterns and architecture guidelines.

### Built-in CLI Tools

The package includes `slack-channel-setup` - a CLI tool for automating Slack workspace channel creation from YAML configuration files. This generic tool works with any Slack workspace and can be used independently of Lambda functions.

```bash
# Install and use
pip install nui-python-shared-utils[slack]
slack-channel-setup --config channels.yaml
```

## Documentation & Support

### 📚 Documentation

- **[Complete Documentation](docs/README.md)** - Comprehensive guides and references
- **[Quick Start Guide](docs/getting-started/quickstart.md)** - Get up and running fast
- **[Configuration Guide](docs/getting-started/configuration.md)** - Setup and AWS integration
- **[Slack Integration Guide](docs/guides/slack-integration.md)** - Detailed Slack features
- **[Testing Guide](docs/development/testing.md)** - Test strategies and coverage

### 🔗 Links

- **GitHub Repository**: https://github.com/nuimarkets/nui-python-shared-utils
- **Issue Tracker**: https://github.com/nuimarkets/nui-python-shared-utils/issues
- **PyPI Package**: https://pypi.org/project/nui-python-shared-utils/

### 💬 Support

- **Bug Reports**: [GitHub Issues](https://github.com/nuimarkets/nui-python-shared-utils/issues)
- **Feature Requests**: [GitHub Issues](https://github.com/nuimarkets/nui-python-shared-utils/issues)
- **Questions**: Check [docs/](docs/README.md) first, then open an issue

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## About NUI Markets

NUI Markets is a technology company focused on building innovative trading and marketplace platforms. This package represents our commitment to open-source tooling and production-grade infrastructure patterns for AWS Lambda development.
