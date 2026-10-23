"""
Timezone utilities for multi-timezone handling (NZ primary, AUS/US/EUR secondary).

Zone data comes from the standard library's ``zoneinfo``, which reads the system
IANA database. Environments without one (Windows, minimal images such as Alpine)
need the ``tzdata`` package: `pip install nui-python-shared-utils[timezone]`.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

# Supported timezones
NZ_TZ = ZoneInfo("Pacific/Auckland")  # Primary timezone
AUS_TZ = ZoneInfo("Australia/Sydney")
US_EAST_TZ = ZoneInfo("America/New_York")
US_WEST_TZ = ZoneInfo("America/Los_Angeles")
EUR_TZ = ZoneInfo("Europe/London")


def nz_time(utc_dt: Optional[datetime] = None) -> datetime:
    """
    Convert UTC datetime to NZ time or get current NZ time.

    Args:
        utc_dt: UTC datetime to convert (defaults to now)

    Returns:
        Datetime in NZ timezone
    """
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc)

    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)

    return utc_dt.astimezone(NZ_TZ)


def format_nz_time(dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    """
    Format datetime as NZ time string.

    Args:
        dt: Datetime to format (defaults to now)
        fmt: strftime format string

    Returns:
        Formatted time string
    """
    nz_dt = nz_time(dt)
    return nz_dt.strftime(fmt)


def format_multi_timezone(dt: Optional[datetime] = None, primary_tz: str = "nz", secondary_tz: str = "aus") -> str:
    """
    Format datetime with primary and secondary timezone display.

    Example output: "Fri 26th 10am NZDT (Thu 8pm AEDT)"

    Args:
        dt: Datetime to format (defaults to now)
        primary_tz: Primary timezone ("nz", "aus", "us_east", "us_west", "eur")
        secondary_tz: Secondary timezone for parenthetical display

    Returns:
        Formatted multi-timezone string
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Timezone mapping
    tz_map = {"nz": NZ_TZ, "aus": AUS_TZ, "us_east": US_EAST_TZ, "us_west": US_WEST_TZ, "eur": EUR_TZ}

    primary_tzinfo = tz_map.get(primary_tz, NZ_TZ)
    secondary_tzinfo = tz_map.get(secondary_tz, AUS_TZ)

    # Convert to both timezones
    primary_dt = dt.astimezone(primary_tzinfo)
    secondary_dt = dt.astimezone(secondary_tzinfo)

    # Format primary timezone with ordinal
    def format_ordinal(day):
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix}"

    # Primary format: "Fri 26th 10am NZDT"
    primary_hour = primary_dt.strftime("%I").lstrip("0") or "12"
    primary_tz_abbr = primary_dt.strftime("%Z")
    primary_formatted = f"{primary_dt.strftime('%a')} {format_ordinal(primary_dt.day)} {primary_hour}{primary_dt.strftime('%p').lower()} {primary_tz_abbr}"

    # Secondary format: "Thu 8pm AEDT"
    secondary_hour = secondary_dt.strftime("%I").lstrip("0") or "12"
    secondary_tz_abbr = secondary_dt.strftime("%Z")
    secondary_formatted = (
        f"{secondary_dt.strftime('%a')} {secondary_hour}{secondary_dt.strftime('%p').lower()} {secondary_tz_abbr}"
    )

    return f"{primary_formatted} ({secondary_formatted})"


def get_timezone_options() -> List[Tuple[str, str]]:
    """
    Get available timezone options for configuration.

    Returns:
        List of (key, description) tuples
    """
    return [
        ("nz", "New Zealand (Pacific/Auckland)"),
        ("aus", "Australia (Australia/Sydney)"),
        ("us_east", "US Eastern (America/New_York)"),
        ("us_west", "US Pacific (America/Los_Angeles)"),
        ("eur", "Europe (Europe/London)"),
    ]
