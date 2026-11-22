"""
Core channel creation logic for Slack setup.
"""

import os
import time
import logging
from typing import List, Dict, Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .channel_definitions import ChannelDefinition

log = logging.getLogger(__name__)


class ChannelCreator:
    """Handles Slack channel creation with bot management."""

    def __init__(self, token: Optional[str] = None):
        """
        Initialize channel creator.

        Args:
            token: Slack bot token (uses SLACK_BOT_TOKEN env var if not provided)
        """
        self.token = token or os.environ.get("SLACK_BOT_TOKEN")
        if not self.token:
            raise ValueError("No Slack bot token provided. Set SLACK_BOT_TOKEN environment variable.")

        self.client = WebClient(token=self.token)
        self.bot_user_id = None
        self._get_bot_info()

    def _get_bot_info(self):
        """Get bot user ID for self-invitation."""
        try:
            response = self.client.auth_test()
            self.bot_user_id = response["user_id"]
            self.bot_name = response["user"]
            log.info(f"Bot authenticated as {self.bot_name} (ID: {self.bot_user_id})")
        except SlackApiError as e:
            log.error(f"Failed to get bot info: {e}")
            raise

    def create_channels(self, definitions: List[ChannelDefinition]) -> Dict[str, str]:
        """
        Create channels from definitions.

        Args:
            definitions: List of channel definitions

        Returns:
            Dict mapping channel names to IDs
        """
        channel_map = {}

        for definition in definitions:
            try:
                channel_id = self.setup_channel(definition)
                if channel_id:
                    channel_map[definition.name] = channel_id
            except Exception as e:
                log.error(f"Error processing channel {definition.name}: {e}")

        return channel_map

    def setup_channel(self, definition: ChannelDefinition) -> Optional[str]:
        """Create or find a channel, configure it, and return its ID.

        Joins the bot, sets topic and purpose, invites requested users, and posts
        a welcome message. Successful setup includes a one-second rate-limit
        delay. Returns None if an existing channel cannot be found; unexpected
        errors propagate so callers can report them and continue their batch.
        """
        channel_id = self._create_or_get_channel(definition)
        if not channel_id:
            return None

        self._bot_join_channel(channel_id, definition.name)
        self._configure_channel(channel_id, definition)
        if definition.invite_users:
            self._invite_users(channel_id, definition.invite_users, definition.name)
        self._post_welcome_message(channel_id, definition)

        # Pace both batch and CLI callers to avoid rate limits.
        time.sleep(1)
        return channel_id

    def _create_or_get_channel(self, definition: ChannelDefinition) -> Optional[str]:
        """Create channel or get existing channel ID."""
        try:
            # Try to create the channel
            response = self.client.conversations_create(
                name=definition.name, is_private=False  # Always create public channels
            )

            channel_id = response["channel"]["id"]
            log.info(f"✅ Created channel: #{definition.name} (ID: {channel_id})")
            return channel_id

        except SlackApiError as e:
            if e.response["error"] == "name_taken":
                # Channel exists, try to find it
                log.info(f"Channel #{definition.name} already exists, looking up ID...")
                return self._find_channel_id(definition.name)
            else:
                log.error(f"Failed to create channel {definition.name}: {e}")
                raise

    def _find_channel_id(self, channel_name: str) -> Optional[str]:
        """Find channel ID by name."""
        try:
            # Get all channels (handles pagination)
            channels = []
            cursor = None

            while True:
                response = self.client.conversations_list(limit=1000, cursor=cursor, exclude_archived=True)
                channels.extend(response["channels"])

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            # Find our channel
            for channel in channels:
                if channel["name"] == channel_name:
                    log.info(f"Found existing channel ID: {channel['id']}")
                    return channel["id"]

            log.warning(f"Could not find channel {channel_name}")
            return None

        except SlackApiError as e:
            log.error(f"Error listing channels: {e}")
            return None

    def _bot_join_channel(self, channel_id: str, channel_name: str):
        """Make bot join the channel."""
        try:
            self.client.conversations_join(channel=channel_id)
            log.info(f"🤖 Bot joined #{channel_name}")
        except SlackApiError as e:
            if e.response["error"] == "already_in_channel":
                log.info(f"Bot already in #{channel_name}")
            else:
                log.error(f"Failed to join #{channel_name}: {e}")

    def _configure_channel(self, channel_id: str, definition: ChannelDefinition):
        """Set channel purpose and topic."""
        # Set purpose
        if definition.purpose:
            try:
                self.client.conversations_setPurpose(channel=channel_id, purpose=definition.purpose)
                log.info(f"Set purpose: {definition.purpose}")
            except Exception as e:
                log.warning(f"Could not set purpose: {e}")

        # Set topic
        if definition.topic:
            try:
                self.client.conversations_setTopic(channel=channel_id, topic=definition.topic)
                log.info(f"Set topic: {definition.topic}")
            except Exception as e:
                log.warning(f"Could not set topic: {e}")

    def _invite_users(self, channel_id: str, user_identifiers: List[str], channel_name: str):
        """Invite users to channel by username or user ID."""
        user_ids = []

        for identifier in user_identifiers:
            if identifier.startswith("U"):
                # Already a user ID
                user_ids.append(identifier)
            else:
                # Try to look up by username
                user_id = self._get_user_id_by_name(identifier)
                if user_id:
                    user_ids.append(user_id)
                else:
                    log.warning(f"Could not find user: {identifier}")

        if user_ids:
            try:
                self.client.conversations_invite(channel=channel_id, users=",".join(user_ids))
                log.info(f"👥 Invited {len(user_ids)} users to #{channel_name}")
            except SlackApiError as e:
                if e.response["error"] == "already_in_channel":
                    log.info(f"Some users already in #{channel_name}")
                else:
                    log.error(f"Failed to invite users: {e}")

    def _get_user_id_by_name(self, username: str) -> Optional[str]:
        """Look up user ID by username."""
        try:
            # Remove @ if present
            username = username.lstrip("@")

            # Get all users (handles pagination)
            users = []
            cursor = None

            while True:
                response = self.client.users_list(limit=1000, cursor=cursor)
                users.extend(response["members"])

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            # Find user by name or display name
            for user in users:
                if not user.get("deleted", False):
                    if (
                        user.get("name") == username
                        or user.get("real_name", "").lower() == username.lower()
                        or user.get("profile", {}).get("display_name", "").lower() == username.lower()
                    ):
                        return user["id"]

            return None

        except SlackApiError as e:
            log.error(f"Error looking up user {username}: {e}")
            return None

    def _post_welcome_message(self, channel_id: str, definition: ChannelDefinition):
        """Post welcome message to channel."""
        try:
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "🎉 Channel Initialized!"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{definition.description}*"}},
            ]

            if definition.service:
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"This channel receives automated messages from *{definition.service}*",
                            }
                        ],
                    }
                )

            self.client.chat_postMessage(
                channel=channel_id, text=f"Channel initialized: {definition.description}", blocks=blocks
            )
            log.info("Posted welcome message")

        except Exception as e:
            log.warning(f"Could not post welcome message: {e}")

    def check_existing_channels(self, channel_names: List[str]) -> Dict[str, Optional[str]]:
        """
        Check which channels already exist.

        Args:
            channel_names: List of channel names to check

        Returns:
            Dict mapping channel names to IDs (None if not found)
        """
        result = {}

        try:
            # Get all channels
            channels = []
            cursor = None

            while True:
                response = self.client.conversations_list(limit=1000, cursor=cursor, exclude_archived=True)
                channels.extend(response["channels"])

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            # Check each requested channel
            for name in channel_names:
                found = False
                for channel in channels:
                    if channel["name"] == name:
                        result[name] = channel["id"]
                        found = True
                        break

                if not found:
                    result[name] = None

        except SlackApiError as e:
            log.error(f"Error checking channels: {e}")
            # Return all as not found on error
            for name in channel_names:
                result[name] = None

        return result
