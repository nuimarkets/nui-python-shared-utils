"""
Tests for slack_client module.
"""

import pytest

pytestmark = pytest.mark.unit

from unittest.mock import patch, Mock
from slack_sdk.errors import SlackApiError
from nui_shared_utils.slack_client import SlackClient, SentMessage, DEFAULT_ACCOUNT_NAMES
import os


class TestSlackClient:
    """Tests for SlackClient class."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": ""}, clear=False)
    def test_init_required_secret(self, mock_webclient, mock_get_secret):
        """Test initialization requires secret name parameter."""
        os.environ.pop("SLACK_BOT_TOKEN", None)
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}

        client = SlackClient(secret_name="test-secret")

        mock_get_secret.assert_called_once_with("test-secret")
        mock_webclient.assert_called_once_with(token="xoxb-test-token")

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": ""}, clear=False)
    def test_init_custom_secret(self, mock_webclient, mock_get_secret):
        """Test initialization with custom secret name."""
        os.environ.pop("SLACK_BOT_TOKEN", None)
        mock_get_secret.return_value = {"bot_token": "xoxb-custom-token"}

        client = SlackClient(secret_name="custom-slack-secret")

        mock_get_secret.assert_called_once_with("custom-slack-secret")
        mock_webclient.assert_called_once_with(token="xoxb-custom-token")

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {}, clear=True)
    def test_init_with_default_secret_name(self, mock_webclient, mock_get_secret):
        """Test initialization with default secret name."""
        mock_get_secret.return_value = {"bot_token": "xoxb-default-token"}
        
        with patch("nui_shared_utils.base_client.resolve_config_value") as mock_resolve:
            mock_resolve.return_value = "slack-credentials"
            
            client = SlackClient()
        
        mock_get_secret.assert_called_once_with("slack-credentials")
        mock_webclient.assert_called_once_with(token="xoxb-default-token")


class TestSendMessage:
    """Tests for send_message method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_success(self, mock_webclient, mock_get_secret):
        """Test successful message sending."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message", include_lambda_header=False)

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(channel="C123", text="Test message", blocks=None, attachments=None)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_with_blocks(self, mock_webclient, mock_get_secret):
        """Test sending message with blocks."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Test block"}}]

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Fallback text", blocks=blocks, include_lambda_header=False)

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(channel="C123", text="Fallback text", blocks=blocks, attachments=None)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_with_attachments(self, mock_webclient, mock_get_secret):
        """Test sending message with legacy attachments (color sidebars)."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        attachments = [{"color": "#36a64f", "text": "Green sidebar message"}]

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Fallback", attachments=attachments, include_lambda_header=False)

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="Fallback", blocks=None, attachments=attachments
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_forwards_unfurl_flags(self, mock_webclient, mock_get_secret):
        """unfurl_links / unfurl_media are forwarded to chat_postMessage when set."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message(
            "C123",
            "Test message",
            include_lambda_header=False,
            unfurl_links=False,
            unfurl_media=False,
        )

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123",
            text="Test message",
            blocks=None,
            attachments=None,
            unfurl_links=False,
            unfurl_media=False,
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_omits_unfurl_when_unset(self, mock_webclient, mock_get_secret):
        """When the unfurl flags are unset (default None), they are NOT forwarded, so
        Slack's default behavior is preserved for every existing caller."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message", include_lambda_header=False)

        assert result is True
        _, kwargs = mock_client.chat_postMessage.call_args
        assert "unfurl_links" not in kwargs
        assert "unfurl_media" not in kwargs

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_api_error(self, mock_webclient, mock_get_secret):
        """Test handling of Slack API error."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.side_effect = SlackApiError(
            message="channel_not_found", response={"error": "channel_not_found"}
        )

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message")

        assert result is False

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_not_ok(self, mock_webclient, mock_get_secret):
        """Test handling of not ok response."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": False, "error": "invalid_auth"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message")

        assert result is False

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_message_unexpected_error(self, mock_webclient, mock_get_secret):
        """Test handling of unexpected errors."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.side_effect = Exception("Network error")

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message")

        assert result is False


