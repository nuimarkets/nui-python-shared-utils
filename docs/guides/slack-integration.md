# Slack Integration Guide

Comprehensive guide for using Slack messaging and formatting features in `nui-python-shared-utils`.

**Last Updated**: 2025-11-16

## Overview

The package provides rich Slack integration through two main components:

- **SlackClient** - Message sending, file uploads, threading
- **SlackBlockBuilder** - Rich message formatting with Slack blocks

## Quick Start

```python
import nui_shared_utils as nui

# Configure Slack credentials
nui.configure(slack_credentials_secret="prod/slack-token")

# Send a simple message
slack = nui.SlackClient()
slack.send_message(
    channel="#alerts",
    text="Hello from Lambda!"
)
```

## Configuration

### AWS Secrets Manager Setup

Create a secret with your Slack bot token:

```bash
aws secretsmanager create-secret \
  --name "slack-credentials" \
  --description "Slack bot token for Lambda notifications" \
  --secret-string '{"bot_token":"YOUR_SLACK_BOT_TOKEN_HERE"}'
```

**Secret Format:**

```json
{
  "bot_token": "YOUR_SLACK_BOT_TOKEN_HERE",
  "webhook_url": "YOUR_WEBHOOK_URL_HERE"
}
```

### Getting a Slack Bot Token

1. Go to https://api.slack.com/apps
2. Create a new app or select existing
3. Navigate to "OAuth & Permissions"
4. Add bot token scopes:
   - `chat:write` - Send messages
   - `files:write` - Upload files
   - `channels:read` - List channels
5. Install app to workspace
6. Copy "Bot User OAuth Token" (starts with `xoxb-`)

### Environment Configuration

```python
import os
import nui_shared_utils as nui

# Configure in Lambda handler
stage = os.environ.get('STAGE', 'dev')
nui.configure(slack_credentials_secret=f"{stage}/slack-token")
```

## Basic Messaging

### Simple Text Messages

```python
from nui_shared_utils import SlackClient

slack = SlackClient()

# Basic message
slack.send_message(
    channel="#general",
    text="Deployment completed successfully"
)

# Message with emoji
slack.send_message(
    channel="#alerts",
    text=":white_check_mark: All tests passed"
)
```

### Message Threading

Threading needs the parent message's `ts`, so post it with `post_message`
rather than `send_message`: the two take the same arguments and add the same
header, but `send_message` answers a bare `True`/`False` and drops the `ts`.

```python
# Send initial message, keeping Slack's response
sent = slack.post_message(
    channel="#support",
    text="New support ticket received"
)

# Reply in thread
slack.send_thread_reply(
    channel=sent.channel,
    thread_ts=sent.ts,
    text="Ticket assigned to engineering team"
)
```

`post_message` returns a `SentMessage` with `ok`, `ts`, `channel` and the raw
`response`.

Unlike every other method on this client it raises rather than swallowing, and it
raises on *every* failure including Slack's own rejections: `slack_sdk` validates
each response, so `{"ok": false, ...}` arrives as a `SlackApiError` carrying the
error code, status and retry headers on `err.response`. A returned `SentMessage`
therefore always succeeded. Handle failure with `except`, not with `if sent:`,
and catch broadly rather than only `SlackApiError`:

```python
try:
    sent = slack.post_message(channel="#support", text="New support ticket")
except SlackApiError as e:
    log.error("Slack rejected the post: %s", e.response["error"])
    raise
except Exception:
    log.exception("Slack post failed before Slack answered")
    raise
```

The same `ts` addresses `update_message` and `add_reaction`:

```python
sent = slack.post_message(channel="#deployments", text="Deploy started")
slack.update_message(channel=sent.channel, ts=sent.ts, text="Deploy finished")
slack.add_reaction(channel=sent.channel, ts=sent.ts, emoji="white_check_mark")
```

Pass `sent.channel`, not the value you posted to. `chat.postMessage` accepts a
channel name, while `chat.update` and `reactions.add` want the id, and a post
addressed to a user id resolves to a DM channel with a different id again. Both
follow-up calls swallow their failures, so getting this wrong is silent.

### Direct Messages

```python
# Send to user by user ID
slack.send_message(
    channel="@U1234ABCD",  # Slack user ID
    text="Your report is ready"
)

# Send to a user found by email. This client does not look users up, so
# go through the underlying Slack SDK for that part.
from slack_sdk import WebClient

lookup = WebClient(token=bot_token).users_lookupByEmail(email="user@example.com")
slack.send_message(
    channel=lookup["user"]["id"],
    text="Direct message content"
)
```

A post addressed to a user resolves to a DM channel with an id of its own, so
keep `sent.channel` from `post_message` if you intend to thread, edit or react.

## Rich Message Formatting

### Using SlackBlockBuilder

