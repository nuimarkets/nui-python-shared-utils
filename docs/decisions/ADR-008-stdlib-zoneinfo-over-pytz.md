# ADR-008: Zone data from stdlib `zoneinfo`, not a bundled `pytz`

**Status**: Proposed
**Date**: 2026-07-27

## Context

Every install of this package pays for its base dependencies, whatever the consumer imports.
`pytz` sat in that base set for two modules (`timezone.py`, `slack_formatter.py`), so a Lambda
that wanted only the logging contract or the metrics helper still shipped it.

The cost is not the code. `pytz` bundles its own copy of the Olson/IANA database (0.86 MB of a
1.0 MB installed package, measured on `pytz==2025.2`), and that copy is redundant on
every interpreter this package supports: `requires-python = ">=3.10"`, and `zoneinfo` has been in
the standard library since 3.9, reading the system database that Linux, macOS, and the AWS Lambda
runtimes all ship.

`pytz` also carries an API predating PEP 495. Its zones must be attached through
`tz.localize(dt)` rather than `datetime(..., tzinfo=tz)`, because a bare `pytz` zone object
defaults to whatever offset was in effect at the start of that zone's records: attaching
`pytz.timezone("Pacific/Auckland")` to a 2024 datetime yields the 1868 LMT offset of `+11:39`.
`ZoneInfo` resolves the offset from the datetime itself, giving `+13:00`, so the direct
constructor form is correct.

## Decision

Zone data comes from stdlib `zoneinfo`. `pytz` is dropped from the base dependencies and from
the package entirely, with no compatibility shim.

- `NZ_TZ`, `AUS_TZ`, `US_EAST_TZ`, `US_WEST_TZ`, `EUR_TZ` are `ZoneInfo` instances.
- `pytz.UTC.localize(dt)` becomes `dt.replace(tzinfo=timezone.utc)`.
- `slack_formatter` imports `NZ_TZ` from `timezone` rather than constructing its own zone, so
  one module owns the zone constants.
- The package's own conversions take UTC input, so no code path here has to resolve an ambiguous
  wall time. Callers attaching a zone to a naive local time do, and the default differs from
  `pytz`: `datetime(..., tzinfo=NZ_TZ)` uses `fold=0`, the first (still-daylight) occurrence of a
  repeated hour, where `pytz.localize()` defaulted to `is_dst=False`, the second. `fold=1`
  reproduces the old default. `TestAmbiguousWallTimes` pins both sides.

Zone data availability is handled where it actually differs by platform, rather than by shipping
a database to everyone:

- `tzdata>=2023.3; platform_system == "Windows"` in the base dependencies. Windows ships no
  system database; POSIX consumers resolve the marker to nothing and install neither package.
- A `[timezone]` extra (`tzdata`) for minimal images (Alpine, distroless) that carry no
  `/usr/share/zoneinfo`. No environment marker can detect those, so it is opt-in.

## Alternatives considered

- **Move `pytz` into an extra, keep the code as-is.** Fixes the unconditional cost and nothing
  else: consumers of `timezone.py` still install a redundant database, and the `localize()` API
  stays. Rejected as half the change for most of the churn.
- **Keep `pytz` and accept the base-dependency cost.** The status quo. Rejected: the package
  exists to be bundled into Lambdas, where the base set is the floor nobody can opt out of.
- **Depend on `tzdata` unconditionally instead of the system database.** Trades one bundled
  database for another (a 0.35 MB wheel) on platforms that already have one. Rejected; the
  marker plus the extra cover the platforms that genuinely need it.
- **Ship a `localize()`-compatible wrapper for the exported zone constants.** Rejected: it
  preserves a pre-PEP-495 idiom indefinitely to spare a one-line call-site change.

## Consequences

- A base install drops ~1 MB and one runtime dependency. Nothing in the package imports a
  third-party timezone library.
- **Breaking for code importing the zone constants from `nui_shared_utils.timezone`.**
  `NZ_TZ.localize(dt)` is gone; use `datetime(..., tzinfo=NZ_TZ)` or `dt.replace(tzinfo=NZ_TZ)`,
  with `fold=1` where the old `is_dst=False` default mattered. `NZ_TZ.zone` is now `NZ_TZ.key`.
  The top-level package exports only `nz_time` / `format_nz_time`, whose behaviour is unchanged,
  so consumers going through `nui_shared_utils` see nothing.
- Attaching a zone at construction is now correct rather than a bug, so the sharp edge that made
  `localize()` mandatory is gone.
- `slack_formatter.format_nz_time()` called with no argument is fixed as a side effect. It built
  a naive `datetime.utcnow()` and handed it to `astimezone()`, which reads a naive datetime as
  *local* time: right on a UTC host, an hour or thirteen out anywhere else. It now starts from an
  aware UTC now, so the result no longer depends on the host clock's zone.
- On an image with no system database, `import nui_shared_utils.timezone` raises
  `ZoneInfoNotFoundError` instead of silently working. That is a louder failure than `pytz` gave,
  and the `[timezone]` extra is the fix.
- Zone data tracks the host, not a pinned package version. DST rule changes arrive with system
  updates instead of a dependency bump, which is the desired direction for long-lived Lambda
  images but does mean a stale base image carries stale rules.

## Load-bearing premises (supersede triggers)

- **All supported interpreters include stdlib `zoneinfo`.** The package requires Python 3.10
  or higher. Supporting an interpreter older than 3.9 would need `backports.zoneinfo`, at
  which point the calculus changes.
- **Target runtimes ship a system IANA database.** True of Linux, macOS, and the AWS Lambda
  runtimes today. If the primary deployment target became one that does not, `tzdata` belongs in
  the base dependencies unconditionally and this ADR's marker split is obsolete.