class TestPostMessage:
    """Tests for post_message, which returns Slack's response instead of a bool."""

    @staticmethod
    def _client(mock_webclient, mock_get_secret, response=None):
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = response or {
            "ok": True,
            "ts": "1234567890.123456",
            "channel": "C999",
        }
        return SlackClient(secret_name="test-secret"), mock_client

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_returns_ts_and_channel(self, mock_webclient, mock_get_secret):
        """The ts is the whole point: send_message drops it, post_message keeps it."""
        slack, _ = self._client(mock_webclient, mock_get_secret)

        sent = slack.post_message("C123", "Test message", include_lambda_header=False)

        assert isinstance(sent, SentMessage)
        assert sent.ok is True
        assert sent.ts == "1234567890.123456"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_channel_comes_from_the_response_not_the_request(self, mock_webclient, mock_get_secret):
        """Slack resolves a name or user ID to a different channel id than the one sent."""
        slack, _ = self._client(
            mock_webclient, mock_get_secret, response={"ok": True, "ts": "1.1", "channel": "D0RESOLVED"}
        )

        sent = slack.post_message("@someone", "Test message", include_lambda_header=False)

        assert sent.channel == "D0RESOLVED"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_exposes_raw_response(self, mock_webclient, mock_get_secret):
        """Fields SentMessage does not name stay reachable."""
        response = {"ok": True, "ts": "1.1", "channel": "C999", "message": {"bot_id": "B1"}}
        slack, _ = self._client(mock_webclient, mock_get_secret, response=response)

        sent = slack.post_message("C123", "Test message", include_lambda_header=False)

        assert sent.response is response

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_not_ok_is_returned_not_raised(self, mock_webclient, mock_get_secret):
        """A response Slack answered but rejected comes back as ok=False."""
        slack, _ = self._client(mock_webclient, mock_get_secret, response={"ok": False, "error": "invalid_auth"})

        sent = slack.post_message("C123", "Test message", include_lambda_header=False)

        assert sent.ok is False
        assert sent.ts is None
        assert bool(sent) is False

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_api_error_propagates(self, mock_webclient, mock_get_secret):
        """Unlike send_message, post_message does not swallow the error.

        A caller that wants the ts usually wants to retry on a retryable
        SlackApiError, which a bare False cannot distinguish from a rejection.
        """
        slack, mock_client = self._client(mock_webclient, mock_get_secret)
        mock_client.chat_postMessage.side_effect = SlackApiError(
            message="ratelimited", response={"error": "ratelimited"}
        )

        with pytest.raises(SlackApiError):
            slack.post_message("C123", "Test message", include_lambda_header=False)

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_unexpected_error_propagates(self, mock_webclient, mock_get_secret):
        """Non-Slack exceptions are not swallowed either."""
        slack, mock_client = self._client(mock_webclient, mock_get_secret)
        mock_client.chat_postMessage.side_effect = Exception("Network error")

        with pytest.raises(Exception, match="Network error"):
            slack.post_message("C123", "Test message", include_lambda_header=False)

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_includes_the_standard_header(self, mock_webclient, mock_get_secret, mock_boto3):
        """Header parity with send_message is why consumers can stop hand-rolling it."""
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        slack.post_message(
            "C123", "Test message", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Original block"}}]
        )

        blocks = mock_client.chat_postMessage.call_args.kwargs["blocks"]
        assert len(blocks) == 2
        assert blocks[0]["type"] == "context"
        assert blocks[1]["text"]["text"] == "Original block"

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_header_can_be_disabled(self, mock_webclient, mock_get_secret, mock_boto3):
        """cream-sync posts its first trade notification without a header."""
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        slack.post_message("C123", "Test message", include_lambda_header=False)

        assert mock_client.chat_postMessage.call_args.kwargs["blocks"] is None

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_forwards_unfurl_flags(self, mock_webclient, mock_get_secret):
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        slack.post_message("C123", "Test message", include_lambda_header=False, unfurl_links=False, unfurl_media=False)

        kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert kwargs["unfurl_links"] is False
        assert kwargs["unfurl_media"] is False

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_omits_unfurl_when_unset(self, mock_webclient, mock_get_secret):
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        slack.post_message("C123", "Test message", include_lambda_header=False)

        kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "unfurl_links" not in kwargs
        assert "unfurl_media" not in kwargs

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_payload_matches_send_message_exactly(self, mock_webclient, mock_get_secret, mock_boto3):
        """The two methods differ in what they return, in nothing else.

        This is the test that fails if a later change touches one path and not
        the other, which is the drift that made consumers hand-roll the call in
        the first place.
        """
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        args = dict(
            channel="C123",
            text="Test message",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Body"}}],
            attachments=[{"color": "#ff0000", "text": "Attached"}],
            event_type="Scheduled",
            unfurl_links=False,
        )

        slack.send_message(**args)
        send_kwargs = mock_client.chat_postMessage.call_args.kwargs

        slack.post_message(**args)
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs

        assert post_kwargs == send_kwargs

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_ts_round_trips_into_a_thread_reply(self, mock_webclient, mock_get_secret):
        """The loop the bool return left open: post, then reply under it."""
        slack, mock_client = self._client(mock_webclient, mock_get_secret)

        sent = slack.post_message("C123", "Parent", include_lambda_header=False)
        assert slack.send_thread_reply("C123", sent.ts, "Reply") is True

        assert mock_client.chat_postMessage.call_args.kwargs["thread_ts"] == "1234567890.123456"


