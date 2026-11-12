"""Every host-side predicate: the coherent artifact passes, and each defect is rejected by its own check."""

import json
import zipfile

import pytest
from conftest import ADAPTER, base_members, elf, metadata, mutate, write_zip

from nui_lambda_validation import artifact, backstop, negatives
from nui_lambda_validation.config import Adapter
from nui_lambda_validation.errors import ValidationError
from nui_lambda_validation.extract import extract
from nui_lambda_validation.metadata import read_pins, read_roots


def code_of(call, *args):
    with pytest.raises(ValidationError) as caught:
        call(*args)
    return caught.value.code


def test_coherent_artifact_passes(good_zip, adapter):
    report = artifact.check(good_zip, adapter)
    assert report["distributions"]["helper"] == "2.1.0"
    assert report["native"]["plain_libraries"] == ["vendor/libplain.so"]
    assert report["layers"] == "not included"


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"config/env-dev.yml": b"x"}, "SECRET_FILE"),
        ({"deep/.env.prod": b"x"}, "SECRET_FILE"),
        ({"service-account.json": b"{}"}, "SECRET_FILE"),
        ({"../escape.py": b""}, "UNSAFE_MEMBER"),
        ({"/abs.py": b""}, "UNSAFE_MEMBER"),
        ({"tests/test_x.py": b""}, "FORBIDDEN_PATH"),
        ({".hidden/x": b""}, "FORBIDDEN_PATH"),
        ({"pkg/fixtures/x.json": b""}, "FORBIDDEN_PATH"),
        ({"pkg/.serverless-x/y": b""}, "FORBIDDEN_PATH"),
        ({"pkg/rows.tsv": b""}, "FORBIDDEN_PATH"),
        ({"README.md": b""}, "FORBIDDEN_PATH"),
        ({"nui_lambda_validation/__init__.py": b""}, "TOOL_IN_ARTIFACT"),
        ({"lambda-validation.toml": b""}, "TOOL_IN_ARTIFACT"),
        ({"requirements-validation.txt": b""}, "TOOL_IN_ARTIFACT"),
        ({"other.cpython-311-x86_64-linux-gnu.so": elf()}, "NATIVE_ABI"),
        ({"other_cp311.so": elf()}, "NATIVE_ABI"),
        ({"other.cpython-312-x86_64-linux-gnu.so": elf(183)}, "NATIVE_ELF"),
        ({"other.cpython-312-x86_64-linux-gnu.so": b"\x7fELF\x01\x01" + b"\0" * 40}, "NATIVE_ELF"),
        ({"vendor/libother.so": elf()}, "NATIVE_UNDECLARED_LIB"),
        ({"vendor/libother.so.1.2": elf()}, "NATIVE_UNDECLARED_LIB"),
    ],
)
def test_member_and_native_defects(tmp_path, adapter, change, expected):
    assert code_of(artifact.check, mutate(tmp_path, change), adapter) == expected


def test_duplicate_member(tmp_path, adapter):
    bad = mutate(tmp_path, duplicates=[("handler.py", b"")])
    assert code_of(artifact.check, bad, adapter) == "DUPLICATE_MEMBER"


def test_missing_required_and_declared_native(tmp_path, adapter):
    assert code_of(artifact.check, mutate(tmp_path, drop=["boto3/__init__.py"]), adapter) == "MISSING_REQUIRED_PATH"
    assert code_of(artifact.check, mutate(tmp_path, drop=["vendor/libplain.so"]), adapter) == "NATIVE_EXPECTED"
    gone = ["helper/_speed.cpython-312-x86_64-linux-gnu.so"]
    assert code_of(artifact.check, mutate(tmp_path, drop=gone), adapter) == "NATIVE_EXPECTED"


def test_size_limit(tmp_path, repo):
    text = (repo / "lambda-validation.toml").read_text().replace("[zip]\n", "[zip]\nuncompressed_limit = 10\n")
    (repo / "lambda-validation.toml").write_text(text)
    with pytest.raises(ValidationError, match="layers not included") as caught:
        artifact.check(mutate(tmp_path), Adapter(repo / "lambda-validation.toml"))
    assert caught.value.code == "SIZE_LIMIT"


def _dist_change(name, version, requires=(), requires_python=None):
    directory = f"{name.replace('-', '_')}-{version}.dist-info"
    return {f"{directory}/METADATA": metadata(name, version, requires, requires_python)}


def test_pin_mismatch_on_removed_extra_or_substituted(tmp_path, adapter):
    removed = mutate(tmp_path, drop=["helper-2.1.0.dist-info/METADATA"])
    assert code_of(artifact.check, removed, adapter) == "PIN_MISMATCH"
    extra = mutate(tmp_path, _dist_change("stray", "1.0"))
    assert code_of(artifact.check, extra, adapter) == "PIN_MISMATCH"
    members = base_members()
    members["helper-2.1.0.dist-info/METADATA"] = metadata("helper", "2.1.1")
    assert code_of(artifact.check, write_zip(tmp_path / "sub.zip", members), adapter) == "PIN_MISMATCH"


