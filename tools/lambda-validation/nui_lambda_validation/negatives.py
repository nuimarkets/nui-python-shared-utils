"""Standard negative controls: mutate an actual ZIP and require the check to reject it for the intended reason."""

import json
import re
import zipfile
from pathlib import Path

from . import artifact
from .errors import ValidationError, fail
from .native import ELF_MACHINE
from .zipcheck import SECRET_FILE_PATTERN, TOOL_PACKAGE, open_zip, read_member


def _elf(machine_code):
    header = bytearray(b"\x7fELF\x02\x01\x01" + b"\x00" * 13)
    header[18:20] = machine_code.to_bytes(2, "little")
    return bytes(header) + b"\x00" * 44


def _first(names, predicate):
    for name in names:
        if predicate(name):
            return name
    return None


# Every standard mutant. One that cannot be built from an artifact must be declared in
# `[negatives.not_applicable]` with a reason, so nothing drops out of the run unrecorded.
STANDARD = (
    "secret-file",
    "traversal",
    "duplicate",
    "tool-in-artifact",
    "foreign-abi",
    "wrong-elf",
    "undeclared-library",
    "metadata-removed",
    "sdk-removed",
)
EXACT_ONLY = ("version-substituted",)


def plans(adapter, names):
    """Each applicable mutant: name -> (expected code, mutate(source_zip, target_zip))."""
    target = adapter.target
    tag = target["python"].replace(".", "")
    other = "311" if tag != "311" else "312"
    good_elf = _elf(ELF_MACHINE[target["machine"]])
    wrong_elf = _elf(next(v for k, v in ELF_MACHINE.items() if k != target["machine"]))
    platform = f"{target['machine']}-linux-gnu"
    deps = adapter.require("dependencies")
    exact = deps["mode"] == "exact"
    found = {}

    def adding(member, data):
        return lambda src, dst: _copy(src, dst, add=[(member, data)])

    found["secret-file"] = ("SECRET_FILE", adding("config/env-dev.yml", b"synthetic: true\n"))
    found["traversal"] = ("UNSAFE_MEMBER", adding("../outside.py", b""))
    first = names[0]
    found["duplicate"] = ("DUPLICATE_MEMBER", adding(first, b""))
    found["tool-in-artifact"] = ("TOOL_IN_ARTIFACT", adding(f"{TOOL_PACKAGE}/__init__.py", b""))
    found["foreign-abi"] = ("NATIVE_ABI", adding(f"forged.cpython-{other}-{platform}.so", good_elf))
    found["wrong-elf"] = ("NATIVE_ELF", adding(f"forged.cpython-{tag}-{platform}.so", wrong_elf))
    found["undeclared-library"] = ("NATIVE_UNDECLARED_LIB", adding("forged_vendor/libforged.so", good_elf))
    metadata_dir = _first(names, lambda n: n.startswith("botocore-") and n.endswith(".dist-info/METADATA"))
    if metadata_dir and exact:
        prefix = metadata_dir.rsplit("/", 1)[0] + "/"
        found["metadata-removed"] = ("PIN_MISMATCH", lambda s, d: _copy(s, d, drop=lambda n: n.startswith(prefix)))
        found["version-substituted"] = ("PIN_MISMATCH", lambda s, d: _substitute(s, d, metadata_dir))
    elif metadata_dir:
        prefix = metadata_dir.rsplit("/", 1)[0] + "/"
        found["metadata-removed"] = ("CLOSURE_MISSING", lambda s, d: _copy(s, d, drop=lambda n: n.startswith(prefix)))
    required = adapter.get("zip").get("required", [])
    sdk = _first(required, lambda n: n.startswith(("boto3/", "botocore/", "s3transfer/")))
    if sdk:
        found["sdk-removed"] = ("MISSING_REQUIRED_PATH", lambda s, d: _copy(s, d, drop=lambda n: n == sdk))
    for dist, roots in sorted(deps.get("test_only", {}).items()):
        if not roots:
            fail("ADAPTER_CONFIG", f"[dependencies.test_only] {dist} lists no top-level names")
        root = sorted(roots)[0]
        member = root if "." in root else f"{root}/__init__.py"
        found[f"test-only-leak-{dist}"] = ("TEST_ONLY_LEAK", adding(member, b""))
    # One probe per configured exclusion rule. Probe names keep the last component
    # clear of the secret pattern, so each is rejected by the rule it exercises.
    forbidden = adapter.get("zip").get("forbidden", {})
    probes = {
        "prefixes": lambda v: f"{v}probe/x.txt",
        "parts": lambda v: f"probe/{v}/x.txt",
        "part_prefixes": lambda v: f"probe/{v}probe/x.txt",
        "suffixes": lambda v: f"probe/x{v}",
        "root_suffixes": lambda v: f"probe{v}",
        # The bare top-level file is what this rule adds over a slash prefix. A name the
        # secret rule would reject first is probed as a directory child instead.
        "top_level": lambda v: f"{v}/x.txt" if re.search(SECRET_FILE_PATTERN, v) else v,
    }
    for kind, make in probes.items():
        for value in forbidden.get(kind, ()):
            found[f"forbidden-{kind}-{value}"] = ("FORBIDDEN_PATH", adding(make(value), b""))
    reasons = adapter.get("negatives").get("not_applicable", {})
    known = set(STANDARD) | set(EXACT_ONLY if exact else ()) | set(found)
    for name, reason in reasons.items():
        if name not in known:
            fail("ADAPTER_CONFIG", f"[negatives.not_applicable] {name} is not a negative this adapter has")
        if not isinstance(reason, str) or not reason.strip():
            fail("ADAPTER_CONFIG", f"[negatives.not_applicable] {name} needs a reason")
        found.pop(name, None)
    owed = [n for n in STANDARD + (EXACT_ONLY if exact else ()) if n not in found and n not in reasons]
    if owed:
        fail(
            "ADAPTER_CONFIG",
            f"standard negatives {owed} cannot be built from this artifact; declare each in "
            "[negatives.not_applicable] with a reason",
        )
    return found