class TestSentMessage:
    """Tests for the SentMessage result type."""

    def test_truthiness_follows_ok(self):
        assert bool(SentMessage(ok=True, ts="1.1")) is True
        assert bool(SentMessage(ok=False)) is False

    def test_defaults(self):
        sent = SentMessage(ok=True)
        assert sent.ts is None
        assert sent.channel is None
        assert sent.response is None

    def test_is_frozen(self):
        sent = SentMessage(ok=True, ts="1.1")
        with pytest.raises(Exception):
            sent.ts = "2.2"


class TestSendFile:
    """Tests for send_file method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_file_success(self, mock_webclient, mock_get_secret):
        """Test successful file upload."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.files_upload_v2.return_value = {"ok": True, "file": {"id": "F123"}}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_file("C123", "File content", "test.txt", "Test File")

        assert result is True
        mock_client.files_upload_v2.assert_called_once_with(
            channel="C123", content="File content", filename="test.txt", title="Test File"
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_file_default_title(self, mock_webclient, mock_get_secret):
        """Test file upload with default title."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.files_upload_v2.return_value = {"ok": True, "file": {"id": "F123"}}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_file("C123", "File content", "report.csv")

        assert result is True
        mock_client.files_upload_v2.assert_called_once_with(
            channel="C123", content="File content", filename="report.csv", title="report.csv"
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_file_api_error(self, mock_webclient, mock_get_secret):
        """Test handling of file upload API error."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.files_upload_v2.side_effect = SlackApiError(
            message="file_upload_error", response={"error": "file_upload_error"}
        )

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_file("C123", "Content", "file.txt")

        assert result is False


class TestSendThreadReply:
    """Tests for send_thread_reply method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_thread_reply_success(self, mock_webclient, mock_get_secret):
        """Test successful thread reply."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123457"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_thread_reply("C123", "1234567890.123456", "Reply text")

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123", thread_ts="1234567890.123456", text="Reply text", blocks=None
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_send_thread_reply_with_blocks(self, mock_webclient, mock_get_secret):
        """Test thread reply with blocks."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123457"}

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Reply"}}]

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_thread_reply("C123", "1234567890.123456", "Reply", blocks)

        assert result is True
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123", thread_ts="1234567890.123456", text="Reply", blocks=blocks
        )


class TestUpdateMessage:
    """Tests for update_message method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_update_message_success(self, mock_webclient, mock_get_secret):
        """Test successful message update."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_update.return_value = {"ok": True, "ts": "1234567890.123456"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.update_message("C123", "1234567890.123456", "Updated text")

        assert result is True
        mock_client.chat_update.assert_called_once_with(
            channel="C123", ts="1234567890.123456", text="Updated text", blocks=None
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_update_message_api_error(self, mock_webclient, mock_get_secret):
        """Test handling of update API error."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_update.side_effect = SlackApiError(
            message="message_not_found", response={"error": "message_not_found"}
        )

        slack = SlackClient(secret_name="test-secret")
        result = slack.update_message("C123", "1234567890.123456", "Updated")

        assert result is False


