#!/usr/bin/env python3
"""
CLI tools for AWS Lambda utilities and Slack workspace automation.
"""

import os
import sys
import logging
from pathlib import Path

import click

from .slack_setup import ChannelCreator, load_channel_config
from .slack_setup.channel_definitions import validate_channel_names, generate_serverless_config
from .slack_setup.setup_helpers import (
    SlackSetupHelper,
    prompt_for_token,
    print_channel_summary,
    confirm_channel_creation,
)

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


@click.group()
@click.version_option(package_name="nui-python-shared-utils")
def cli():
    """AWS Lambda utilities and Slack workspace automation."""
    pass


@cli.group()
def slack():
    """Slack workspace automation commands."""
    pass


@slack.command("setup-channels")
@click.version_option(package_name="nui-python-shared-utils")
@click.option("--config", required=True, type=click.Path(exists=True), help="Path to channel configuration YAML file")
@click.option("--token", envvar="SLACK_BOT_TOKEN", help="Slack bot token (or use SLACK_BOT_TOKEN env var)")
@click.option("--check-only", is_flag=True, help="Only check existing channels, do not create")
@click.option("--dry-run", is_flag=True, help="Preview changes without creating channels")
@click.option("--output", type=click.Path(), help="Output file for environment variable configuration")
@click.option(
    "--output-format",
    type=click.Choice(["yaml", "env"], case_sensitive=False),
    default="yaml",
    help="Output format for configuration",
)
@click.option("--no-interactive", is_flag=True, help="Skip interactive confirmations")
@click.option("--validate-only", is_flag=True, help="Only validate channel names, do not create")
@click.option("--test-access", is_flag=True, help="Test bot access to created channels")
def setup_channels(
    config, token, check_only, dry_run, output, output_format, no_interactive, validate_only, test_access
):
    """Set up Slack channels from YAML configuration.

    Automates channel creation, topic/purpose configuration, user invitations,
    and bot setup for team workspaces.

    \b
    Examples:
      # Create channels from config
      slack-channel-setup --config channels.yaml

      # Check existing channels only
      slack-channel-setup --config channels.yaml --check-only

      # Generate environment variables
      slack-channel-setup --config channels.yaml --output channels.env --output-format env

      # Use specific token
      SLACK_BOT_TOKEN=xoxb-... slack-channel-setup --config channels.yaml
    """
    try:
        # Load channel configuration
        log.info(f"Loading configuration from {config}")
        definitions = load_channel_config(config)

        if not definitions:
            click.echo(click.style("❌ No channel definitions found in config", fg="red"), err=True)
            sys.exit(1)

        # Extract service/project name from first definition or config path
        service_name = definitions[0].service or Path(config).parent.parent.name

        click.echo(f"\n🚀 Slack Channel Setup for {service_name}")
        click.echo("=" * 60)

        # Validate channel names
        errors = validate_channel_names(definitions)
        if errors:
            click.echo(click.style("Channel name validation failed:", fg="red"), err=True)
            for error in errors:
                click.echo(f"  ❌ {error}")
            sys.exit(1)

        if validate_only:
            click.echo(click.style("✅ All channel names are valid", fg="green"))
            return

        # Get Slack token
        if not token and not check_only:
            token = prompt_for_token()
            os.environ["SLACK_BOT_TOKEN"] = token

        # Initialize channel creator
        creator = ChannelCreator(token)
        helper = SlackSetupHelper(token)

        # Check bot authentication
        auth_info = helper.validate_bot_permissions()
        if auth_info.get("authenticated"):
            click.echo(f"\n✅ Authenticated as: {auth_info['bot_name']} (@{auth_info['bot_id']})")
            click.echo(f"   Team: {auth_info['team']}")
        else:
            click.echo(click.style(f"❌ Authentication failed: {auth_info.get('error')}", fg="red"), err=True)
            sys.exit(1)

        # Check existing channels
        channel_names = [d.name for d in definitions]
        existing_channels = creator.check_existing_channels(channel_names)

        click.echo("\n📊 Channel Status:")
        for name, channel_id in existing_channels.items():
            if channel_id:
                click.echo(click.style(f"  ✅ #{name} exists (ID: {channel_id})", fg="green"))
            else:
                click.echo(click.style(f"  ❌ #{name} does not exist", fg="yellow"))

        if check_only or dry_run:
            if dry_run:
                click.echo("\n🔍 Dry run - showing what would be created:")
                for definition in definitions:
                    exists = existing_channels.get(definition.name)
                    if exists:
                        click.echo(f"  • #{definition.name} - Would update (exists)")
                    else:
                        click.echo(f"  • #{definition.name} - Would create (new)")
                click.echo("\nNo changes made (dry run mode)")
            return

        # Confirm creation
        if not no_interactive:
            if not confirm_channel_creation(definitions, existing_channels):
                click.echo("\nChannel creation cancelled")
                return

        # Create/configure channels
        click.echo("\n🔨 Setting up channels...")
        with click.progressbar(
            definitions, label="Creating channels", item_show_func=lambda d: f"#{d.name}" if d else ""
        ) as bar:
            channel_map = {}
            for definition in bar:
                try:
                    channel_id = creator.setup_channel(definition)
                    if channel_id:
                        channel_map[definition.name] = channel_id
                except Exception as e:
                    click.echo(click.style(f"\n  ⚠️  Error setting up #{definition.name}: {e}", fg="yellow"))

        if not channel_map:
            click.echo(click.style("❌ No channels were created", fg="red"), err=True)
            sys.exit(1)

        # Test access if requested
        if test_access:
            click.echo("\n🧪 Testing channel access...")
            test_results = helper.test_channel_access(list(channel_map.values()))
            for channel_id, success in test_results.items():
                channel_name = [k for k, v in channel_map.items() if v == channel_id][0]
                if success:
                    click.echo(click.style(f"  ✅ Can post to #{channel_name}", fg="green"))
                else:
                    click.echo(click.style(f"  ❌ Cannot post to #{channel_name}", fg="red"))

        # Generate configuration
        if output or not no_interactive:
            config_output = generate_serverless_config(channel_map, service_name, output_format)

            if output:
                with open(output, "w") as f:
                    f.write(config_output)
                click.echo(click.style(f"\n✅ Configuration written to {output}", fg="green"))
            else:
                click.echo("\n" + "=" * 60)
                click.echo("Configuration Output:")
                click.echo("=" * 60)
                click.echo(config_output)

        # Print summary
        print_channel_summary(channel_map, service_name)

    except FileNotFoundError as e:
        click.echo(click.style(f"❌ Configuration file not found: {e}", fg="red"), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(click.style(f"❌ Configuration error: {e}", fg="red"), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\n\n⚠️  Setup cancelled by user")
        sys.exit(130)
    except Exception as e:
        click.echo(click.style(f"❌ Unexpected error: {e}", fg="red"), err=True)
        log.error("Unexpected error", exc_info=True)
        sys.exit(1)


def main() -> None:
    """Run the installed channel-setup command with its documented root options."""
    setup_channels()


if __name__ == "__main__":
    main()
