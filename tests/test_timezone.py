"""
Tests for timezone module.
"""

import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import nui_shared_utils.timezone as timezone_utils
from nui_shared_utils.timezone import nz_time, format_nz_time, NZ_TZ


def _run(script: str, env_overrides: dict = None) -> str:
    """Execute ``script`` in a fresh Python interpreter and return stdout."""
    env = {**os.environ, **(env_overrides or {})}
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env=env,
    )
    return result.stdout.strip()


class TestNzTime:
    """Tests for nz_time function."""

    def test_nz_time_with_utc_datetime(self):
        """Test converting UTC datetime to NZ time."""
        # Create a UTC datetime (2024-01-30 10:00:00 UTC)
        utc_dt = datetime(2024, 1, 30, 10, 0, 0, tzinfo=timezone.utc)

        result = nz_time(utc_dt)

        # In January, NZ is UTC+13 (NZDT)
        # So 10:00 UTC should be 23:00 NZDT
        assert result.hour == 23
        assert result.day == 30
        assert result.tzinfo.key == "Pacific/Auckland"

    def test_nz_time_with_naive_datetime(self):
        """Test converting naive datetime to NZ time (assumes UTC)."""
        # Create a naive datetime
        naive_dt = datetime(2024, 1, 30, 10, 0, 0)

        result = nz_time(naive_dt)

        # Should treat as UTC and convert to NZ
        assert result.hour == 23
        assert result.day == 30
        assert result.tzinfo.key == "Pacific/Auckland"

    def test_nz_time_no_argument(self):
        """Test getting current NZ time."""
        # Mock current UTC time
        mock_now = datetime(2024, 1, 30, 10, 0, 0, tzinfo=timezone.utc)

        with patch("nui_shared_utils.timezone.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = nz_time()

        assert result.hour == 23
        assert result.day == 30
        assert result.tzinfo.key == "Pacific/Auckland"

    def test_nz_time_winter_time(self):
        """Test NZ time during winter (NZST - UTC+12)."""
        # July is winter in NZ (UTC+12)
        utc_dt = datetime(2024, 7, 15, 10, 0, 0, tzinfo=timezone.utc)

        result = nz_time(utc_dt)

        # 10:00 UTC should be 22:00 NZST
        assert result.hour == 22
        assert result.day == 15
        assert result.tzinfo.key == "Pacific/Auckland"

    def test_nz_time_with_already_nz_datetime(self):
        """Test passing datetime already in NZ timezone."""
        nz_dt = datetime(2024, 1, 30, 15, 0, 0, tzinfo=NZ_TZ)

        result = nz_time(nz_dt)

        # Should preserve the time
        assert result.hour == 15
        assert result.day == 30
        assert result.tzinfo.key == "Pacific/Auckland"


class TestFormatNzTime:
    """Tests for format_nz_time function."""

    def test_format_nz_time_with_datetime(self):
        """Test formatting specific datetime."""
        utc_dt = datetime(2024, 1, 30, 10, 30, 45, tzinfo=timezone.utc)

        result = format_nz_time(utc_dt)

        # Default format includes timezone
        assert result == "2024-01-30 23:30:45 NZDT"

    def test_format_nz_time_custom_format(self):
        """Test custom format string."""
        utc_dt = datetime(2024, 1, 30, 10, 30, 45, tzinfo=timezone.utc)

        result = format_nz_time(utc_dt, fmt="%Y-%m-%d %H:%M")

        assert result == "2024-01-30 23:30"

    def test_format_nz_time_no_argument(self):
        """Test formatting current time."""
        mock_now = datetime(2024, 1, 30, 10, 30, 45, tzinfo=timezone.utc)

        with patch("nui_shared_utils.timezone.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = format_nz_time()

        assert result == "2024-01-30 23:30:45 NZDT"

    def test_format_nz_time_date_only(self):
        """Test formatting date only."""
        utc_dt = datetime(2024, 1, 30, 10, 0, 0, tzinfo=timezone.utc)

        result = format_nz_time(utc_dt, fmt="%Y-%m-%d")

        assert result == "2024-01-30"

    def test_format_nz_time_12_hour(self):
        """Test 12-hour format."""
        utc_dt = datetime(2024, 1, 30, 10, 0, 0, tzinfo=timezone.utc)

        result = format_nz_time(utc_dt, fmt="%I:%M %p")

        assert result == "11:00 PM"

    def test_format_nz_time_winter(self):
        """Test formatting during winter (NZST)."""
        utc_dt = datetime(2024, 7, 15, 10, 0, 0, tzinfo=timezone.utc)

        result = format_nz_time(utc_dt)

        assert result == "2024-07-15 22:00:00 NZST"


class TestNoThirdPartyTimezoneDependency:
    """Zone data comes from stdlib ``zoneinfo``; nothing here may pull in ``pytz``.

    Run in a fresh interpreter so an import from elsewhere in the suite cannot
    mask a regression. ``pytz`` is no longer a declared dependency, so this also
    fails loudly if it creeps back into a module that ships in the base install.
    """

    def test_timezone_module_does_not_import_pytz(self):
        output = _run("""
            import sys
            import nui_shared_utils.timezone  # noqa: F401
            print(f"pytz={'pytz' in sys.modules}")
            """)
        assert "pytz=False" in output

    def test_slack_formatter_does_not_import_pytz(self):
        output = _run("""
            import sys
            import nui_shared_utils.slack_formatter  # noqa: F401
            print(f"pytz={'pytz' in sys.modules}")
            """)
        assert "pytz=False" in output


class TestZoneDataSources:
    """Where ``zoneinfo`` reads its database from, and how it fails without one.

    ``TZPATH`` is fixed at interpreter start from ``PYTHONTZPATH``, so each case
    needs its own process. Emptying it simulates an image with no
    ``/usr/share/zoneinfo`` (Alpine, distroless, Windows).
    """

    def test_zone_resolves_from_tzdata_when_system_database_is_absent(self):
        """The ``[timezone]`` extra (``tzdata``) must be a sufficient zone source.

        This is the documented fix for a minimal image, so it needs to hold
        without a system database rather than be asserted in the docs alone.
        """
        pytest.importorskip("tzdata", reason="tzdata is the [timezone] extra; not installed here")

        output = _run(
            """
            from zoneinfo import TZPATH
            from nui_shared_utils.timezone import NZ_TZ, nz_time
            from datetime import datetime, timezone
            print(f"tzpath_empty={TZPATH == ()}")
            print(f"key={NZ_TZ.key}")
            print(nz_time(datetime(2024, 1, 30, 10, 0, 0, tzinfo=timezone.utc)).strftime("offset=%z %Z"))
            """,
            env_overrides={"PYTHONTZPATH": ""},
        )
        assert "tzpath_empty=True" in output
        assert "key=Pacific/Auckland" in output
        assert "offset=+1300 NZDT" in output

    def test_missing_zone_data_fails_loudly(self):
        """With no system database and no ``tzdata``, import raises rather than guessing."""
        output = _run(
            """
            import sys

            class _BlockTzdata:
                def find_spec(self, name, path=None, target=None):
                    if name == "tzdata" or name.startswith("tzdata."):
                        raise ImportError("tzdata blocked for this test")
                    return None

            sys.meta_path.insert(0, _BlockTzdata())
            try:
                import nui_shared_utils.timezone  # noqa: F401
                print("result=imported")
            except Exception as exc:
                print(f"result={type(exc).__name__}")
            """,
            env_overrides={"PYTHONTZPATH": ""},
        )
        assert "result=ZoneInfoNotFoundError" in output


class TestAmbiguousWallTimes:
    """``fold`` decides which side of a DST fall-back an ambiguous wall time lands on.

    ``ZoneInfo`` defaults to ``fold=0`` (the first, still-daylight occurrence).
    Pinned here because the zone constants are public and invite direct
    construction, where the choice is silent and an hour wide.
    """

    def test_fold_selects_the_repeated_hour(self):
        # NZ daylight time ends 2024-04-07 03:00 NZDT -> 02:00 NZST, so 02:30 happens twice.
        ambiguous = datetime(2024, 4, 7, 2, 30, 0)

        first = ambiguous.replace(tzinfo=NZ_TZ)  # fold=0
        second = ambiguous.replace(tzinfo=NZ_TZ, fold=1)

        assert first.strftime("%Z") == "NZDT"
        assert first.utcoffset().total_seconds() == 13 * 3600
        assert second.strftime("%Z") == "NZST"
        assert second.utcoffset().total_seconds() == 12 * 3600

    def test_utc_input_is_never_ambiguous(self):
        """The module's own conversions take UTC, so they sidestep the fold question."""
        utc_dt = datetime(2024, 4, 6, 13, 30, 0, tzinfo=timezone.utc)

        assert nz_time(utc_dt).strftime("%H:%M %Z") == "02:30 NZDT"
        assert nz_time(utc_dt + timedelta(hours=1)).strftime("%H:%M %Z") == "02:30 NZST"


class TestTimezoneConstants:
    """Tests for timezone constants."""

    def test_all_zone_keys_are_canonical(self):
        # Canonical names for the supported regions, from IANA's zone1970.tab:
        # https://data.iana.org/time-zones/tzdb/zone1970.tab
        # Keep this list independent of the host database: available_timezones()
        # also includes optional backward links on many systems.
        canonical_keys = {
            "Pacific/Auckland",
            "Australia/Sydney",
            "America/New_York",
            "America/Los_Angeles",
            "Europe/London",
        }
        zone_keys = {value.key for value in vars(timezone_utils).values() if isinstance(value, ZoneInfo)}
        assert zone_keys, "No timezone constants were checked"
        assert zone_keys <= canonical_keys, f"Non-canonical or unreviewed zone keys: {zone_keys - canonical_keys}"

    def test_option_labels_use_canonical_zone_keys(self):
        labels = dict(timezone_utils.get_timezone_options())
        assert labels["us_east"] == "US Eastern (America/New_York)"
        assert labels["us_west"] == "US Pacific (America/Los_Angeles)"

    @pytest.mark.parametrize(
        "zone, month, offset, abbreviation",
        [
            (timezone_utils.US_EAST_TZ, 1, -5, "EST"),
            (timezone_utils.US_EAST_TZ, 7, -4, "EDT"),
            (timezone_utils.US_WEST_TZ, 1, -8, "PST"),
            (timezone_utils.US_WEST_TZ, 7, -7, "PDT"),
        ],
    )
    def test_us_offsets_and_dst(self, zone, month, offset, abbreviation):
        local = datetime(2024, month, 15, 12, tzinfo=timezone.utc).astimezone(zone)
        assert local.utcoffset() == timedelta(hours=offset)
        assert local.tzname() == abbreviation

    def test_nz_tz_constant(self):
        """Test that NZ_TZ is properly configured."""
        assert NZ_TZ.key == "Pacific/Auckland"

        # Test DST transitions
        # Summer time (NZDT - UTC+13)
        summer = datetime(2024, 1, 15, 12, 0, 0)
        summer_nz = summer.replace(tzinfo=NZ_TZ)
        assert summer_nz.strftime("%Z") == "NZDT"
        assert summer_nz.utcoffset().total_seconds() == 13 * 3600

        # Winter time (NZST - UTC+12)
        winter = datetime(2024, 7, 15, 12, 0, 0)
        winter_nz = winter.replace(tzinfo=NZ_TZ)
        assert winter_nz.strftime("%Z") == "NZST"
        assert winter_nz.utcoffset().total_seconds() == 12 * 3600