class TestAddReaction:
    """Tests for add_reaction method."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_add_reaction_success(self, mock_webclient, mock_get_secret):
        """Test successful reaction addition."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.reactions_add.return_value = {"ok": True}

        slack = SlackClient(secret_name="test-secret")
        result = slack.add_reaction("C123", "1234567890.123456", "thumbsup")

        assert result is True
        mock_client.reactions_add.assert_called_once_with(
            channel="C123", timestamp="1234567890.123456", name="thumbsup"
        )

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_add_reaction_already_exists(self, mock_webclient, mock_get_secret):
        """Test handling when reaction already exists."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.reactions_add.side_effect = SlackApiError(
            message="already_reacted", response={"error": "already_reacted"}
        )

        slack = SlackClient(secret_name="test-secret")
        result = slack.add_reaction("C123", "1234567890.123456", "thumbsup")

        assert result is True  # Should return True for already_reacted

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_add_reaction_api_error(self, mock_webclient, mock_get_secret):
        """Test handling of reaction API error."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.reactions_add.side_effect = SlackApiError(
            message="invalid_name", response={"error": "invalid_name"}
        )

        slack = SlackClient(secret_name="test-secret")
        result = slack.add_reaction("C123", "1234567890.123456", "invalid")

        assert result is False


class TestLambdaContextHeader:
    """Tests for Lambda context header functionality."""

    @staticmethod
    def assert_header_format(header_text: str, expected_parts: dict):
        """
        Helper to assert header format with nice error messages.

        Args:
            header_text: Full header text from Slack block
            expected_parts: Dict of expected values to check

        Shows the full header on assertion failure for easy debugging.
        """
        failure_msg = f"\n\nFull header text:\n{header_text}\n\nExpected to contain: {expected_parts}\n"

        for key, value in expected_parts.items():
            assert value in header_text, f"{failure_msg}Missing {key}: {value}"

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "test-function",
            "AWS_LAMBDA_FUNCTION_VERSION": "$LATEST",
            "AWS_LAMBDA_LOG_GROUP_NAME": "/aws/lambda/test-function",
            "AWS_LAMBDA_LOG_STREAM_NAME": "2023/11/15/[$LATEST]abc123",
            "AWS_REGION": "eu-west-1",
            "STAGE": "prod",
            "AWS_EXECUTION_ENV": "AWS_Lambda_python3.9",
        },
    )
    def test_lambda_header_connect_production(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test Lambda header generation for Production."""
        # Setup mocks
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock Lambda client for deployment time
        mock_lambda = Mock()
        mock_boto3.return_value = mock_lambda
        mock_lambda.get_function.return_value = {"Configuration": {"LastModified": "2023-11-15T10:30:45.123+0000"}}

        # Mock config file check
        with patch("os.path.exists") as mock_exists:
            mock_exists.return_value = True  # .lambda-deploy.yml exists

            slack = SlackClient(secret_name="test-secret")

            # Simulate Lambda handler providing context with ARN
            mock_context = Mock()
            mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:test-function"
            slack.set_handler_context(mock_context)

            result = slack.send_message("C123", "Test message")

            # Verify the header was included
            call_args = mock_client.chat_postMessage.call_args
            blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

            assert blocks is not None
            assert len(blocks) >= 1  # At least the context header

            # Check header content - it's now a context block
            header_block = blocks[0]
            assert header_block["type"] == "context"
            header_text = header_block["elements"][0]["text"]

            # Use helper to check all expected parts
            self.assert_header_format(header_text, {
                "robot emoji": "🤖",
                "function name": "test-function",
                "account name": "Production",
                "account ID": "(123456789012)",
                "region": "eu-west-1",
                "log emoji": "📋",
                "log group": "`/aws/lambda/test-function`"
            })

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "nui-service",
            "AWS_REGION": "ap-southeast-2",
            "STAGE": "dev",
        },
    )
    def test_lambda_header_nui_development(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test Lambda header generation for Development."""
        # Setup mocks
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock Lambda client - deployment time fails
        mock_lambda = Mock()
        mock_boto3.return_value = mock_lambda
        mock_lambda.get_function.side_effect = Exception("Access denied")

        # Mock config file check
        with patch("os.path.exists") as mock_exists:
            mock_exists.side_effect = lambda path: path.endswith("serverless.yml")

            slack = SlackClient(secret_name="test-secret")

            # Simulate Lambda handler providing context with ARN
            mock_context = Mock()
            mock_context.invoked_function_arn = "arn:aws:lambda:ap-southeast-2:234567890123:function:nui-service"
            slack.set_handler_context(mock_context)

            result = slack.send_message("C123", "Test message")

            # Verify the header was included
            call_args = mock_client.chat_postMessage.call_args
            blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

            assert blocks is not None

            # Check header content - it's now a context block
            header_block = blocks[0]
            assert header_block["type"] == "context"
            header_text = header_block["elements"][0]["text"]

            # Use helper to check all expected parts
            self.assert_header_format(header_text, {
                "robot emoji": "🤖",
                "function name": "nui-service",
                "account name": "Development",
                "account ID": "(234567890123)",
                "region": "ap-southeast-2",
                "log emoji": "📋"
            })

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "connect-prod-long-service-name-12345",
            "AWS_REGION": "eu-west-1",
            "STAGE": "prod",
        },
    )
    def test_custom_service_name_in_header(self, mock_webclient, mock_get_secret, mock_boto3):
        """Service name parameter overrides function name in header."""
        # Setup mocks
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock Lambda client
        mock_lambda = Mock()
        mock_boto3.return_value = mock_lambda
        mock_lambda.get_function.return_value = {
            "Configuration": {"FunctionName": "connect-prod-long-service-name-12345"}
        }

        # Mock config file check
        with patch("os.path.exists") as mock_exists:
            mock_exists.return_value = False

            # Create client with custom service name
            slack = SlackClient(
                secret_name="test-secret",
                service_name="my-service",
                account_names={"123456789012": "Production"}
            )

            # Simulate Lambda handler providing context with ARN
            mock_context = Mock()
            mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:connect-prod-long-service-name-12345"
            slack.set_handler_context(mock_context)

            result = slack.send_message("C123", "Test message")

            # Verify the header was included
            call_args = mock_client.chat_postMessage.call_args
            blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

            assert blocks is not None
            header_block = blocks[0]
            assert header_block["type"] == "context"
            header_text = header_block["elements"][0]["text"]

            # Should show custom service name, not full function name
            assert "my-service" in header_text
            assert "connect-prod-long-service-name-12345" not in header_text

            # Other header elements should still be present
            self.assert_header_format(header_text, {
                "robot emoji": "🤖",
                "function name": "my-service",  # Custom name
                "account name": "Production",
                "account ID": "(123456789012)",
                "region": "eu-west-1",
                "log emoji": "📋"
            })

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "test-function",
            "AWS_REGION": "eu-west-1",
            "STAGE": "prod",
        },
    )
    def test_missing_service_name_uses_function_name(self, mock_webclient, mock_get_secret, mock_boto3):
        """Without service_name, falls back to function name."""
        # Setup mocks
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock Lambda client
        mock_lambda = Mock()
        mock_boto3.return_value = mock_lambda
        mock_lambda.get_function.return_value = {
            "Configuration": {"FunctionName": "test-function"}
        }

        # Mock config file check
        with patch("os.path.exists") as mock_exists:
            mock_exists.return_value = False

            # Create client WITHOUT custom service name
            slack = SlackClient(
                secret_name="test-secret",
                account_names={"123456789012": "Production"}
            )

            # Simulate Lambda handler providing context with ARN
            mock_context = Mock()
            mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:test-function"
            slack.set_handler_context(mock_context)

            result = slack.send_message("C123", "Test message")

            # Verify the header was included
            call_args = mock_client.chat_postMessage.call_args
            blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

            assert blocks is not None
            header_block = blocks[0]
            assert header_block["type"] == "context"
            header_text = header_block["elements"][0]["text"]

            # Should show full function name
            assert "test-function" in header_text

            # All header elements should be present
            self.assert_header_format(header_text, {
                "robot emoji": "🤖",
                "function name": "test-function",  # Original function name
                "account name": "Production",
                "account ID": "(123456789012)",
                "region": "eu-west-1",
                "log emoji": "📋"
            })

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {})  # No Lambda environment variables
    def test_lambda_header_not_in_lambda(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that Lambda header is not included when not running in Lambda."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock STS to simulate that we can't get account info when not in Lambda
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.side_effect = Exception("Not authenticated")

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message(
            "C123", "Test message", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Original block"}}]
        )

        # Verify local header was added (not Lambda header)
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

        assert len(blocks) == 2  # Local header + original block
        assert blocks[0]["type"] == "context"  # First block is header
        assert blocks[1]["text"]["text"] == "Original block"  # Second block is original

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_lambda_header_can_be_disabled(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that Lambda header can be disabled."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock STS
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_message("C123", "Test message", include_lambda_header=False)

        # Verify no header was added
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

        assert blocks is None  # No blocks added

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_thread_reply_no_header_by_default(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that thread replies don't include header by default."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Mock STS
        mock_sts = Mock()
        mock_boto3.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        slack = SlackClient(secret_name="test-secret")
        result = slack.send_thread_reply("C123", "1234567890.123456", "Reply")

        # Verify no header was added
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))

        assert blocks is None

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function", "STAGE": "prod"})
    def test_unknown_account_id_handling(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test handling of unknown AWS account IDs."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        slack = SlackClient(secret_name="test-secret")

        # Simulate Lambda handler providing context with unknown account
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:999999999999:function:test-function"
        slack.set_handler_context(mock_context)

        result = slack.send_message("C123", "Test message")

        # Check header contains unknown account info
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))
        header_block = blocks[0]
        header_text = header_block["elements"][0]["text"]

        assert "Unknown (999999999999)" in header_text

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {"AWS_LAMBDA_FUNCTION_NAME": "dev-test-function", "STAGE": "dev", "AWS_REGION": "eu-west-1"},  # Dev stage
    )
    def test_stage_mismatch_shown_in_header(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that header shows account name regardless of stage env var."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        slack = SlackClient(secret_name="test-secret")

        # Simulate Lambda handler providing context with Production account
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:dev-test-function"
        slack.set_handler_context(mock_context)

        result = slack.send_message("C123", "Test message")

        # Check header shows account name only (not stage)
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))
        header_block = blocks[0]
        header_text = header_block["elements"][0]["text"]

        # Shared library doesn't interpret stage - just shows account name
        assert "dev-test-function" in header_text
        assert "(dev)" not in header_text  # No stage suffix
        assert "Production" in header_text


