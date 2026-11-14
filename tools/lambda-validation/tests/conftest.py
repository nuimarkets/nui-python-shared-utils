import zipfile
from pathlib import Path

import pytest


def elf(machine=62):
    header = bytearray(b"\x7fELF\x02\x01\x01" + b"\x00" * 13)
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header) + b"\x00" * 44


def metadata(name, version, requires=(), requires_python=None):
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if requires_python:
        lines.append(f"Requires-Python: {requires_python}")
    lines += [f"Requires-Dist: {req}" for req in requires]
    return ("\n".join(lines) + "\n").encode()


# A small, coherent artifact: an app, a bundled SDK, one native extension and one
# declared plain library. Tests mutate copies of it.
DISTS = {
    "app-lib": ("1.0.0", ["helper>=2,<3", "extra-only>=1; extra == 'fast'"], None),
    "helper": ("2.1.0", [], ">=3.9"),
    "boto3": ("1.44.1", ["botocore<1.45.0,>=1.44.1", "s3transfer<0.20.0,>=0.19.0"], None),
    "botocore": ("1.44.1", [], None),
    "s3transfer": ("0.19.3", ["botocore<2.0a.0,>=1.37.4"], None),
}


def base_members():
    members = {
        "handler.py": b"def handler(event, context):\n    return {}\n",
        "app_lib/__init__.py": b"",
        "helper/__init__.py": b"",
        "boto3/__init__.py": b"__version__ = '1.44.1'\n",
        "botocore/__init__.py": b"__version__ = '1.44.1'\n",
        "s3transfer/__init__.py": b"__version__ = '0.19.3'\n",
        "helper/_speed.cpython-312-x86_64-linux-gnu.so": elf(),
        "vendor/libplain.so": elf(),
    }
    for name, (version, requires, requires_python) in DISTS.items():
        directory = f"{name.replace('-', '_')}-{version}.dist-info"
        members[f"{directory}/METADATA"] = metadata(name, version, requires, requires_python)
        members[f"{directory}/RECORD"] = b""
    return members


def write_zip(path, members, duplicates=()):
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
        for name, data in duplicates:
            info = zipfile.ZipInfo(name)
            info.filename = name
            bundle.writestr(info, data)
    return path


ADAPTER = """
[target]
python = "3.12"
python_full_version = "3.12.16"
machine = "x86_64"

[zip]
required = ["handler.py", "boto3/__init__.py"]
[zip.forbidden]
prefixes = ["tests/", "."]
parts = ["fixtures"]
part_prefixes = [".serverless"]
suffixes = [".tsv"]
root_suffixes = [".md"]

[native]
extensions = 1
plain_libraries = ["vendor/libplain.so"]

[dependencies]
mode = "exact"
pins = "requirements.txt"
roots = "requirements.in"
"""

PINS = "app-lib==1.0.0\nhelper==2.1.0\nboto3==1.44.1\nbotocore==1.44.1\ns3transfer==0.19.3\n"
ROOTS = "app-lib>=1\nboto3==1.44.1\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "lambda-validation.toml").write_text(ADAPTER)
    (root / "requirements.txt").write_text(PINS)
    (root / "requirements.in").write_text(ROOTS)
    return root


@pytest.fixture
def good_zip(tmp_path):
    return write_zip(tmp_path / "good.zip", base_members())


@pytest.fixture
def adapter(repo):
    from nui_lambda_validation.config import Adapter

    return Adapter(repo / "lambda-validation.toml")


def mutate(tmp_path, change=None, drop=(), duplicates=()):
    members = base_members()
    for name in drop:
        members.pop(name)
    members.update(change or {})
    return write_zip(Path(tmp_path) / "mutant.zip", members, duplicates)
