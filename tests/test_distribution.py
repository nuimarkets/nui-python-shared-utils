"""Regression checks for the build-only distribution contract gate."""

import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

pytest.importorskip("tomllib", reason="The build-only checker runs on Python 3.11+")


@pytest.fixture
def checker():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_distribution.py"
    spec = importlib.util.spec_from_file_location("distribution_checker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project():
    return {
        "name": "fixture-utils",
        "requires-python": ">=3.10",
        "dependencies": ["example>=1"],
        "optional-dependencies": {"feature": ["demo>=2; python_version < '3.12'"]},
    }


METADATA = (
    b"Metadata-Version: 2.4\nName: fixture-utils\nVersion: 1.0\nRequires-Python: >=3.10\n"
    b"Requires-Dist: example>=1\n"
    b'Requires-Dist: demo>=2; python_version < "3.12" and extra == "feature"\n'
    b"Provides-Extra: feature\n\n"
)


def test_metadata_accepts_declared_requirements_and_extra_marker(checker, project):
    checker.check_metadata(METADATA, project)


@pytest.mark.parametrize(
    "old,new,message",
    [
        (b"fixture-utils", b"wrong-name", "Distribution name differs"),
        (b">=3.10", b">=3.11", "Python requirement differs"),
        (b"Requires-Dist: example>=1\n", b"", "Dependency metadata differs"),
        (b"example>=1", b"example>=9", "Dependency metadata differs"),
        (b' and extra == "feature"', b"", "Dependency metadata differs"),
        (b"Provides-Extra: feature\n", b"", "Published extras differ"),
    ],
)
def test_metadata_rejects_contract_drift(checker, project, old, new, message):
    with pytest.raises(SystemExit, match=message):
        checker.check_metadata(METADATA.replace(old, new), project)


def write_archives(dist, wheel_files, source_files):
    with zipfile.ZipFile(dist / "fixture.whl", "w") as archive:
        for name, data in wheel_files.items():
            archive.writestr(name, data)
    with tarfile.open(dist / "fixture.tar.gz", "w:gz") as archive:
        for name, data in source_files.items():
            member = tarfile.TarInfo("fixture-1.0/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize(
    "damage,message",
    [
        (None, None),
        ("wheel_dependency", "Dependency metadata differs"),
        ("wheel_python", "Wheel payload differs"),
        ("wheel_data", "Wheel payload differs"),
        ("wheel_validator", "Wheel payload differs"),
        ("cli", "CLI entry points differ"),
        ("source_python", "Source distribution omits package files"),
        ("source_validator", "Source distribution includes the separate validator"),
        ("source_dependency", "Dependency metadata differs"),
    ],
)
def test_archive_contracts(checker, tmp_path, monkeypatch, capsys, damage, message):
    root = tmp_path / "project"
    package = root / "nui_shared_utils"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sample.txt").write_text("package data")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture-utils"\nrequires-python = ">=3.10"\n'
        'dependencies = ["example>=1"]\n'
        "[project.optional-dependencies]\nfeature = [\"demo>=2; python_version < '3.12'\"]\n"
        '[project.scripts]\nfixture = "nui_shared_utils:main"\n'
        '[tool.setuptools.package-data]\nnui_shared_utils = ["*.txt"]\n'
    )
    info = "fixture_utils-1.0.dist-info/"
    wheel_files = {
        "nui_shared_utils/__init__.py": b"",
        "nui_shared_utils/sample.txt": b"package data",
        info + "METADATA": METADATA,
        info + "entry_points.txt": b"[console_scripts]\nfixture = nui_shared_utils:main\n",
    }
    source_files = {
        "nui_shared_utils/__init__.py": b"",
        "nui_shared_utils/sample.txt": b"package data",
        "PKG-INFO": METADATA,
    }
    if damage == "wheel_dependency":
        wheel_files[info + "METADATA"] = METADATA.replace(b"example>=1", b"example>=9")
    elif damage == "wheel_python":
        del wheel_files["nui_shared_utils/__init__.py"]
    elif damage == "wheel_data":
        del wheel_files["nui_shared_utils/sample.txt"]
    elif damage == "wheel_validator":
        wheel_files["nui_lambda_validation/__init__.py"] = b""
    elif damage == "cli":
        wheel_files[info + "entry_points.txt"] = b"[console_scripts]\nfixture = wrong:main\n"
    elif damage == "source_python":
        del source_files["nui_shared_utils/__init__.py"]
    elif damage == "source_validator":
        source_files["tools/lambda-validation/pyproject.toml"] = b""
    elif damage == "source_dependency":
        source_files["PKG-INFO"] = METADATA.replace(b"example>=1", b"example>=9")
    dist = tmp_path / "dist"
    dist.mkdir()
    write_archives(dist, wheel_files, source_files)
    monkeypatch.setattr(checker, "__file__", str(root / "scripts" / "check_distribution.py"))
    monkeypatch.setattr(sys, "argv", ["check_distribution.py", str(dist)])
    if message:
        with pytest.raises(SystemExit, match=message):
            checker.main()
    else:
        checker.main()
        assert "Distribution contracts passed: 2 package files, 1 extras" in capsys.readouterr().out
