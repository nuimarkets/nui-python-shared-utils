"""Member-level checks on a deployment ZIP: safety, secrets, forbidden and required paths, size."""

import hashlib
import re
import zipfile
import zlib
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
# Distribution metadata directories (`nui_lambda_validation-0.1.0.dist-info`, `.egg-info`).
VERIFY_OUTPUT = ".serverless-validation-"
TOOL_DIST_PREFIXES = tuple(f"{name}{sep}" for name in (TOOL_PACKAGE, "nui-lambda-validation") for sep in "-.")


def open_zip(archive):
    try:
        return zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as error:
        fail("UNREADABLE_ZIP", f"{archive}: {error}")


def member_safety(names):
    """Unique, relative, no traversal, and no file that is also a directory: a parent of
    another member or an explicit ``name/`` entry (it could not be extracted as both).
    Applies to every artifact."""
    seen = set()
    parents = set()
    for name in names:
        if name in seen:
            fail("DUPLICATE_MEMBER", name)
        seen.add(name)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            fail("UNSAFE_MEMBER", name)
        # Canonical names only: `./tests/a.py` or `pkg//a.py` extracts to a path no prefix rule or
        # duplicate check written against the raw name would see.
        if not path.parts or path.as_posix() != (name[:-1] if name.endswith("/") else name):
            fail("UNSAFE_MEMBER", f"not a canonical member name: {name!r}")
        parents.update(parent.as_posix() for parent in path.parents if parent.as_posix() != ".")
        if name.endswith("/"):
            parents.add(name[:-1])
    collisions = sorted(name for name in seen if not name.endswith("/") and name in parents)
    if collisions:
        fail("UNSAFE_MEMBER", f"file is also a directory of other members: {collisions[:10]}")


def no_symlinks(infos):
    """A symlink member extracts as a regular file here, so the tree the checks see would
    differ from what an unzip that keeps links produces."""
    links = [info.filename for info in infos if (info.external_attr >> 16) & 0o170000 == 0o120000]
    if links:
        fail("UNSAFE_MEMBER", f"symlink members: {links[:10]}")


def read_member(bundle, member):
    """Read one member; a corrupt or unsupported member fails as UNREADABLE_ZIP."""
    try:
        return bundle.read(member)
    except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error) as error:
        name = getattr(member, "filename", member)
        fail("UNREADABLE_ZIP", f"{name}: {error}")


def secret_files(names):
    hits = [name for name in names if _SECRET.search(name)]
    if hits:
        fail("SECRET_FILE", ", ".join(hits[:10]))


def _forbidden(name, rules):
    """``prefixes``: whole-path string prefixes. ``parts``: any exact path component.
    ``part_prefixes``: any component starting with one. ``suffixes``: whole-path endings.
    ``root_suffixes``: endings of a top-level file only. ``top_level``: the first path
    component equals the value exactly, so a bare top-level file of that name is caught too."""
    path = PurePosixPath(name)
    parts = path.parts
    if parts and parts[0] in rules.get("top_level", ()):
        return "top level"
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


def _tool_member(name):
    """Validation-only material at any depth: the package, its venv, its dist-info or
    egg-info under either spelling, or an adapter file."""
    parts = PurePosixPath(name).parts
    if parts and parts[-1] in TOOL_FILES:
        return True
    # `.serverless-validation-<stage>` is where verify packages each stage: a later stage that
    # swept it up would ship the earlier stage's ZIP.
    return any(
        part in (TOOL_PACKAGE, ".venv-validation") or part.startswith(TOOL_DIST_PREFIXES + (VERIFY_OUTPUT,))
        for part in parts
    )


def forbidden_paths(names, rules):
    hits = [f"{name} ({why})" for name in names if (why := _forbidden(name, rules))]
    tool = [name for name in names if _tool_member(name)]
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
        no_symlinks(infos)
        secret_files(names)
        forbidden_paths(names, rules.get("forbidden", {}))
        required_paths(names, rules.get("required", ()))
        total = size(infos, rules.get("uncompressed_limit", LAMBDA_UNZIPPED_LIMIT))
        # Every member decompresses and matches its CRC, not only the ones later checks read.
        for info in infos:
            read_member(bundle, info)
    return dict(
        identity(archive),
        uncompressed_bytes=total,
        members=len(names),
        layers="not included",
    )
