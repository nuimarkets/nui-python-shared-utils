"""Distribution metadata inside a ZIP: exact pins, target-marker closure, test-only leaks."""

import re
from email.parser import BytesParser
from pathlib import PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .errors import fail
from .zipcheck import read_member


def _requirement_lines(path):
    try:
        text = path.read_text()
    except OSError as error:
        fail("ADAPTER_CONFIG", f"{path}: {error}")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield line.split(" #", 1)[0].strip()


def read_pins(path):
    """Exact pins: one ``==`` per distribution, no marker, no duplicates."""
    selected = {}
    for line in _requirement_lines(path):
        try:
            req = Requirement(line)
        except InvalidRequirement as error:
            fail("PIN_FORMAT", f"{path}: {line}: {error}")
        specifiers = list(req.specifier)
        if req.marker is not None or len(specifiers) != 1 or specifiers[0].operator != "==":
            fail("PIN_FORMAT", f"{path}: not one exact unconditional pin: {line}")
        name = canonicalize_name(req.name)
        if name in selected:
            fail("PIN_FORMAT", f"{path}: duplicate pin {name}")
        selected[name] = specifiers[0].version
    if not selected:
        fail("PIN_FORMAT", f"{path}: no pins")
    return selected


def read_roots(path):
    roots = []
    for line in _requirement_lines(path):
        if line.startswith("-"):
            fail("PIN_FORMAT", f"{path}: options are not supported in a roots file: {line}")
        try:
            roots.append(Requirement(line))
        except InvalidRequirement as error:
            fail("PIN_FORMAT", f"{path}: {line}: {error}")
    if not roots:
        fail("PIN_FORMAT", f"{path}: no roots")
    return roots


def _requirement(raw, where):
    try:
        return Requirement(raw)
    except InvalidRequirement as error:
        fail("METADATA_INVALID", f"{where}: {raw}: {error}")


def _contains(specifier, version):
    """Strict, as the source checks were: a prerelease never satisfies a range. Explicit,
    because packaging's default for ``in`` differs between the host's packaging and the
    image's vendored copy."""
    return SpecifierSet(str(specifier)).contains(Version(version), prereleases=False)


def _dist_name(directory):
    """``<name>-<version>.dist-info`` or ``<name>[-<version>[-pyX.Y]].egg-info`` -> name. The
    version is the last dash-separated part that starts with a digit, so a non-normalized
    hyphenated name (``pytest-cov-6.0.0.dist-info``) keeps all of itself."""
    stem = directory.rsplit(".", 1)[0]
    if directory.endswith(".egg-info"):
        stem = re.sub(r"-py\d+(?:\.\d+)*$", "", stem)
    if re.search(r"-\d[^-]*$", stem):
        stem = stem.rsplit("-", 1)[0]
    return canonicalize_name(stem)


def zip_distributions(bundle):
    """Every top-level ``*.dist-info/METADATA``, keyed by normalized name; duplicates fail.

    A top-level ``*.egg-info`` is refused: the runtime's ``importlib.metadata`` would count it
    as a distribution that these host checks never saw."""
    found = {}
    for name in bundle.namelist():
        path = PurePosixPath(name)
        if len(path.parts) >= 1 and path.parts[0].endswith(".egg-info"):
            fail("METADATA_INVALID", f"egg-info distributions are not supported: {path.parts[0]}")
        if len(path.parts) != 2 or path.name != "METADATA" or not path.parts[0].endswith(".dist-info"):
            continue
        message = BytesParser().parsebytes(read_member(bundle, name))
        if not message["Name"] or not message["Version"]:
            fail("METADATA_INVALID", f"{name}: no Name or Version")
        try:
            Version(message["Version"])
            SpecifierSet(message.get("Requires-Python") or "")
        except (InvalidVersion, InvalidSpecifier) as error:
            fail("METADATA_INVALID", f"{name}: {error}")
        for raw in message.get_all("Requires-Dist", []):
            _requirement(raw, name)
        key = canonicalize_name(message["Name"])
        if key in found:
            fail("METADATA_DUPLICATE", f"{key}: {found[key]['path']} and {name}")
        found[key] = {
            "path": name,
            "version": message["Version"],
            "requires_python": message.get("Requires-Python"),
            "requires": message.get_all("Requires-Dist", []),
        }
    return found


def check_pins(distributions, pins):
    actual = {name: data["version"] for name, data in distributions.items()}
    if actual != pins:
        missing = sorted(set(pins) - set(actual))
        extra = sorted(set(actual) - set(pins))
        changed = sorted(n for n in set(pins) & set(actual) if pins[n] != actual[n])
        fail("PIN_MISMATCH", f"missing={missing} extra={extra} changed={changed}")


