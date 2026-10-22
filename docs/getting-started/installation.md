# Installation Guide

This guide covers installation, dependency management, and environment setup for `nui-python-shared-utils`.

## Requirements

- Python 3.10 or higher
- AWS credentials configured for Secrets Manager access
- Optional: AWS CLI for credential management

## Installation Methods

### Standard Installation

```bash
# Basic installation with core dependencies
pip install nui-python-shared-utils
```

### Installation with Optional Dependencies

The package uses optional extras to minimize Lambda bundle size. Install only the integrations you need:

```bash
# Slack integration only
pip install nui-python-shared-utils[slack]

# Elasticsearch integration only
pip install nui-python-shared-utils[elasticsearch]

# Database integration only
pip install nui-python-shared-utils[database]

# JWT authentication only
pip install nui-python-shared-utils[jwt]

# Zone data, for minimal images with no system IANA database (Alpine, distroless)
pip install nui-python-shared-utils[timezone]

# All integrations
pip install nui-python-shared-utils[all]

# Development dependencies
pip install nui-python-shared-utils[dev]
```

### Development Installation

For local development and testing:

```bash
# Clone the repository
git clone https://github.com/nuimarkets/nui-python-shared-utils.git
cd nui-python-shared-utils

# Install in development mode with all dependencies
pip install -e .[dev]
```

## Dependency Overview

### Core Dependencies (Always Installed)

- `boto3` - AWS SDK for Python
- `click` - CLI framework
- `pyyaml` - YAML configuration parsing
- `tzdata` - Zone data, **Windows only** (installed via an environment marker)

Timezone conversion uses the standard library's `zoneinfo`, which reads the
system IANA database. Linux, macOS, and the AWS Lambda runtimes all ship one,
so nothing extra is installed there.

### Optional Dependencies by Extra

#### elasticsearch

- `elasticsearch>=7.17.0,<10.0.0` - Elasticsearch client

  Client majors 7.17, 8.x and 9.x are all supported and tested; pick whichever
  your cluster needs. The `<10` ceiling is the untested next major, not a known
  incompatibility. See [ADR-007](../decisions/ADR-007-elasticsearch-client-version-range.md).

#### database

- `pymysql>=1.0.0` - MySQL driver
- `psycopg2-binary>=2.9.0` - PostgreSQL driver

#### slack

- `slack-sdk>=3.19.0` - Official Slack SDK

#### jwt

- `rsa>=4.9` - Pure Python RSA implementation (~100KB, no C extensions)

#### timezone

- `tzdata>=2023.3` - Zone data for images that carry no system IANA database

  Only needed on a minimal image (Alpine, distroless) where `/usr/share/zoneinfo`
  is absent, which no environment marker can detect. Without it, importing
  `nui_shared_utils.timezone` raises `ZoneInfoNotFoundError`. Windows already
  gets `tzdata` from the core dependencies.

  `[all]` covers the integrations and does **not** include this; on a minimal
  image ask for it by name (`[all,timezone]`).

#### dev

- `pytest>=7.0.0` - Testing framework
- `pytest-cov>=4.0.0` - Coverage reporting
- `pytest-mock>=3.10.0` - Mocking utilities
- `moto>=4.0.0` - AWS service mocking
- `black>=22.0.0` - Code formatting
- `mypy>=0.990` - Type checking
- `boto3-stubs[essential]>=1.20.0` - Type stubs for boto3
- `twine>=4.0.0` - Package publishing
- `build>=0.8.0` - Package building

## Virtual Environment Setup

### Using venv (Recommended)

```bash
# Create virtual environment
python -m venv venv

# Activate (Linux/Mac)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate

# Install package
pip install nui-python-shared-utils[all]
```

### Using conda

```bash
# Create environment
conda create -n nui-utils python=3.10

# Activate environment
conda activate nui-utils

# Install package
pip install nui-python-shared-utils[all]
```

## AWS Configuration

### Credentials Setup

The package requires AWS credentials with Secrets Manager access:

```bash
# Configure AWS CLI (if not already done)
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1
```

### Required IAM Permissions

Your AWS credentials need these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

## Lambda Layer Usage

For AWS Lambda deployment, consider using the package as a layer:

### Creating a Lambda Layer

```bash
# Create layer directory structure
mkdir python
pip install nui-python-shared-utils[all] -t python/

# Create layer zip
zip -r nui-utils-layer.zip python/

# Upload to AWS Lambda Layers (via AWS CLI)
aws lambda publish-layer-version \
  --layer-name nui-python-shared-utils \
  --description "NUI Lambda Shared Utilities" \
  --zip-file fileb://nui-utils-layer.zip \
  --compatible-runtimes python3.10 python3.11
```

### Using in Lambda Function

```python
# In your Lambda function code
import nui_shared_utils as nui

# Configure and use
nui.configure(
    es_host="your-es-host:9200",
    slack_credentials_secret="prod/slack-token"
)
```

## Verification

Test your installation:

```python
import nui_shared_utils as nui

# Check version
print(nui.__version__)  # Should print version number

# Test configuration
config = nui.get_config()
print(config.to_dict())  # Should show default configuration

# Test optional imports (will be None if not installed)
print(f"Elasticsearch: {nui.ElasticsearchClient is not None}")
print(f"Database: {nui.DatabaseClient is not None}")
```

## Troubleshooting

### Common Issues

#### Import Errors with Optional Dependencies

```
ImportError: No module named 'elasticsearch'
```

**Solution**: Install with the appropriate extra:

```bash
pip install nui-python-shared-utils[elasticsearch]
```

#### AWS Credentials Not Found

```
NoCredentialsError: Unable to locate credentials
```

**Solution**: Configure AWS credentials using `aws configure` or environment variables.

#### Version Conflicts

```
ERROR: pip's dependency resolver does not currently consider all the ways...
```

**Solution**: Use a fresh virtual environment or update conflicting packages.

### Getting Help

- Check the [Troubleshooting Guide](troubleshooting.md)
- Review [GitHub Issues](https://github.com/nuimarkets/nui-python-shared-utils/issues)
- Consult the [Configuration Guide](configuration.md) for setup details
