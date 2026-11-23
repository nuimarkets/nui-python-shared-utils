"""
Tests for slack_formatter module.
"""

import logging
import os
import subprocess
import sys
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pytest

from nui_shared_utils.slack_formatter import (
    format_currency,
    format_percentage,
    format_number,
    format_nz_time,
    format_date_range,
    SlackBlockBuilder,
    format_daily_header,
    format_weekly_header,
    format_error_alert,
    SEVERITY_EMOJI,
    STATUS_EMOJI,
)

# Run in its own process so the host clock's zone can be set for the call.
_NO_ARG_NZ_TIME_SCRIPT = """
from datetime import datetime, timezone
from nui_shared_utils.slack_formatter import format_nz_time
from nui_shared_utils.timezone import NZ_TZ

fmt = "%I:%M %p %Z"
before = datetime.now(timezone.utc)
got = format_nz_time()
after = datetime.now(timezone.utc)
expected = {t.astimezone(NZ_TZ).strftime(fmt) for t in (before, after)}
print(f"match={got in expected}")
"""


class TestFormatFunctions:
    """Tests for formatting helper functions."""

    def test_format_currency(self):
        """Test currency formatting."""
        assert format_currency(1234.56, "USD") == "$1,234.56 USD"
        assert format_currency(0, "NZD") == "$0.00 NZD"
        assert format_currency(1000000.789, "EUR") == "$1,000,000.79 EUR"
        assert format_currency(999.99, "GBP") == "$999.99 GBP"

    def test_format_percentage_default(self):
        """Test percentage formatting with default decimals."""
        assert format_percentage(15.5) == "15.5%"
        assert format_percentage(0) == "0.0%"
        assert format_percentage(100) == "100.0%"

    def test_format_percentage_custom_decimals(self):
        """Test percentage formatting with custom decimals."""
        assert format_percentage(15.567, 2) == "15.57%"
        assert format_percentage(15.567, 0) == "16%"
        assert format_percentage(15.567, 3) == "15.567%"

    def test_format_number_integer(self):
        """Test number formatting with integers."""
        assert format_number(1234) == "1,234"
        assert format_number(0) == "0"
        assert format_number(1000000) == "1,000,000"

    def test_format_number_float_default(self):
        """Test number formatting with floats and default decimals."""
        assert format_number(1234.56) == "1,234"  # Truncated to int
        assert format_number(999.99) == "999"  # Truncated to int

    def test_format_number_float_custom_decimals(self):
        """Test number formatting with floats and custom decimals."""
        assert format_number(1234.567, 2) == "1,234.57"
        assert format_number(1234.567, 1) == "1,234.6"
        assert format_number(1234.567, 0) == "1,234"  # Truncated to int when decimals=0

    def test_format_nz_time_default(self, mock_datetime):
        """Test NZ time formatting with default (current) time."""
        result = format_nz_time()

        # The actual result shows 12-hour format by default
        # Mock datetime is 2024-01-30 10:30:45 UTC
        assert "AM" in result or "PM" in result
        assert "NZST" in result or "NZDT" in result

    def test_format_nz_time_with_datetime(self):
        """Test NZ time formatting with provided datetime."""
        utc_time = datetime(2023, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = format_nz_time(utc_time)

        # Should contain time in NZ timezone format
        assert "AM" in result or "PM" in result
        assert "NZST" in result or "NZDT" in result

    def test_format_nz_time_naive_datetime(self):
        """Test NZ time formatting with naive datetime (assumed UTC)."""
        naive_time = datetime(2023, 6, 15, 12, 30, 0)
        result = format_nz_time(naive_time)

        # Should contain time in NZ timezone format
        assert "AM" in result or "PM" in result
        assert "NZST" in result or "NZDT" in result

    def test_format_nz_time_no_argument_ignores_the_host_clock_zone(self):
        """The no-arg result must be the same wherever the process runs.

        Run under ``TZ=Pacific/Auckland``: a naive "now" handed to ``astimezone()``
        would be read as local time and come back unshifted (13 hours off), so this
        only passes if the value starts out UTC-aware. Bracketing the call with two
        UTC reads keeps it stable across a minute boundary.
        """
        result = subprocess.run(
            [sys.executable, "-c", _NO_ARG_NZ_TIME_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env={**os.environ, "TZ": "Pacific/Auckland"},
        )

        assert "match=True" in result.stdout

    def test_format_date_range_same_day(self):
        """Test date range formatting for same day."""
        start = datetime(2023, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 6, 15, 14, 0, 0, tzinfo=timezone.utc)

        result = format_date_range(start, end)

        # Should show single date with time range
        assert "Jun 15" in result
        assert "AM" in result or "PM" in result
        assert "-" in result

    def test_format_date_range_different_days(self):
        """Test date range formatting for different days."""
        start = datetime(2023, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 6, 16, 14, 0, 0, tzinfo=timezone.utc)

        result = format_date_range(start, end)

        # Should show both dates (accounting for NZ timezone conversion)
        assert "Jun 15" in result
        assert "Jun 17" in result  # Jun 16 14:00 UTC becomes Jun 17 02:00 AM NZST
        assert "-" in result

    def test_format_date_range_naive_datetimes(self):
        """Test date range formatting with naive datetimes."""
        start = datetime(2023, 6, 15, 10, 0, 0)
        end = datetime(2023, 6, 15, 14, 0, 0)

        result = format_date_range(start, end)

        # Should still work and contain expected format
        assert "Jun 15" in result
        assert "-" in result


class TestEmojiMappings:
    """Tests for emoji mapping constants."""

    def test_severity_emoji_mapping(self):
        """Test severity emoji mappings."""
        assert SEVERITY_EMOJI["critical"] == "🚨"
        assert SEVERITY_EMOJI["warning"] == "⚠️"
        assert SEVERITY_EMOJI["info"] == "ℹ️"
        assert SEVERITY_EMOJI["success"] == "✅"
        assert SEVERITY_EMOJI["error"] == "❌"

    def test_status_emoji_mapping(self):
        """Test status emoji mappings."""
        assert STATUS_EMOJI["active"] == "🟢"
        assert STATUS_EMOJI["pending"] == "🟡"
        assert STATUS_EMOJI["completed"] == "✅"
        assert STATUS_EMOJI["failed"] == "❌"
        assert STATUS_EMOJI["cancelled"] == "🚫"
        assert STATUS_EMOJI["ending_soon"] == "⏰"


class TestSlackBlockBuilder:
    """Tests for SlackBlockBuilder class."""

    def test_init(self):
        """Test builder initialization."""
        builder = SlackBlockBuilder()
        assert builder.blocks == []

    def test_add_header_simple(self):
        """Test adding a simple header."""
        builder = SlackBlockBuilder()
        result = builder.add_header("Test Header")

        assert result == builder  # Should return self for chaining
        assert len(builder.blocks) == 1
        assert builder.blocks[0]["type"] == "header"
        assert builder.blocks[0]["text"]["type"] == "plain_text"
        assert builder.blocks[0]["text"]["text"] == "Test Header"

    def test_add_header_with_emoji(self):
        """Test adding a header with emoji."""
        builder = SlackBlockBuilder()
        builder.add_header("Test Header", emoji="🎉")

        assert builder.blocks[0]["text"]["text"] == "🎉 Test Header"

    def test_add_section_simple(self):
        """Test adding a simple section."""
        builder = SlackBlockBuilder()
        builder.add_section("Test section")

        assert len(builder.blocks) == 1
        assert builder.blocks[0]["type"] == "section"
        assert builder.blocks[0]["text"]["type"] == "mrkdwn"
        assert builder.blocks[0]["text"]["text"] == "Test section"

    def test_add_section_with_value(self):
        """Test adding a section with value."""
        builder = SlackBlockBuilder()
        builder.add_section("Metric", "123")

        assert builder.blocks[0]["text"]["text"] == "*Metric:* 123"

    def test_add_section_with_emoji(self):
        """Test adding a section with emoji."""
        builder = SlackBlockBuilder()
        builder.add_section("Status", "OK", emoji="✅")

        assert builder.blocks[0]["text"]["text"] == "✅ *Status:* OK"

    def test_add_fields_basic(self):
        """Test adding fields."""
        builder = SlackBlockBuilder()
        fields = [("Label 1", "Value 1"), ("Label 2", "Value 2")]
        builder.add_fields(fields)

        assert len(builder.blocks) == 1
        assert builder.blocks[0]["type"] == "section"
        assert "fields" in builder.blocks[0]
        assert len(builder.blocks[0]["fields"]) == 2

        field1 = builder.blocks[0]["fields"][0]
        assert field1["type"] == "mrkdwn"
        assert field1["text"] == "*Label 1:*\nValue 1"

    def test_add_fields_odd_number(self):
        """Test adding odd number of fields (should pad)."""
        builder = SlackBlockBuilder()
        fields = [("Label 1", "Value 1"), ("Label 2", "Value 2"), ("Label 3", "Value 3")]
        builder.add_fields(fields)

        # Should pad to even number
        assert len(builder.blocks[0]["fields"]) == 4
        assert builder.blocks[0]["fields"][3]["text"] == " "

    def test_add_fields_limit(self):
        """Test fields limit (max 10)."""
        builder = SlackBlockBuilder()
        fields = [(f"Label {i}", f"Value {i}") for i in range(15)]
        builder.add_fields(fields)

        # Should limit to 10 fields
        assert len(builder.blocks[0]["fields"]) == 10

    def test_add_context(self):
        """Test adding context."""
        builder = SlackBlockBuilder()
        builder.add_context("Context text")

        assert len(builder.blocks) == 1
        assert builder.blocks[0]["type"] == "context"
        assert builder.blocks[0]["elements"][0]["type"] == "mrkdwn"
        assert builder.blocks[0]["elements"][0]["text"] == "Context text"

    def test_add_divider(self):
        """Test adding divider."""
        builder = SlackBlockBuilder()
        builder.add_divider()

        assert len(builder.blocks) == 1
        assert builder.blocks[0]["type"] == "divider"

    def test_add_metrics_list(self):
        """Test adding metrics list."""
        builder = SlackBlockBuilder()
        metrics = [
            {"label": "Orders", "value": "150"},
            {"label": "Revenue", "value": "$25,000", "change": 15.5},
            {"label": "Errors", "value": "5", "change": -10.0},
        ]
        builder.add_metrics_list(metrics)

        assert len(builder.blocks) == 3

        # First metric (no change)
        assert "• *Orders:* 150" in builder.blocks[0]["text"]["text"]

        # Second metric (positive change)
        assert "• *Revenue:* $25,000 📈 15.5%" in builder.blocks[1]["text"]["text"]

        # Third metric (negative change)
        assert "• *Errors:* 5 📉 -10.0%" in builder.blocks[2]["text"]["text"]

    def test_add_activity_meter(self):
        """Test adding activity meter."""
        builder = SlackBlockBuilder()
        builder.add_activity_meter(75, max_value=100, width=10)

        assert len(builder.blocks) == 1
        text = builder.blocks[0]["text"]["text"]
        assert "Activity Level:" in text
        assert "75.0%" in text
        assert "█" in text  # Should have filled bars
        assert "░" in text  # Should have empty bars

    def test_add_error_summary_with_errors(self):
        """Test adding error summary with errors."""
        builder = SlackBlockBuilder()
        errors = [
            {"key": "404_error", "doc_count": 25},
            {"key": "500_error", "doc_count": 10},
            {"key": "timeout", "doc_count": 5},
        ]
        builder.add_error_summary(errors, limit=2)

        assert len(builder.blocks) == 1
        text = builder.blocks[0]["text"]["text"]
        assert "*Top 2 Errors:*" in text
        assert "`404_error` - 25 occurrences" in text
        assert "`500_error` - 10 occurrences" in text
        assert "timeout" not in text  # Should respect limit

    def test_add_error_summary_no_errors(self):
        """Test adding error summary with no errors."""
        builder = SlackBlockBuilder()
        builder.add_error_summary([])

        assert len(builder.blocks) == 1
        text = builder.blocks[0]["text"]["text"]
        assert "✅" in text
        assert "No errors detected" in text

    def test_add_chart(self):
        """Test adding ASCII chart."""
        builder = SlackBlockBuilder()
        data_points = [10, 20, 15, 25, 30]
        labels = ["A", "B", "C", "D", "E"]
        builder.add_chart("Test Chart", data_points, labels)

        assert len(builder.blocks) == 1
        text = builder.blocks[0]["text"]["text"]
        assert "*Test Chart*" in text
        assert "```" in text  # Should be code block
        assert "█" in text  # Should have chart bars
        assert "A " in text  # Should have labels

    def test_add_chart_no_labels(self):
        """Test adding chart without labels."""
        builder = SlackBlockBuilder()
        data_points = [10, 20, 15]
        builder.add_chart("Test Chart", data_points)

        text = builder.blocks[0]["text"]["text"]
        assert "*Test Chart*" in text
        assert "```" in text
        # Should not have label line
        assert not text.endswith("A B C \n```")

    def test_add_alert(self):
        """Test adding alert."""
        builder = SlackBlockBuilder()
        builder.add_alert("critical", "auth", "Database connection failed")

        # Should add header, fields, and message
        assert len(builder.blocks) >= 3

        # Check header
        assert builder.blocks[0]["type"] == "header"
        assert "🚨" in builder.blocks[0]["text"]["text"]
        assert "CRITICAL" in builder.blocks[0]["text"]["text"]

        # Check fields section
        assert builder.blocks[1]["type"] == "section"
        assert "fields" in builder.blocks[1]

        # Check message
        message_block = next(
            block
            for block in builder.blocks
            if block["type"] == "section" and "text" in block and "Database connection failed" in block["text"]["text"]
        )
        assert message_block is not None

    def test_add_alert_with_details(self):
        """Test adding alert with details."""
        builder = SlackBlockBuilder()
        details = {"error_code": "500", "attempts": "3"}
        builder.add_alert("error", "order", "Processing failed", details)

        # Should have additional details section
        details_block = next(
            block
            for block in builder.blocks
            if block["type"] == "section" and "text" in block and "*Details:*" in block["text"]["text"]
        )
        assert details_block is not None
        assert "error_code: 500" in details_block["text"]["text"]
        assert "attempts: 3" in details_block["text"]["text"]

    def test_add_summary_stats(self):
        """Test adding summary statistics."""
        builder = SlackBlockBuilder()
        stats = {"total_orders": 150, "error_rate": 2.5, "revenue_amount": 25000.50, "status": "healthy"}
        builder.add_summary_stats(stats)

        assert len(builder.blocks) >= 2

        # Check header
        header_block = next(block for block in builder.blocks if "Summary Statistics" in block["text"]["text"])
        assert header_block is not None

        # Check fields
        fields_block = next(block for block in builder.blocks if "fields" in block)
        assert fields_block is not None

        # Check formatting
        field_texts = [field["text"] for field in fields_block["fields"]]
        assert any("Total Orders" in text and "150" in text for text in field_texts)
        assert any("Error Rate" in text and "2.5%" in text for text in field_texts)
        assert any("Revenue Amount" in text and "25,000" in text for text in field_texts)  # format_number (no decimals)
        assert any("Status" in text and "healthy" in text for text in field_texts)

    def test_add_code_block(self):
        """Test adding code block."""
        builder = SlackBlockBuilder()
        code = "SELECT * FROM orders WHERE status = 'pending'"
        builder.add_code_block(code, "sql")

        assert len(builder.blocks) == 1
        text = builder.blocks[0]["text"]["text"]
        assert "```sql" in text
        assert code in text
        assert text.endswith("```")

    def test_add_code_block_no_language(self):
        """Test adding code block without language."""
        builder = SlackBlockBuilder()
        code = "console.log('hello')"
        builder.add_code_block(code)

        text = builder.blocks[0]["text"]["text"]
        assert "```\n" in text
        assert code in text

    def test_build(self):
        """Test building blocks."""
        builder = SlackBlockBuilder()
        builder.add_header("Test")
        builder.add_section("Content")

        blocks = builder.build()

        assert len(blocks) == 2
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"

    @pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 100, 101, 105])
    def test_build_limit(self, count, caplog):
        """Single-message builds warn only when blocks are omitted."""
        builder = SlackBlockBuilder()
        for i in range(count):
            builder.add_section(f"Section {i}")
        original = builder.blocks.copy()

        with caplog.at_level(logging.WARNING, logger="nui_shared_utils.slack_formatter"):
            blocks = builder.build()

        assert blocks == original[:50]
        assert blocks is not builder.blocks
        assert builder.blocks == original
        records = [record for record in caplog.records if record.name == "nui_shared_utils.slack_formatter"]
        if count > 50:
            assert len(records) == 1
            assert records[0].levelno == logging.WARNING
            assert f"truncated {count} blocks to 50, omitting {count - 50} blocks" in records[0].getMessage()
            assert "build_messages()" in records[0].getMessage()
        else:
            assert records == []

        # A prior single-message build must leave the tail available.
        assert [block for message in builder.build_messages() for block in message] == original

    @pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 100, 101, 105])
    def test_build_messages(self, count, caplog):
        """Continuation chunks retain every block in order without warnings."""
        builder = SlackBlockBuilder()
        for i in range(count):
            builder.add_section(f"Section {i}")
        original = builder.blocks.copy()

        with caplog.at_level(logging.WARNING, logger="nui_shared_utils.slack_formatter"):
            messages = builder.build_messages()

        assert [len(message) for message in messages] == [min(50, count - start) for start in range(0, count, 50)]
        assert [block for message in messages for block in message] == original
        assert builder.blocks == original
        assert not caplog.records
        for message, repeated in zip(messages, builder.build_messages()):
            assert message is not repeated
            assert message is not builder.blocks
        for actual, expected in zip([block for message in messages for block in message], original):
            assert actual is expected

    def test_clear(self):
        """Test clearing blocks."""
        builder = SlackBlockBuilder()
        builder.add_header("Test")
        builder.add_section("Content")

        assert len(builder.blocks) == 2

        result = builder.clear()

        assert result == builder  # Should return self
        assert len(builder.blocks) == 0

    def test_method_chaining(self):
        """Test method chaining."""
        builder = SlackBlockBuilder()

        result = builder.add_header("Test").add_section("Content").add_divider().add_context("Footer")

        assert result == builder
        assert len(builder.blocks) == 4


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @patch("nui_shared_utils.slack_formatter.format_nz_time")
    def test_format_daily_header_no_time_window(self, mock_format_time):
        """Test daily header without time window."""
        mock_format_time.return_value = "2:30 PM NZST"

        blocks = format_daily_header("Sales Report")

        assert len(blocks) >= 3  # Header, context, divider

        # Check header
        header_block = blocks[0]
        assert header_block["type"] == "header"
        assert "Daily Sales Report" in header_block["text"]["text"]
        assert "📈" in header_block["text"]["text"]

        # Check context
        context_block = blocks[1]
        assert context_block["type"] == "context"
        assert "Generated" in context_block["elements"][0]["text"]
        assert "2:30 PM NZST" in context_block["elements"][0]["text"]

        # Check divider
        assert blocks[2]["type"] == "divider"

    def test_format_daily_header_with_time_window(self):
        """Test daily header with time window."""
        time_window = {"start": "2023-06-15 00:00", "end": "2023-06-15 23:59"}

        blocks = format_daily_header("Sales Report", time_window)

        context_block = blocks[1]
        assert "Period" in context_block["elements"][0]["text"]
        assert "2023-06-15 00:00" in context_block["elements"][0]["text"]
        assert "2023-06-15 23:59" in context_block["elements"][0]["text"]

    @patch("nui_shared_utils.slack_formatter.format_date_range")
    def test_format_weekly_header(self, mock_format_range):
        """Test weekly header."""
        mock_format_range.return_value = "Jun 15 - Jun 21 NZST"

        week_start = datetime(2023, 6, 15, 0, 0, 0)
        blocks = format_weekly_header("Performance Report", week_start)

        assert len(blocks) >= 3

        # Check header
        header_block = blocks[0]
        assert "Weekly Performance Report" in header_block["text"]["text"]
        assert "📊" in header_block["text"]["text"]

        # Check context
        context_block = blocks[1]
        assert "Week of" in context_block["elements"][0]["text"]
        assert "Jun 15 - Jun 21 NZST" in context_block["elements"][0]["text"]

        # Check format_date_range was called with correct dates
        call_args = mock_format_range.call_args[0]
        assert call_args[0] == week_start
        assert call_args[1] == week_start + timedelta(days=6)

    def test_format_error_alert_warning(self):
        """Test error alert with warning severity."""
        top_errors = [{"key": "404_error", "doc_count": 15}, {"key": "timeout", "doc_count": 8}]

        blocks = format_error_alert("auth", 15.0, top_errors)

        assert len(blocks) >= 4

        # Check alert header
        header_block = blocks[0]
        assert "WARNING" in header_block["text"]["text"]
        assert "⚠️" in header_block["text"]["text"]

        # Check alert message
        message_block = next(block for block in blocks if "15.0%" in block.get("text", {}).get("text", ""))
        assert message_block is not None
        assert "exceeds threshold" in message_block["text"]["text"]

        # Check error summary
        error_block = next(block for block in blocks if "404_error" in block.get("text", {}).get("text", ""))
        assert error_block is not None

    def test_format_error_alert_critical(self):
        """Test error alert with critical severity."""
        blocks = format_error_alert("order", 25.0, [])

        # Should be critical severity
        header_block = blocks[0]
        assert "CRITICAL" in header_block["text"]["text"]
        assert "🚨" in header_block["text"]["text"]
