"""Extract a ZIP and prove the tree on disk is exactly its members, byte for byte."""

import hashlib
from pathlib import Path

from .errors import fail
from .zipcheck import member_safety, open_zip


def extract(archive, destination):
    destination = Path(destination)
    if destination.exists():
        fail("EXTRACTION_MISMATCH", f"destination already exists: {destination}")
    with open_zip(archive) as bundle:
        infos = [info for info in bundle.infolist() if not info.is_dir()]
        member_safety([info.filename for info in infos])
        expected = {info.filename: hashlib.sha256(bundle.read(info)).hexdigest() for info in infos}
        bundle.extractall(destination)
    actual = {
        path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in destination.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != expected:
        extra = sorted(set(actual) - set(expected))[:10]
        missing = sorted(set(expected) - set(actual))[:10]
        changed = sorted(n for n in set(actual) & set(expected) if actual[n] != expected[n])[:10]
        fail("EXTRACTION_MISMATCH", f"extra={extra} missing={missing} changed={changed}")
    return {"files": len(actual), "all_hashes_match": True}