```python
from nui_shared_utils import SlackClient, SlackBlockBuilder

slack = SlackClient()
builder = SlackBlockBuilder()

# Build message blocks
blocks = (builder
    .add_header("Deployment Status", emoji=":rocket:")
    .add_context("Production deployment completed at 14:30 NZDT")
    .add_divider()
    .add_section("Summary", "All services deployed successfully")
    .add_fields([
        ("Services", "3"),
        ("Duration", "5 minutes"),
        ("Status", ":white_check_mark: Success")
    ])
    .build()
)

# Send formatted message
slack.send_message(channel="#deployments", blocks=blocks)
```

### Block Types

#### Header Block

```python
builder.add_header("Title Text", emoji=":tada:")
```

#### Section Block

```python
# Simple section
builder.add_section("Section Title", "Section content goes here")

# Section with markdown
builder.add_section(
    "Error Details",
    "*Function*: `process_orders`\n*Error*: `Connection timeout`"
)
```

#### Fields Block

```python
# Multiple fields in columns
builder.add_fields([
    ("Label 1", "Value 1"),
    ("Label 2", "Value 2"),
    ("Label 3", "Value 3"),
])
```

#### Context Block

```python
# Smaller, lighter text
builder.add_context("Last updated: 2025-01-17 14:30 NZDT")
```

#### Divider

```python
# Visual separator
builder.add_divider()
```

## Advanced Examples

### Lambda Execution Report

```python
import nui_shared_utils as nui
from datetime import datetime

def send_execution_report(event, context, results):
    """Send detailed Lambda execution report to Slack."""
    slack = nui.SlackClient()
    builder = nui.SlackBlockBuilder()

    # Determine status emoji
    status_emoji = ":white_check_mark:" if results['success'] else ":x:"

    blocks = (builder
        .add_header(f"{status_emoji} Lambda Execution Report")
        .add_context(f"Function: {context.function_name} | Request ID: {context.request_id}")
        .add_divider()
        .add_fields([
            ("Records Processed", str(results['processed'])),
            ("Errors", str(results['errors'])),
            ("Execution Time", f"{results['duration']:.2f}ms"),
            ("Memory Used", f"{results['memory_mb']}MB")
        ])
        .add_section("Details", results.get('details', 'No additional details'))
        .build()
    )

    slack.send_message(
        channel="#lambda-executions",
        blocks=blocks
    )
```

### Error Alerts with Details

```python
import traceback
import nui_shared_utils as nui

def send_error_alert(error: Exception, context: dict):
    """Send detailed error alert to Slack."""
    slack = nui.SlackClient()
    builder = nui.SlackBlockBuilder()

    error_details = "".join(traceback.format_exception(
        type(error), error, error.__traceback__
    ))

    blocks = (builder
        .add_header(":rotating_light: Lambda Error", emoji=":rotating_light:")
        .add_context(f"Occurred at {nui.format_nz_time()}")
        .add_divider()
        .add_fields([
            ("Function", context.get('function_name', 'Unknown')),
            ("Error Type", type(error).__name__),
            ("Environment", context.get('environment', 'Unknown'))
        ])
        .add_section("Error Message", f"```{str(error)}```")
        .build()
    )

    # Send to alerts channel
    sent = slack.post_message(
        channel="#alerts-critical",
        text="Critical error",
        blocks=blocks
    )

    # Add stack trace in thread to avoid clutter
    slack.send_thread_reply(
        channel=sent.channel,
        thread_ts=sent.ts,
        text=f"```{error_details}```"
    )
```

### Data Processing Summary

```python
import nui_shared_utils as nui

def send_processing_summary(stats: dict):
    """Send data processing summary with metrics."""
    slack = nui.SlackClient()
    builder = nui.SlackBlockBuilder()

    success_rate = (stats['successful'] / stats['total']) * 100

    blocks = (builder
        .add_header("Data Processing Complete", emoji=":bar_chart:")
        .add_context(f"Batch processed at {nui.format_nz_time()}")
        .add_divider()
        .add_fields([
            ("Total Records", nui.format_number(stats['total'])),
            ("Successful", nui.format_number(stats['successful'])),
            ("Failed", nui.format_number(stats['failed'])),
            ("Success Rate", nui.format_percentage(success_rate))
        ])
        .add_section(
            "Performance",
            f"Processed {stats['records_per_second']:.1f} records/sec"
        )
        .build()
    )

    slack.send_message(channel="#data-pipeline", blocks=blocks)
```

## File Uploads

The client uploads content, not paths: read the file yourself and pass the bytes
or text.

```python
from nui_shared_utils import SlackClient

slack = SlackClient()

# Upload file content
csv_content = "name,value\nItem 1,100\nItem 2,200"
slack.send_file(
    channel="#reports",
    content=csv_content,
    filename="sales_summary.csv",
    title="Sales Summary"
)

# Upload from a path by reading it first
with open("/tmp/report.csv", "rb") as fh:
    slack.send_file(
        channel="#reports",
        content=fh.read(),
        filename="report.csv",
        title="Daily Sales Report"
    )
```