def _copy(source, target, add=(), drop=lambda name: False):
    with open_zip(source) as original, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as mutant:
        for info in original.infolist():
            if not drop(info.filename):
                mutant.writestr(info, read_member(original, info))
        for member, data in add:
            info = zipfile.ZipInfo(member)
            info.filename = member  # keep `..` and duplicates verbatim
            mutant.writestr(info, data)


def _substitute(source, target, metadata_path):
    with open_zip(source) as original:
        text = read_member(original, metadata_path).decode()
    version = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Version: "))
    head, patch = version.rsplit(".", 1)
    new = f"{head}.{int(patch) + 1}"
    package = metadata_path.split("-", 1)[0]
    changed = 0
    with open_zip(source) as original, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as mutant:
        for info in original.infolist():
            data = read_member(original, info)
            before = data
            if info.filename == metadata_path:
                data = data.replace(f"Version: {version}".encode(), f"Version: {new}".encode(), 1)
            elif info.filename == f"{package}/__init__.py":
                data = data.replace(f"'{version}'".encode(), f"'{new}'".encode(), 1)
                data = data.replace(f'"{version}"'.encode(), f'"{new}"'.encode(), 1)
            changed += data != before
            mutant.writestr(info, data)
    # Metadata and module both, or the mutant is not the coherent substitution it claims to be.
    if changed != 2:
        fail("NEGATIVE_INEFFECTIVE", f"version substitution changed {changed} of 2 members ({package} {version})")


def run(archive, adapter, output):
    """Run every applicable mutant; fail unless each is rejected with its intended code."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    artifact.check(archive, adapter)  # the unmutated artifact must pass first
    with open_zip(archive) as bundle:
        names = bundle.namelist()
    results = {}
    for index, (name, (expected, mutate)) in enumerate(sorted(plans(adapter, names).items())):
        mutant = output / f"negative-{index:02d}.zip"
        mutate(archive, mutant)
        try:
            artifact.check(mutant, adapter)
        except ValidationError as error:
            results[name] = {"expected": expected, "code": error.code, "detail": error.detail[:300]}
        else:
            results[name] = {"expected": expected, "code": None}
        mutant.unlink()
    exempt = adapter.get("negatives").get("not_applicable", {})
    record = {"results": results, "not_applicable": dict(sorted(exempt.items()))}
    (output / "negative-controls.json").write_text(json.dumps(record, indent=2) + "\n")
    wrong = {name: r for name, r in results.items() if r["code"] != r["expected"]}
    if wrong:
        fail("NEGATIVE_INEFFECTIVE", f"negative controls not rejected for their intended reason: {wrong}")
    return results