def test_duplicate_metadata(tmp_path, adapter):
    bad = mutate(tmp_path, {"Helper-2.1.0.dist-info/METADATA": metadata("Helper", "2.1.0")})
    assert code_of(artifact.check, bad, adapter) == "METADATA_DUPLICATE"


def _roots_only(repo, roots, majors=None):
    text = ADAPTER
    text = text.replace(
        'mode = "exact"\npins = "requirements.txt"\nroots = "requirements.in"',
        'mode = "roots-only"\nroots = "roots.txt"',
    )
    if majors is not None:
        (repo / "majors.json").write_text(json.dumps(majors))
        text += 'accepted_majors = "majors.json"\n'
    (repo / "roots.txt").write_text(roots)
    (repo / "lambda-validation.toml").write_text(text)
    return Adapter(repo / "lambda-validation.toml")


def test_roots_only_closure(tmp_path, repo, good_zip):
    adapter = _roots_only(repo, "app-lib>=1,<2\nboto3>=1.34,<2\n")
    assert artifact.check(good_zip, adapter)["dependency_mode"] == "roots-only"
    # version outside a dependency's range
    members = base_members()
    members["helper-2.1.0.dist-info/METADATA"] = metadata("helper", "3.0.0")
    assert code_of(artifact.check, write_zip(tmp_path / "v.zip", members), adapter) == "CLOSURE_DEPENDENCY"
    # a reachable dependency without metadata
    gone = mutate(tmp_path, drop=["helper-2.1.0.dist-info/METADATA"])
    assert code_of(artifact.check, gone, adapter) == "CLOSURE_MISSING"
    # packaged but unreachable
    stray = mutate(tmp_path, _dist_change("stray", "1.0"))
    assert code_of(artifact.check, stray, adapter) == "UNREACHABLE"
    # root outside its range
    adapter = _roots_only(repo, "app-lib>=2\nboto3>=1.34,<2\n")
    assert code_of(artifact.check, good_zip, adapter) == "CLOSURE_ROOT"


def test_extras_propagate_and_requires_python(tmp_path, repo, good_zip):
    adapter = _roots_only(repo, "app-lib[fast]>=1\nboto3>=1.34\n")
    assert code_of(artifact.check, good_zip, adapter) == "CLOSURE_MISSING"  # extra-only is absent
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n")
    members = base_members()
    members["helper-2.1.0.dist-info/METADATA"] = metadata("helper", "2.1.0", requires_python=">=3.13")
    assert code_of(artifact.check, write_zip(tmp_path / "rp.zip", members), adapter) == "REQUIRES_PYTHON"


def test_target_markers_select_requirements(tmp_path, repo):
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n")
    members = base_members()
    members["app_lib-1.0.0.dist-info/METADATA"] = metadata(
        "app-lib",
        "1.0.0",
        ["helper>=2,<3", "old-only>=1; python_version < '3.12'", "x86-only>=1; platform_machine == 'x86_64'"],
    )
    # old-only is inactive on 3.12; x86-only is active on x86_64 and absent
    with pytest.raises(ValidationError) as caught:
        artifact.check(write_zip(tmp_path / "m.zip", members), adapter)
    assert caught.value.code == "CLOSURE_MISSING" and caught.value.detail == "x86-only"


def test_major_drift(tmp_path, repo, good_zip):
    accepted = {"app-lib": 1, "helper": 2, "boto3": 1, "botocore": 1, "s3transfer": 0}
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n", accepted)
    artifact.check(good_zip, adapter)
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n", dict(accepted, helper=3))
    assert code_of(artifact.check, good_zip, adapter) == "MAJOR_DRIFT"


def test_test_only_leak_and_stale_mapping(tmp_path, repo, good_zip):
    (repo / "requirements-test.txt").write_text(
        (repo / "requirements.txt").read_text() + "pytest==8.4.2\npluggy==1.6.0\n"
    )
    text = (
        repo / "lambda-validation.toml"
    ).read_text() + 'test_lock = "requirements-test.txt"\n[dependencies.test_only]\npytest = ["pytest", "_pytest", "py.py"]\npluggy = ["pluggy"]\n'
    (repo / "lambda-validation.toml").write_text(text)
    adapter = Adapter(repo / "lambda-validation.toml")
    assert artifact.check(good_zip, adapter)["test_only_exclusions"]["pytest"] == ["_pytest", "py.py", "pytest"]
    assert code_of(artifact.check, mutate(tmp_path, {"_pytest/x.py": b""}), adapter) == "TEST_ONLY_LEAK"
    assert code_of(artifact.check, mutate(tmp_path, {"py.py": b""}), adapter) == "TEST_ONLY_LEAK"
    assert (
        code_of(artifact.check, mutate(tmp_path, {"pluggy-1.6.0.dist-info/RECORD": b""}), adapter) == "TEST_ONLY_LEAK"
    )
    (repo / "requirements-test.txt").write_text((repo / "requirements-test.txt").read_text() + "iniconfig==2.1.0\n")
    assert code_of(artifact.check, good_zip, Adapter(repo / "lambda-validation.toml")) == "TEST_MAPPING_STALE"