Pass `thread_ts` to upload into a thread, and use `post_file` when you need the
upload's response back or want a failure to raise rather than return `False`:

```python
sent = slack.post_message(channel="#reports", text="Tender results attached")
response = slack.post_file(
    channel=sent.channel,
    content=pdf_bytes,
    filename="tender.pdf",
    thread_ts=sent.ts
)
file_id = response["files"][0]["id"]
```

## Channel Management

`SlackClient` sends; it does not enumerate or look up channels. Use the Slack SDK
directly for that, with the same bot token:

```python
from slack_sdk import WebClient

web = WebClient(token=bot_token)

# List channels
for channel in web.conversations_list()["channels"]:
    print(f"{channel['name']}: {channel['id']}")

# Channel info by id
info = web.conversations_info(channel="C1234567890")["channel"]
```

To create channels from a YAML definition, see the `slack-channel-setup` CLI
shipped with this package.

## Error Handling

### Retry Logic

```python
from nui_shared_utils import with_retry, SlackClient

@with_retry(max_attempts=3, backoff_factor=2)
def send_critical_alert(message: str):
    """Send message with automatic retry on failure."""
    slack = SlackClient()
    # post_message, not send_message: `with_retry` retries a raised exception,
    # and send_message swallows its errors into a False the retry never sees.
    slack.post_message(channel="#alerts", text=message)
```

### Graceful Degradation

```python
from nui_shared_utils import SlackClient

def notify_with_fallback(message: str):
    """Try Slack notification with fallback to logging."""
    slack = SlackClient()
    if not slack.send_message(channel="#notifications", text=message):
        # Fallback to CloudWatch logs. send_message reports failure by
        # returning False, so testing the return is what catches it; wrapping
        # this call in try/except would give you an except block that can
        # never run.
        print(f"Slack notification failed, message content: {message}")
```

Use `post_message` instead when you want the reason rather than just the fact:

```python
def notify_with_fallback(message: str):
    try:
        SlackClient().post_message(channel="#notifications", text=message)
    except Exception as e:
        print(f"Slack notification failed: {e}")
        print(f"Message content: {message}")
```

## Best Practices

### 1. Use Environment-Based Configuration

```python
import os
import nui_shared_utils as nui

stage = os.environ.get('STAGE', 'dev')
nui.configure(slack_credentials_secret=f"{stage}/slack")
```

### 2. Channel Naming Conventions

- `#alerts-critical` - Production issues requiring immediate attention
- `#alerts` - General alerts and warnings
- `#deployments` - Deployment notifications
- `#data-pipeline` - Data processing updates
- `#lambda-executions` - Lambda execution reports

### 3. Rate Limiting Awareness

Slack has rate limits:

- ~1 message per second per channel
- Burst allowance available
- Use threading for related messages

```python
# Good: Use threading for related messages
sent = slack.post_message(channel="#support", text="Main message")
slack.send_thread_reply(channel=sent.channel, thread_ts=sent.ts, text="Details")

# Avoid: Flooding channel with sequential messages
for item in items:  # Could hit rate limit
    slack.send_message(channel="#notifications", text=item)
```

### 4. Message Formatting

- Use emoji for visual cues (`:white_check_mark:`, `:x:`, `:warning:`)
- Format code with backticks: `` `function_name` ``
- Use code blocks for stack traces: ``` ```error details``` ```
- Keep messages concise and scannable

### 5. Testing

```python
# Mock Slack client for testing
from unittest.mock import Mock, patch

@patch('nui_shared_utils.SlackClient')
def test_notification_logic(mock_slack):
    """Test notification without actually sending to Slack."""
    mock_client = Mock()
    mock_slack.return_value = mock_client

    # Your code that uses SlackClient
    send_notification("test message")

    # Verify it was called correctly
    mock_client.send_message.assert_called_once_with(
        channel="#test",
        text="test message"
    )
```

## Troubleshooting

### Common Issues

#### Authentication Errors

**Error:** `invalid_auth`

**Solutions:**

- Verify bot token in AWS Secrets Manager
- Check token starts with `xoxb-`
- Ensure bot is installed to workspace

#### Channel Not Found

**Error:** `channel_not_found`

**Solutions:**

- Verify channel name (include `#` for public channels)
- Check bot has been invited to private channels
- Use channel ID instead of name

#### Missing Scopes

**Error:** `missing_scope`

**Solutions:**

- Add required OAuth scopes in Slack app settings
- Reinstall app to workspace after adding scopes
- Common scopes: `chat:write`, `files:write`, `channels:read`

## Resources

- [Slack Block Kit Builder](https://app.slack.com/block-kit-builder/) - Visual block designer
- [Slack API Documentation](https://api.slack.com/)
- [Slack Emoji Reference](https://www.webfx.com/tools/emoji-cheat-sheet/)

---

*For more examples, see [Quick Start Guide](../getting-started/quickstart.md)*
