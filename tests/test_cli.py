"""Exercise channel setup through the CLI with Slack's WebClient mocked."""

from unittest.mock import Mock, call, patch

import pytest
from click.testing import CliRunner

from nui_shared_utils.cli import setup_channels

pytestmark = pytest.mark.unit


def test_setup_channels_configures_and_paces_channels(tmp_path):
    config = tmp_path / "channels.yaml"
    config.write_text(
        "channels:\n"
        "  - name: app-alerts\n"
        "    description: Application alerts\n"
        "    purpose: Report failures\n"
        "    topic: Production health\n"
        "    invite_users: [U123]\n"
        "  - name: deployments\n"
        "    description: Deployment notifications\n"
        '    purpose: "Shipment status"\n'
        '    topic: "Releases"\n'
    )
    output = tmp_path / "channels.env"
    client = Mock()
    client.auth_test.return_value = {"user_id": "UBOT", "user": "bot", "team": "test"}
    client.conversations_list.return_value = {"channels": []}
    client.conversations_create.side_effect = [
        {"channel": {"id": "C123"}},
        {"channel": {"id": "C456"}},
    ]
    timeline = Mock()
    timeline.attach_mock(client, "slack")

    with (
        patch("nui_shared_utils.slack_setup.channel_creator.WebClient", return_value=client),
        patch("nui_shared_utils.slack_setup.setup_helpers.WebClient", return_value=client),
        patch("nui_shared_utils.slack_setup.channel_creator.time.sleep") as sleep,
    ):
        timeline.attach_mock(sleep, "sleep")
        result = CliRunner().invoke(
            setup_channels,
            [
                "--config",
                str(config),
                "--token",
                "test-token",
                "--no-interactive",
                "--output",
                str(output),
                "--output-format",
                "env",
            ],
        )

    assert result.exit_code == 0, result.output
    assert output.read_text().splitlines() == [
        "# Environment variables for your application:",
        "APP_ALERTS_CHANNEL=C123",
        "DEPLOYMENTS_CHANNEL=C456",
    ]
    client.conversations_create.assert_has_calls(
        [
            call(name="app-alerts", is_private=False),
            call(name="deployments", is_private=False),
        ]
    )
    client.conversations_join.assert_has_calls([call(channel="C123"), call(channel="C456")])
    client.conversations_setPurpose.assert_has_calls(
        [
            call(channel="C123", purpose="Report failures"),
            call(channel="C456", purpose="Shipment status"),
        ]
    )
    client.conversations_setTopic.assert_has_calls(
        [
            call(channel="C123", topic="Production health"),
            call(channel="C456", topic="Releases"),
        ]
    )
    client.conversations_invite.assert_called_once_with(channel="C123", users="U123")
    assert [c.kwargs["channel"] for c in client.chat_postMessage.call_args_list] == ["C123", "C456"]
    events = [
        c[0] for c in timeline.mock_calls if c[0] in {"slack.conversations_create", "slack.chat_postMessage", "sleep"}
    ]
    assert events == [
        "slack.conversations_create",
        "slack.chat_postMessage",
        "sleep",
        "slack.conversations_create",
        "slack.chat_postMessage",
        "sleep",
    ]
    assert sleep.call_args_list == [call(1), call(1)]


def test_create_channels_delegates_and_continues_after_unsuccessful_setup():
    from nui_shared_utils.slack_setup.channel_creator import ChannelCreator
    from nui_shared_utils.slack_setup.channel_definitions import ChannelDefinition

    creator = object.__new__(ChannelCreator)
    definitions = [
        ChannelDefinition(name=name, purpose="", description="", topic="") for name in ["missing", "broken", "working"]
    ]
    creator.setup_channel = Mock(side_effect=[None, RuntimeError("setup failed"), "C3"])

    assert creator.create_channels(definitions) == {"working": "C3"}
    assert creator.setup_channel.call_args_list == [call(definition) for definition in definitions]


def test_setup_channel_skips_remaining_steps_when_channel_id_is_unresolved():
    from nui_shared_utils.slack_setup.channel_creator import ChannelCreator
    from nui_shared_utils.slack_setup.channel_definitions import ChannelDefinition

    creator = object.__new__(ChannelCreator)
    definition = ChannelDefinition(name="missing", purpose="", description="", topic="")
    creator._create_or_get_channel = Mock(return_value=None)
    helper_names = ["_bot_join_channel", "_configure_channel", "_invite_users", "_post_welcome_message"]
    for name in helper_names:
        setattr(creator, name, Mock())

    with patch("nui_shared_utils.slack_setup.channel_creator.time.sleep") as sleep:
        assert creator.setup_channel(definition) is None
        for name in helper_names:
            getattr(creator, name).assert_not_called()
        sleep.assert_not_called()