class TestAccountNameConsistency:
    """Tests to ensure account name mappings are consistent across functions."""

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {})  # No Lambda environment variables
    def test_centralized_account_mappings(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that both lambda context and local header use the same account mappings."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        slack = SlackClient(secret_name="test-secret")

        # Simulate handler providing Development account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:ap-southeast-2:234567890123:function:test"
        slack.set_handler_context(mock_context)

        # Verify lambda context uses centralized mapping
        assert slack._lambda_context["aws_account_name"] == "Development"

        # Send message to trigger local header creation (since no Lambda env vars)
        slack.send_message("C123", "Test message")

        # Verify local header also uses centralized mapping
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))
        header_text = blocks[0]["elements"][0]["text"]

        assert "Development" in header_text

    def test_default_account_names_constant(self):
        """Test that DEFAULT_ACCOUNT_NAMES constant contains only example mappings."""
        expected_accounts = {
            "123456789012": "Production",
            "234567890123": "Development",
            "345678901234": "Staging",
        }

        assert DEFAULT_ACCOUNT_NAMES == expected_accounts

        # Verify example account IDs are present
        assert DEFAULT_ACCOUNT_NAMES["123456789012"] == "Production"
        assert DEFAULT_ACCOUNT_NAMES["234567890123"] == "Development"
        assert DEFAULT_ACCOUNT_NAMES["345678901234"] == "Staging"

        # Verify no real account IDs are hardcoded
        assert "043836023178" not in DEFAULT_ACCOUNT_NAMES
        assert "036220212417" not in DEFAULT_ACCOUNT_NAMES

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "custom-function",
            "AWS_LAMBDA_LOG_GROUP_NAME": "/aws/lambda/custom-function",
            "AWS_REGION": "us-east-1",
        },
    )
    def test_custom_account_names_via_dict(self, mock_webclient, mock_get_secret, mock_boto3):
        """Test that clients can provide custom account name mappings via dict."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Create client with custom account names
        custom_accounts = {"999888777666": "MyCustomAccount"}
        slack = SlackClient(secret_name="test-secret", account_names=custom_accounts)

        # Simulate Lambda handler providing context with ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:us-east-1:999888777666:function:custom-function"
        slack.set_handler_context(mock_context)

        result = slack.send_message("C123", "Test message")
        assert result is True

        # Verify custom account name is used
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))
        header_text = blocks[0]["elements"][0]["text"]

        assert "MyCustomAccount" in header_text
        assert "(999888777666)" in header_text

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(
        os.environ,
        {
            "AWS_LAMBDA_FUNCTION_NAME": "yaml-config-function",
            "AWS_LAMBDA_LOG_GROUP_NAME": "/aws/lambda/yaml-config-function",
            "AWS_REGION": "eu-central-1",
        },
    )
    def test_custom_account_names_via_yaml(self, mock_webclient, mock_get_secret, mock_boto3, tmp_path):
        """Test that clients can provide custom account name mappings via YAML config."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_client = Mock()
        mock_webclient.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "123"}

        # Create YAML config file
        config_file = tmp_path / "slack_config.yaml"
        config_file.write_text("""
account_names:
  "111222333444": "YAMLConfiguredAccount"
  "555666777888": "AnotherAccount"
""")

        # Create client with config file
        slack = SlackClient(secret_name="test-secret", account_names_config=str(config_file))

        # Simulate Lambda handler providing context with ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-central-1:111222333444:function:yaml-config-function"
        slack.set_handler_context(mock_context)

        result = slack.send_message("C123", "Test message")
        assert result is True

        # Verify YAML account name is used
        call_args = mock_client.chat_postMessage.call_args
        blocks = call_args.kwargs.get("blocks", call_args[1].get("blocks"))
        header_text = blocks[0]["elements"][0]["text"]

        assert "YAMLConfiguredAccount" in header_text
        assert "(111222333444)" in header_text

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function"})
    def test_malformed_yaml_fallback_to_defaults(self, mock_webclient, mock_get_secret, tmp_path):
        """Test that malformed YAML falls back to defaults gracefully."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_webclient.return_value = Mock()

        # Create malformed YAML file
        config_file = tmp_path / "bad_config.yaml"
        config_file.write_text("account_names: [this, is, not, a, dict]")

        # Should initialize without error and use defaults
        slack = SlackClient(secret_name="test-secret", account_names_config=str(config_file))

        # Simulate handler providing Production account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:test-function"
        slack.set_handler_context(mock_context)

        # Should fall back to default account name
        assert slack._lambda_context["aws_account_name"] == "Production"  # From defaults

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function"})
    def test_empty_yaml_file_fallback(self, mock_webclient, mock_get_secret, tmp_path):
        """Test that empty YAML file falls back to defaults."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_webclient.return_value = Mock()

        # Create empty YAML file
        config_file = tmp_path / "empty_config.yaml"
        config_file.write_text("")

        slack = SlackClient(secret_name="test-secret", account_names_config=str(config_file))

        # Simulate handler providing Development account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:ap-southeast-2:234567890123:function:test-function"
        slack.set_handler_context(mock_context)

        # Should use default account name
        assert slack._lambda_context["aws_account_name"] == "Development"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function"})
    def test_missing_yaml_file_fallback(self, mock_webclient, mock_get_secret, tmp_path):
        """Test that missing YAML file falls back to defaults."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_webclient.return_value = Mock()

        # Reference non-existent file
        config_file = tmp_path / "nonexistent.yaml"

        slack = SlackClient(secret_name="test-secret", account_names_config=str(config_file))

        # Simulate handler providing Staging account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:345678901234:function:test-function"
        slack.set_handler_context(mock_context)

        # Should use default account name
        assert slack._lambda_context["aws_account_name"] == "Staging"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function"})
    def test_invalid_account_names_type_ignored(self, mock_webclient, mock_get_secret):
        """Test that invalid account_names parameter type is ignored gracefully."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_webclient.return_value = Mock()

        # Pass invalid type (list instead of dict)
        slack = SlackClient(secret_name="test-secret", account_names=["not", "a", "dict"])

        # Simulate handler providing Production account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:test-function"
        slack.set_handler_context(mock_context)

        # Should use default account name, ignoring invalid parameter
        assert slack._lambda_context["aws_account_name"] == "Production"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "test-function"})
    def test_yaml_with_non_string_values_ignored(self, mock_webclient, mock_get_secret, tmp_path):
        """Test that YAML with non-string values is validated and ignored."""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_webclient.return_value = Mock()

        # Create YAML with non-string values
        config_file = tmp_path / "invalid_types.yaml"
        config_file.write_text("""
account_names:
  "123456789012": 12345
  "234567890123": true
""")

        slack = SlackClient(secret_name="test-secret", account_names_config=str(config_file))

        # Simulate handler providing Production account via ARN
        mock_context = Mock()
        mock_context.invoked_function_arn = "arn:aws:lambda:eu-west-1:123456789012:function:test-function"
        slack.set_handler_context(mock_context)

        # Should fall back to defaults due to type validation failure
        assert slack._lambda_context["aws_account_name"] == "Production"

    @patch("nui_shared_utils.slack_client.create_aws_client")
    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch("nui_shared_utils.slack_client.get_lambda_environment_info")
    @patch.dict(os.environ, {})
    def test_uses_shared_lambda_environment_helper(self, mock_env_info, _mock_webclient, mock_get_secret, _mock_boto3):
        """Verify SlackClient delegates to get_lambda_environment_info"""
        mock_get_secret.return_value = {"bot_token": "xoxb-test-token"}
        mock_env_info.return_value = {
            "function_name": "test-fn",
            "function_version": "$LATEST",
            "aws_region": "ap-southeast-2",
            "environment": "unknown",
            "memory_limit": "",
            "is_local": True,
        }
        SlackClient(secret_name="test-secret")  # noqa: S106
        mock_env_info.assert_called_once()