def target_environment(python_full_version, machine):
    major_minor = ".".join(python_full_version.split(".")[:2])
    environment = default_environment()
    environment.update(
        python_version=major_minor,
        python_full_version=python_full_version,
        implementation_version=python_full_version,
        implementation_name="cpython",
        platform_python_implementation="CPython",
        sys_platform="linux",
        platform_system="Linux",
        os_name="posix",
        platform_machine=machine,
    )
    return environment


def closure(distributions, roots, environment, pins=None):
    """Walk Requires-Dist from ``roots`` under ``environment``, propagating extras.

    With ``pins`` the packaged distributions must equal the pins exactly, checked first so a
    removed or substituted distribution is reported as a pin mismatch. Returns the graph;
    raises on the first class of error found, in a fixed order, with all its members.
    """
    if pins is not None:
        check_pins(distributions, pins)
    versions = {name: data["version"] for name, data in distributions.items()}
    full = environment["python_full_version"]
    errors = {"CLOSURE_ROOT": [], "CLOSURE_MISSING": [], "REQUIRES_PYTHON": [], "CLOSURE_DEPENDENCY": []}
    extras = {}
    for req in roots:
        if req.marker is not None and not req.marker.evaluate(dict(environment, extra="")):
            continue
        name = canonicalize_name(req.name)
        extras.setdefault(name, set()).update(req.extras)
        if name not in versions or not _contains(req.specifier, versions[name]):
            errors["CLOSURE_ROOT"].append(f"{req} -> {versions.get(name)}")
    pending = list(extras)
    examined = {}
    edges = []
    while pending:
        name = pending.pop()
        wanted = extras[name]
        if examined.get(name) == wanted:
            continue
        examined[name] = set(wanted)
        if name not in distributions:
            errors["CLOSURE_MISSING"].append(name)
            continue
        data = distributions[name]
        if data["requires_python"] and not _contains(data["requires_python"], full):
            errors["REQUIRES_PYTHON"].append(f"{name} requires {data['requires_python']}")
        for raw in data["requires"]:
            req = _requirement(raw, data["path"])
            if req.marker is not None and not any(
                req.marker.evaluate(dict(environment, extra=extra)) for extra in [""] + sorted(wanted)
            ):
                continue
            dependency = canonicalize_name(req.name)
            edges.append([name, str(req)])
            if dependency not in versions or not _contains(req.specifier, versions[dependency]):
                errors["CLOSURE_DEPENDENCY"].append(f"{name}: {req} -> {versions.get(dependency)}")
            before = set(extras.get(dependency, set()))
            extras.setdefault(dependency, set()).update(req.extras)
            if dependency not in examined or before != extras[dependency]:
                pending.append(dependency)
    for code, found in errors.items():
        if found:
            fail(code, "; ".join(sorted(set(found))))
    unreachable = sorted(set(distributions) - set(examined))
    if unreachable:
        fail("UNREACHABLE", f"packaged but not reachable from roots: {unreachable}")
    return {"edges": edges, "extras": {k: sorted(v) for k, v in extras.items() if v}}


def test_only(names, production, joint, mapping):
    """Fail when a distribution only the test lock carries leaks into the ZIP.

    ``mapping`` maps each test-only distribution to the top-level names it installs.
    It belongs to the pinned test graph: when the joint-minus-production set changes,
    it must be re-derived from the new distributions' installed file lists.
    """
    exclusive = set(joint) - set(production)
    mapped = {canonicalize_name(name) for name in mapping}
    if exclusive != mapped:
        fail(
            "TEST_MAPPING_STALE",
            f"test-only set {sorted(exclusive)} differs from mapped {sorted(mapped)}; re-derive",
        )
    forbidden = set().union(*(set(roots) for roots in mapping.values())) if mapping else set()
    hits = []
    for name in names:
        root = PurePosixPath(name).parts[0]
        if root in forbidden:
            hits.append(name)
        elif root.endswith((".dist-info", ".egg-info")):
            dist = _dist_name(root)
            if dist in exclusive:
                hits.append(name)
    if hits:
        fail("TEST_ONLY_LEAK", ", ".join(hits[:10]))
    return {name: sorted(roots) for name, roots in sorted(mapping.items())}


def check_majors(distributions, accepted):
    """Range requirements let a rebuild move a dependency major. ``accepted`` maps each
    distribution of an accepted artifact to its major; the set and every major must hold."""
    if not isinstance(accepted, dict) or not all(
        isinstance(major, int) and not isinstance(major, bool) for major in accepted.values()
    ):
        fail("ADAPTER_CONFIG", "accepted_majors must map each distribution name to an integer major")
    actual = {name: Version(data["version"]).major for name, data in distributions.items()}
    expected = {canonicalize_name(name): major for name, major in accepted.items()}
    if actual != expected:
        added = sorted(set(actual) - set(expected))
        removed = sorted(set(expected) - set(actual))
        moved = sorted(
            f"{n} {expected[n]}->{actual[n]}" for n in set(actual) & set(expected) if actual[n] != expected[n]
        )
        fail("MAJOR_DRIFT", f"added={added} removed={removed} moved={moved}")
