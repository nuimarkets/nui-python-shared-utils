# AGENTS.md

Canonical agent guidance for this repository. Read by Claude Code (via `CLAUDE.md` import), Codex CLI, and humans. If you only have time to read one doc, read this one.

## Quick Links

📚 **Documentation**: See [docs/README.md](docs/README.md) for comprehensive guides

- [Quick Start Guide](docs/getting-started/quickstart.md) - Usage patterns and examples
- [Configuration Guide](docs/getting-started/configuration.md) - Environment setup and credentials
- [Slack Integration](docs/guides/slack-integration.md) - Messaging, formatting, and file uploads
- [Testing Guide](docs/development/testing.md) - Test strategies and coverage

🔧 **Development**: Jump to [Development Commands](#development-commands) below

## Navigation Guide

**Are you...**

- 🆕 **New to this package?** → Start with [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)
- 🔧 **Contributing code?** → See [Development Commands](#development-commands) below
- 🧪 **Writing tests?** → See [docs/development/testing.md](docs/development/testing.md)
- 📦 **Deploying/publishing?** → See [Package Building](#package-building-and-publishing) below
- 💬 **Integrating Slack?** → See [docs/guides/slack-integration.md](docs/guides/slack-integration.md)

## Project Overview

This is `nui-python-shared-utils`, a Python package providing production-ready utilities for AWS Lambda functions and CLI tools. Built and battle-tested on the NUI platform, it offers standardized integrations for Slack, Elasticsearch, database operations, CloudWatch metrics, and error handling patterns. The package is designed to be generic and configurable for any Python environment.

## Architecture

### Core Module Structure

- **Configuration System** (`config.py`) - Environment-aware configuration with AWS Secrets Manager integration
- **Secrets Management** (`secrets_helper.py`) - Centralized credential handling with caching
- **Slack Integration** (`slack_client.py`, `slack_formatter.py`) - Rich messaging, threading, and file uploads
- **Elasticsearch** (`es_client.py`, `es_query_builder.py`) - Query builders and health monitoring
- **Database** (`db_client.py`) - Connection pooling with retry logic
- **Metrics** (`cloudwatch_metrics.py`) - Batched CloudWatch publishing with decorators
- **JWT Authentication** (`jwt_auth.py`) - RS256 token validation for API Gateway Lambdas (uses `rsa` package)
- **LLM Helper** (`llm.py`) - Generic Anthropic (Claude) client plumbing: API-key/Bedrock auth, forced tool-use call, text call (uses `anthropic`)
- **Error Handling** (`error_handler.py`) - Retry patterns with exponential backoff
- **Timezone Utils** (`timezone.py`) - Timezone conversion and formatting utilities (stdlib `zoneinfo`)

### Optional Dependencies

The package uses optional extras to minimize Lambda bundle size:

- `elasticsearch` - Elasticsearch client and query builders
- `database` - MySQL/PostgreSQL drivers
- `slack` - Slack SDK
- `jwt` - RS256 JWT validation (`rsa` package)
- `snowflake` - Pure-Python Snowflake SQL API client (`snowflake-sql-api`)
- `llm` - Anthropic (Claude) client helper (`anthropic[bedrock]`, both API-key and Bedrock IAM auth)
- `timezone` - `tzdata`, for minimal images with no system IANA database (Alpine, distroless)
- `all` - All integrations
- `dev` - Development and testing tools

### Slack Workspace Automation

The `slack_setup/` submodule provides automated Slack workspace configuration:

- **Channel Creation** - Programmatic channel and permission setup
- **Template System** - YAML-based channel definitions
- **CLI Tool** - `slack-channel-setup` command for automation
- **Generic Design** - Adaptable for any Slack workspace, not NUI-specific

### Build-time tools (`tools/`)

`tools/lambda-validation/` is a separate distribution, `nui-lambda-validation`, with its own
`pyproject.toml`, tests and workflow (`.github/workflows/lambda-validation.yml`). It must not
import or depend on `nui_shared_utils`, and `MANIFEST.in` prunes `tools/` so it never ships in
this package. The root `pytest` run does not collect its tests; run them from the tool
directory as its README describes. Consumers pin it by full commit SHA, so a change there is a
new release for every repository that bumps its pin.

## Design Principles

### Keeping the Package Generic

This package is intentionally designed to avoid vendor-specific assumptions:

**✅ DO:**

- Use configurable defaults (e.g., `currency` as required parameter)
- Accept parameters for service-specific values (e.g., `service_name` in alerts)
- Provide flexible configuration via environment variables or programmatic setup
- Keep utility functions generic and reusable

**❌ DON'T:**

- Hardcode service names, currency codes, or business logic
- Add domain-specific database query methods (use generic `query()`, `execute()` methods)
- Create mapping dictionaries for specific organizations (emoji maps, status codes, etc.)
- Assume specific table schemas or column names

**Examples:**

- ❌ Hardcoded organization-specific mappings and constants
- ✅ Accept values as parameters or via configuration
- ❌ Default values that assume specific geography/currency/timezone
- ✅ Required parameters or configurable defaults
- ❌ Database methods that assume specific table schemas
- ✅ Generic query methods that accept SQL and parameters

### When Adding New Features

Before adding convenience methods or defaults:

1. **Ask**: "Is this specific to NUI, or useful for any Lambda project?"
2. **Check**: Could this be made configurable rather than hardcoded?
3. **Test**: Can someone use this without NUI-specific knowledge?

If a feature is NUI-specific:

- Consider if it belongs in this shared package
- Document it clearly as an example pattern users can adapt
- Provide configuration options to override defaults

## Development Commands

### Environment Setup

```bash
# Install package in development mode with all dependencies
pip install -e .[dev]

# Install with specific integrations only
pip install -e .[slack,elasticsearch]

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
```

### Testing

**See [docs/development/testing.md](docs/development/testing.md) for comprehensive testing guide**

```bash
# Run all tests
pytest

# Run with coverage reporting
pytest --cov=nui_shared_utils --cov-report=html

# Run only unit tests (skip integration tests requiring AWS)
pytest -m "not integration"

# Run specific test categories
pytest -m unit           # Unit tests only
pytest -m integration    # Integration tests only
pytest -m slow          # Slow tests only

# Run single test file
pytest tests/test_slack_client.py -v
```

### Code Quality

```bash
# Format code with Black (line length: 120)
black nui_shared_utils/ tests/

# Check formatting without changes
black --check nui_shared_utils/

# Type checking with MyPy
mypy nui_shared_utils/ --config-file mypy.ini

# Run linting (currently configured to use Black)
black --check nui_shared_utils/

# Lint markdown documentation
npx markdownlint-cli2 '**/*.md'

# Auto-fix markdown formatting issues
npx markdownlint-cli2 --fix '**/*.md'
```

### Package Building and Publishing

**See [docs/getting-started/installation.md](docs/getting-started/installation.md) for installation details**

```bash
# Build package
python -m build

# Install build tools
pip install build twine

# Publish to PyPI (requires credentials)
python -m twine upload dist/*
```

## Configuration Patterns

### Credential Resolution

Clients resolve credentials in this order (first match wins):

1. **Explicit `credentials` dict** — constructor param, skips everything else
2. **Environment variables** — per-client patterns, skips Secrets Manager
3. **AWS Secrets Manager** — default behavior

The `get_*_credentials` helpers in `secrets_helper.py` follow tiers 2 and 3 through the same
per-service env resolver and secret normaliser the clients use. Elasticsearch host:
`host` argument > `ES_HOST`/`ELASTICSEARCH_HOST` > `configure(es_host=...)` > secret `host` >
`localhost:9200`.

### Environment Variables

Runtime configuration:

- `ES_HOST` - Elasticsearch endpoint
- `ES_CREDENTIALS_SECRET` - AWS secret name for ES credentials
- `DB_CREDENTIALS_SECRET` - AWS secret name for database credentials
- `SLACK_CREDENTIALS_SECRET` - AWS secret name for Slack token
- `AWS_REGION` - AWS region for services

Direct credential env vars (bypass Secrets Manager when set):

| Client | Required | Optional |
|--------|----------|----------|
| Slack | `SLACK_BOT_TOKEN` | `SLACK_WEBHOOK_URL` |
| Elasticsearch | `ES_PASSWORD` | `ES_USERNAME` (default: `elastic`) |
| Database (MySQL) | `DB_HOST` + `DB_PASSWORD` | `DB_PORT` (3306), `DB_USERNAME` (root), `DB_DATABASE` (app) |
| Database (PostgreSQL) | `DB_HOST` + `DB_PASSWORD` | `DB_PORT` (5432), `DB_USERNAME` (postgres), `DB_DATABASE` (postgres) |

### AWS Secrets Format

Secrets should follow standardized JSON structures:

```json
// Elasticsearch
{"host": "elastic:9200", "username": "user", "password": "pass"}

// Database  
{"host": "db-host", "port": 3306, "username": "user", "password": "pass", "database": "db"}

// Slack
{"bot_token": "xoxb-...", "webhook_url": "https://hooks.slack.com/..."}
```

### Programmatic Configuration

```python
import nui_shared_utils as nui

# Pass credentials directly (no Secrets Manager needed)
slack = nui.SlackClient(credentials={"bot_token": "xoxb-..."})
es = nui.ElasticsearchClient(credentials={"username": "elastic", "password": "pass"})
db = nui.DatabaseClient(credentials={"host": "localhost", "port": 3306, "username": "root", "password": "pass", "database": "app"})

# Or configure globally
nui.configure(
    es_host="localhost:9200",
    slack_credentials_secret="dev/slack-token"
)
```

## Common Usage Patterns

For detailed usage examples and integration patterns, see:

- **[Quick Start Guide](docs/getting-started/quickstart.md)** - Common usage patterns for all components
- **[Slack Integration Guide](docs/guides/slack-integration.md)** - Slack messaging, blocks, threading, and file uploads
- **[Configuration Guide](docs/getting-started/configuration.md)** - Environment setup and AWS Secrets integration

### Quick Reference

```python
# See docs/getting-started/quickstart.md for complete examples
from nui_shared_utils import SlackClient, MetricsPublisher, with_retry

# Slack messaging with rich formatting
slack = SlackClient()
slack.send_message(channel="#alerts", text="Alert message")

# CloudWatch metrics publishing
metrics = MetricsPublisher(namespace="MyService")
metrics.put_metric("ProcessedItems", count, "Count")

# Error handling with automatic retries
@with_retry(max_attempts=3)
def critical_operation():
    pass
```

## Testing Strategy

### Test Categories (pytest markers)

- `@pytest.mark.unit` - Fast unit tests with mocking
- `@pytest.mark.integration` - Tests requiring AWS services
- `@pytest.mark.slow` - Long-running tests

### AWS Testing

Integration tests use `moto` for AWS service mocking. Some tests require real AWS credentials for full integration testing.

### Test Structure

```text
tests/
├── test_<module>.py     # Main module tests
├── conftest.py         # Shared fixtures
└── fixtures/           # Test data files
```

## Contribution Guidelines

### Pull Request Checklist

Before submitting PRs, ensure:

- [ ] **No hardcoded organization-specific values** (service names, currencies, business logic)
- [ ] **Configuration options** provided for any defaults
- [ ] **Tests pass** with `pytest --cov`
- [ ] **Code formatted** with Black (`black nui_shared_utils/ tests/`)
- [ ] **Type hints** included for public APIs
- [ ] **Documentation updated** in docs/ for new features
- [ ] **Generic naming** - avoid organization-specific terminology in public APIs

### Code Review Focus Areas

Reviewers should check for:

- Generic utility patterns vs vendor-specific code
- Configurable defaults rather than hardcoded values
- Clear documentation of any platform assumptions
- Reusability across different AWS Lambda projects

## Package Distribution

### Version Management

Version is defined in both `setup.py` and `pyproject.toml` and should be kept in sync. The package follows semantic versioning.

### PyPI Publishing

The package is published to PyPI as `nui-python-shared-utils`. The old name `nui-lambda-shared-utils` is a redirect package that depends on `nui-python-shared-utils`.

### CLI Tools

The package provides `slack-channel-setup` CLI tool for automating Slack workspace channel creation and configuration from YAML files. This is a generic tool useful for any team managing Slack workspaces.

**Usage:**

```bash
# Create channels from YAML config
slack-channel-setup --config channels.yaml

# Check which channels exist
slack-channel-setup --config channels.yaml --check-only

# Generate environment variables
slack-channel-setup --config channels.yaml --output channels.env --output-format env
```

## AWS Lambda Integration

### Bundle Size Optimization

- Use specific extras (`[slack]`, `[elasticsearch]`) rather than `[all]`
- Optional imports prevent failure if dependencies aren't installed
- Core utilities work without optional dependencies

### Lambda Layer Usage

The package is designed to work well in Lambda layers for sharing across multiple functions.

### Environment Integration

- Automatic AWS region detection
- Secrets Manager integration with credential caching
- CloudWatch metrics with proper dimensions

## Documentation Organization

This project follows a clear documentation structure:

- **[AGENTS.md](AGENTS.md)** (this file) - Development workflows, commands, testing strategies (`CLAUDE.md` imports it)
- **[README.md](README.md)** - User-facing PyPI package description with quick start examples
- **[docs/](docs/README.md)** - Comprehensive usage guides and references
  - `getting-started/` - Installation, configuration, quick start patterns
  - `guides/` - Component-specific how-to guides (Slack, ES, Database, etc.)
  - `development/` - Testing strategies, contributing guidelines
  - `templates/` - Configuration file templates (Slack setup YAML)
  - `archive/` - Historical documentation and migration notes

**For coding agents**: The docs/ directory contains detailed usage patterns,
configuration examples, and integration guides that complement these development instructions.

**For new package users**: Start with [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)
for working code examples and common patterns.
