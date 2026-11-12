"""Native members of a ZIP: Python extension ABI, ELF class/endianness/machine, declared plain libraries."""

import re
from pathlib import PurePosixPath

from .errors import fail

ELF_MACHINE = {"x86_64": 62, "aarch64": 183}
_ABI_TAG = re.compile(r"\.(?:cpython-(\d{2,3})|abi3)[^/]*\.so$")
# Any CPython version tag anywhere in a native file name: `cpython-311`, `cp311`.
_FOREIGN = re.compile(r"(?:cpython-|cp)([0-9]{2,3})(?![0-9])")


def is_native(name):
    path = PurePosixPath(name)
    return path.suffix == ".so" or ".so." in path.name


def classify(name):
    """``extension`` for a CPython extension module, ``library`` for a plain shared object."""
    return "extension" if _ABI_TAG.search(name) else "library"


def elf_header(bundle, name, machine):
    header = bundle.read(name)[:20]
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01":
        fail("NATIVE_ELF", f"not ELF64 little-endian: {name}")
    found = int.from_bytes(header[18:20], "little")
    if found != ELF_MACHINE[machine]:
        fail("NATIVE_ELF", f"e_machine {found}, expected {ELF_MACHINE[machine]} ({machine}): {name}")


def inspect(bundle, python, machine, rules):
    """Check every native member against the target.

    ``rules``: ``extensions`` (expected count, or omit), ``require`` (member names
    that must be present), ``plain_libraries`` (exact member names of declared
    non-extension shared objects, such as a ctypes-loaded vendor library).
    """
    if machine not in ELF_MACHINE:
        fail("ADAPTER_CONFIG", f"unsupported machine {machine!r}")
    tag = python.replace(".", "")
    names = [name for name in bundle.namelist() if is_native(name)]
    declared = set(rules.get("plain_libraries", ()))
    extensions, libraries = [], []
    for name in names:
        for match in _FOREIGN.finditer(PurePosixPath(name).name):
            if match.group(1) != tag:
                fail("NATIVE_ABI", f"foreign Python ABI {match.group(0)}: {name}")
        if classify(name) == "extension":
            extensions.append(name)
        elif name in declared:
            libraries.append(name)
        else:
            fail("NATIVE_UNDECLARED_LIB", f"shared object without a Python ABI tag and not declared: {name}")
        elf_header(bundle, name, machine)
    missing_declared = sorted(declared - set(libraries))
    if missing_declared:
        fail("NATIVE_EXPECTED", f"declared plain libraries absent: {missing_declared}")
    expected = rules.get("extensions")
    if expected is not None and len(extensions) != expected:
        fail("NATIVE_EXPECTED", f"{len(extensions)} extensions, expected {expected}: {extensions}")
    if rules.get("require_any", True) and not extensions:
        fail("NATIVE_EXPECTED", "no native extensions")
    present = set(names)
    absent = [name for name in rules.get("require", ()) if name not in present]
    if absent:
        fail("NATIVE_EXPECTED", f"required native members absent: {absent}")
    return {"extensions": extensions, "plain_libraries": libraries, "python": python, "machine": machine}
