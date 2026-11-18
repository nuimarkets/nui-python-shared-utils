# CLI Tools Guide

This package includes command-line tools for automating common workflows.

**Last Updated**: 2025-01-19

## Slack Channel Setup

The `slack-channel-setup` command automates Slack workspace channel creation and configuration from YAML files.

### Installation

```bash
# Install with Slack support
pip install nui-python-shared-utils[slack]

# Verify installation
slack-channel-setup --help
```

### Quick Start

**1. Create a channel configuration file:**

```yaml
# channels.yaml
channels:
  - name: app-alerts
    description: "Critical application alerts"
    purpose: "Automated alerts from production"
    topic: "Production alerts"
    service: "monitoring-service"
    invite_users:
      - "devops-team"
      - "on-call-engineer"

  - name: deployments
    description: "Deployment notifications"
    purpose: "Track production deployments"
    topic: "Release tracking"
    service: "ci-cd-pipeline"
    invite_users:
      - "engineering-team"
```

**2. Set your Slack bot token:**

```bash
export SLACK_BOT_TOKEN=xoxb-your-token-here
```

**3. Run the setup:**

```bash
# Create channels
slack-channel-setup --config channels.yaml

# Check which channels exist first
slack-channel-setup --config channels.yaml --check-only

# Validate configuration without creating
slack-channel-setup --config channels.yaml --validate-only
```

### Features

#### Automated Channel Creation

- Creates public Slack channels programmatically
- Handles existing channels gracefully (updates configuration)
- Sets channel purpose, topic, and description
- Posts welcome messages with channel context

Python callers can use `ChannelCreator.setup_channel(definition)` for one channel or
`ChannelCreator.create_channels(definitions)` for a batch. Both share the CLI's setup
sequence and pause for one second after each successful setup. Single-channel setup
returns the channel ID, or `None` if an existing channel cannot be found. Unexpected
errors propagate to the caller; batch setup logs them and continues with the next channel.

#### User Management

- Invites specified users automatically
- Bot joins all channels
- Supports usernames or user IDs

#### Configuration Generation

- Outputs channel IDs for your application
- Multiple formats: YAML (serverless.yml) or ENV variables

```bash
# Generate environment variables
slack-channel-setup --config channels.yaml --output channels.env --output-format env

# Generate serverless.yml snippet
slack-channel-setup --config channels.yaml --output serverless-channels.yml --output-format yaml
```

#### Validation & Testing

- Validates channel names before creation
- Tests bot access to created channels
- Interactive confirmation before making changes

### Configuration Reference

#### Channel Definition

```yaml
channels:
  - name: string              # Required: channel name (lowercase, hyphens)
    description: string       # Required: shown in welcome message
    purpose: string          # Optional: channel purpose (Slack UI)
    topic: string            # Optional: channel topic (Slack UI)
    service: string          # Optional: service/app name for context
    invite_users: array      # Optional: list of usernames or user IDs
```

#### Channel Naming Rules

- Lowercase letters, numbers, hyphens only
- Cannot start or end with hyphens
- Maximum 80 characters
- Cannot contain spaces or special characters

### Command Options

```bash
slack-channel-setup [OPTIONS]

Options:
  --config PATH               Path to channel configuration YAML [required]
  --token TEXT                Slack bot token (or SLACK_BOT_TOKEN env var)
  --check-only                Check existing channels, don't create
  --dry-run                   Preview changes without creating channels
  --output PATH               Output file for configuration
  --output-format [yaml|env]  Output format (default: yaml)
  --no-interactive            Skip confirmations (CI/automation)
  --validate-only             Validate names only, don't create
  --test-access               Test bot access after creation
  --version                   Show CLI version
  --help                      Show help message
```

### Examples

#### Basic Setup

```bash
# Standard workflow
export SLACK_BOT_TOKEN=xoxb-...
slack-channel-setup --config channels.yaml
```

#### CI/CD Integration

```bash
# Non-interactive mode for automation
slack-channel-setup \
  --config channels.yaml \
  --no-interactive \
  --output channels.env \
  --output-format env
```

#### Development Workflow

```bash
# 1. Validate configuration
slack-channel-setup --config channels.yaml --validate-only

# 2. Check what exists
slack-channel-setup --config channels.yaml --check-only

# 3. Preview changes (dry run)
slack-channel-setup --config channels.yaml --dry-run

# 4. Create channels with testing
slack-channel-setup --config channels.yaml --test-access
```

### Slack Bot Setup

**Prerequisites:**

1. Create a Slack App at https://api.slack.com/apps
2. Add bot token scopes:
   - `channels:read` - List channels
   - `channels:manage` - Create channels and set purpose/topic (public channels)
   - `groups:write` - Create and manage private channels (if using private channels)
   - `chat:write` - Post welcome messages
   - `chat:write.public` - (Optional) Post to public channels without being added first
   - `users:read` - Look up users by name
   - `users:read.email` - (Optional) Access user email addresses
3. Install app to workspace
4. Copy bot token (starts with `xoxb-`)

### Output Formats

#### Environment Variables (`--output-format env`)

```bash
# channels.env
APP_ALERTS_CHANNEL=C01234567
DEPLOYMENTS_CHANNEL=C01234568
ERRORS_CHANNEL=C01234569
```

Usage in your application:

```python
import os
from nui_shared_utils import SlackClient

slack = SlackClient()
alerts_channel = os.environ['APP_ALERTS_CHANNEL']
slack.send_message(channel=alerts_channel, text="Alert!")
```

#### Serverless Configuration (`--output-format yaml`)

```yaml
# serverless-channels.yml
custom:
  channels:
    alerts: "C01234567"  # app-alerts
    deployments: "C01234568"  # deployments
    errors: "C01234569"  # errors
```

### Troubleshooting

#### Authentication Errors

```
❌ Authentication failed: invalid_auth
```

**Solution**: Check that `SLACK_BOT_TOKEN` is set correctly and starts with `xoxb-`

#### Permission Errors

```
❌ Failed to create channel: missing_scope
```

**Solution**: Add required bot token scopes in Slack App settings

#### Channel Name Validation Errors

```
❌ Invalid channel name: my_channel
```

**Solution**: Use hyphens instead of underscores: `my-channel`

### Best Practices

1. **Version Control**: Commit `channels.yaml` to track channel configuration
2. **CI Integration**: Use `--no-interactive` in automated pipelines
3. **Validation First**: Always run `--validate-only` before creating
4. **Testing**: Use `--check-only` to audit existing channels
5. **Documentation**: Keep channel descriptions clear and up-to-date

### Template Files

See example configuration templates:

- **[Channel Configuration Template](../templates/channels.yaml.template)** - Complete example with comments

---

**Next Steps:**

- See [Slack Integration Guide](slack-integration.md) for using SlackClient in your code
- Check [Templates](../templates/) for example configurations
