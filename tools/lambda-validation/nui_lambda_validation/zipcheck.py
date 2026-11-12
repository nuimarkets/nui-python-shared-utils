"""Member-level checks on a deployment ZIP: safety, secrets, forbidden and required paths, size."""

import hashlib
import re
import zipfile
from pathlib import Path, PurePosixPath

from .errors import fail

# The canonical secret-file rule. Deploy-time shell backstops copy this exact
# pattern into `grep -E`; `backstop.check_script` fails CI when a copy drifts.
SECRET_FILE_PATTERN = r"(^|/)(env-[^/]*\.ya?ml|\.env[^/]*|service-account\.json)$"
_SECRET = re.compile(SECRET_FILE_PATTERN)

LAMBDA_UNZIPPED_LIMIT = 250 * 1024 * 1024
TOOL_PACKAGE = "nui_lambda_validation"
# Validation-only files an adapter keeps in its repository. None may ship.
TOOL_FILES = ("lambda-validation.toml", "requirements-validation.txt")


def open_zip(archive):
    try:
        return zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as error:
        fail("UNREADABLE_ZIP", f"{archive}: {error}")


def member_safety(names):
    """Unique, relative, no traversal. Applies to every artifact."""
    seen = set()
    for name in names:
        if name in seen:
            fail("DUPLICATE_MEMBER", name)
        seen.add(name)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            fail("UNSAFE_MEMBER", name)


def secret_files(names):
    hits = [name for name in names if _SECRET.search(name)]
    if hits:
        fail("SECRET_FILE", ", ".join(hits[:10]))


def _forbidden(name, rules):
    """``prefixes``: whole-path string prefixes. ``parts``: any exact path component.
    ``part_prefixes``: any component starting with one. ``suffixes``: whole-path endings.
    ``root_suffixes``: endings of a top-level file only."""
    path = PurePosixPath(name)
    parts = path.parts
    if any(name.startswith(prefix) for prefix in rules.get("prefixes", ())):
        return "prefix"
    if any(part in rules.get("parts", ()) for part in parts):
        return "part"
    if any(part.startswith(tuple(rules.get("part_prefixes", ()))) for part in parts):
        return "part prefix"
    if name.endswith(tuple(rules.get("suffixes", ()))):
        return "suffix"
    if len(parts) == 1 and name.endswith(tuple(rules.get("root_suffixes", ()))):
        return "root suffix"
    return None


def forbidden_paths(names, rules):
    hits = [f"{name} ({why})" for name in names if (why := _forbidden(name, rules))]
    tool = [
        name
        for name in names
        if PurePosixPath(name).parts[0] in (TOOL_PACKAGE, ".venv-validation")
        or PurePosixPath(name).name in TOOL_FILES
        or PurePosixPath(name).parts[0].startswith("nui_lambda_validation-")
    ]
    if tool:
        fail("TOOL_IN_ARTIFACT", ", ".join(tool[:10]))
    if hits:
        fail("FORBIDDEN_PATH", ", ".join(hits[:10]))


def required_paths(names, required):
    present = set(names)
    missing = [name for name in required if name not in present]
    if missing:
        fail("MISSING_REQUIRED_PATH", ", ".join(missing))


def size(infos, limit):
    total = sum(info.file_size for info in infos)
    if total >= limit:
        fail("SIZE_LIMIT", f"{total} uncompressed bytes >= {limit} (layers not included)")
    return total


def identity(archive):
    data = Path(archive).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "zip_bytes": len(data)}


def inspect(archive, rules):
    """Run every member-level check. ``rules`` is the adapter's ``[zip]`` table."""
    with open_zip(archive) as bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        member_safety(names)
        secret_files(names)
        forbidden_paths(names, rules.get("forbidden", {}))
        required_paths(names, rules.get("required", ()))
        total = size(infos, rules.get("uncompressed_limit", LAMBDA_UNZIPPED_LIMIT))
    return dict(
        identity(archive),
        uncompressed_bytes=total,
        members=len(names),
        layers="not included",
    )
