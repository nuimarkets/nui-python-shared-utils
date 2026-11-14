"""Deploy-time secret backstop: the same refusal rules as the shell copy, and a drift check on it."""

import re
from pathlib import Path

from .errors import fail
from .zipcheck import SECRET_FILE_PATTERN, open_zip, secret_files


def secret_scan(package_dir):
    """Refuse a package directory with no ZIP, an unreadable ZIP, or a ZIP carrying a secret file."""
    zips = sorted(Path(package_dir).glob("*.zip"))
    if not zips:
        fail("NO_PACKAGE", f"no zip found in {package_dir}")
    for archive in zips:
        with open_zip(archive) as bundle:
            secret_files(bundle.namelist())
    return {"zips": [str(z) for z in zips]}


_GREP = re.compile(r"grep -E '([^']+)'")


def check_script(script):
    """The shell backstop must grep for exactly the canonical pattern, once."""
    try:
        text = Path(script).read_text()
    except OSError as error:
        fail("BACKSTOP_DRIFT", f"{script}: {error}")
    patterns = _GREP.findall(text)
    if patterns != [SECRET_FILE_PATTERN]:
        fail("BACKSTOP_DRIFT", f"{script}: grep -E patterns {patterns} != [{SECRET_FILE_PATTERN!r}]")
    return {"script": str(script), "pattern": SECRET_FILE_PATTERN}