@pytest.mark.parametrize(
    "text",
    [
        "helper>=2\n",
        "helper==2.1.0; python_version >= '3.12'\n",
        "helper==2.1.0\nHelper==2.1.0\n",
        "helper==2.1.0,<3\n",
        "# only a comment\n",
    ],
)
def test_strict_pin_parser(tmp_path, text):
    path = tmp_path / "pins.txt"
    path.write_text(text)
    assert code_of(read_pins, path) == "PIN_FORMAT"


def test_pin_parser_accepts_compiled_lock(tmp_path):
    path = tmp_path / "pins.txt"
    path.write_text("# generated\nboto3==1.44.1\n    # via app\nhelper==2.1.0  # pinned\n")
    assert read_pins(path) == {"boto3": "1.44.1", "helper": "2.1.0"}
    roots = tmp_path / "roots.txt"
    roots.write_text("-r other.txt\n")
    assert code_of(read_roots, roots) == "PIN_FORMAT"


def test_extract_proves_every_member(tmp_path, good_zip):
    assert extract(good_zip, tmp_path / "out")["all_hashes_match"]
    assert code_of(extract, good_zip, tmp_path / "out") == "EXTRACTION_MISMATCH"  # destination reused


def test_backstop_scan_and_drift(tmp_path, good_zip):
    package = tmp_path / "pkg"
    package.mkdir()
    assert code_of(backstop.secret_scan, package) == "NO_PACKAGE"
    (package / "broken.zip").write_bytes(b"not a zip")
    assert code_of(backstop.secret_scan, package) == "UNREADABLE_ZIP"
    (package / "broken.zip").unlink()
    write_zip(package / "a.zip", base_members())
    backstop.secret_scan(package)
    write_zip(package / "b.zip", dict(base_members(), **{"env-prod.yaml": b""}))
    assert code_of(backstop.secret_scan, package) == "SECRET_FILE"
    script = tmp_path / "check.sh"
    script.write_text(
        "printf '%s\\n' \"$l\" | grep -E '(^|/)(env-[^/]*\\.ya?ml|\\.env[^/]*|service-account\\.json)$' || s=$?\n"
    )
    backstop.check_script(script)
    script.write_text(script.read_text().replace("service-account", "svc-account"))
    assert code_of(backstop.check_script, script) == "BACKSTOP_DRIFT"


def test_standard_negatives_reach_intended_codes(tmp_path, adapter, good_zip):
    results = negatives.run(good_zip, adapter, tmp_path / "neg")
    assert all(r["code"] == r["expected"] for r in results.values())
    expected = {
        "secret-file",
        "traversal",
        "duplicate",
        "tool-in-artifact",
        "foreign-abi",
        "wrong-elf",
        "undeclared-library",
        "sdk-removed",
    }
    assert expected <= set(results)
    assert {name for name in results if name.startswith("forbidden-")} == {
        "forbidden-prefixes-tests/",
        "forbidden-prefixes-.",
        "forbidden-parts-fixtures",
        "forbidden-part_prefixes-.serverless",
        "forbidden-suffixes-.tsv",
        "forbidden-root_suffixes-.md",
    }


def test_negative_harness_detects_an_ineffective_check(tmp_path, adapter, good_zip, monkeypatch):
    from nui_lambda_validation import native

    monkeypatch.setattr(native, "elf_header", lambda *a: None)  # a checker that lost its ELF guard
    with pytest.raises(ValidationError) as caught:
        negatives.run(good_zip, adapter, tmp_path / "neg")
    assert caught.value.code == "NEGATIVE_INEFFECTIVE"
    assert "wrong-elf" in caught.value.detail


def test_not_applicable_needs_reason(tmp_path, repo, good_zip):
    (repo / "lambda-validation.toml").write_text(
        (repo / "lambda-validation.toml").read_text() + '[negatives.not_applicable]\nduplicate = ""\n'
    )
    assert (
        code_of(negatives.run, good_zip, Adapter(repo / "lambda-validation.toml"), tmp_path / "n") == "ADAPTER_CONFIG"
    )


def test_adapter_config_is_strict(tmp_path, repo):
    path = repo / "lambda-validation.toml"
    path.write_text(path.read_text() + "[surprise]\n")
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"
    path.write_text(
        path.read_text()
        .replace("[surprise]\n", "")
        .replace('python_full_version = "3.12.16"', 'python_full_version = "3.11.9"')
    )
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"


def test_unreadable_zip(tmp_path, adapter):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 truncated")
    assert code_of(artifact.check, bad, adapter) == "UNREADABLE_ZIP"
    assert zipfile.is_zipfile(tmp_path / "bad.zip") is False