class TestSlackCredentialResolution:
    """Tests for Slack credential resolution precedence."""

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    def test_explicit_credentials_bypass_secrets_manager(self, mock_webclient, mock_get_secret):
        """Explicit credentials dict should skip SM entirely."""
        creds = {"bot_token": "xoxb-direct-token"}
        client = SlackClient(credentials=creds)

        mock_get_secret.assert_not_called()
        mock_webclient.assert_called_once_with(token="xoxb-direct-token")
        assert client.credentials == creds

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token"}, clear=False)
    def test_env_var_bypasses_secrets_manager(self, mock_webclient, mock_get_secret):
        """SLACK_BOT_TOKEN env var should skip SM."""
        client = SlackClient()

        mock_get_secret.assert_not_called()
        assert client.credentials["bot_token"] == "xoxb-env-token"
        mock_webclient.assert_called_once_with(token="xoxb-env-token")

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/xxx"}, clear=False)
    def test_env_var_includes_optional_webhook(self, _mock_webclient, _mock_get_secret):
        """SLACK_WEBHOOK_URL is included when present."""
        client = SlackClient()

        assert client.credentials["bot_token"] == "xoxb-env-token"
        assert client.credentials["webhook_url"] == "https://hooks.slack.com/xxx"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token"}, clear=False)
    def test_explicit_credentials_win_over_env_vars(self, _mock_webclient, mock_get_secret):
        """Explicit credentials should take priority over env vars."""
        creds = {"bot_token": "xoxb-explicit-token"}
        client = SlackClient(credentials=creds)

        mock_get_secret.assert_not_called()
        assert client.credentials["bot_token"] == "xoxb-explicit-token"

    @patch("nui_shared_utils.base_client.get_secret")
    @patch("nui_shared_utils.slack_client.WebClient")
    @patch.dict("os.environ", {}, clear=False)
    def test_falls_through_to_secrets_manager(self, _mock_webclient, mock_get_secret):
        """Without explicit creds or env vars, should use SM."""
        # Ensure env vars are not set
        os.environ.pop("SLACK_BOT_TOKEN", None)
        mock_get_secret.return_value = {"bot_token": "xoxb-sm-token"}

        client = SlackClient(secret_name="test-secret")

        mock_get_secret.assert_called_once_with("test-secret")
        assert client.credentials["bot_token"] == "xoxb-sm-token"
