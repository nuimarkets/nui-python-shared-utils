"""Every host-side predicate: the coherent artifact passes, and each defect is rejected by its own check."""

import json
import zipfile
from pathlib import Path

import pytest
from conftest import ADAPTER, PINS, base_members, elf, metadata, mutate, write_zip

from nui_lambda_validation import artifact, backstop, negatives
from nui_lambda_validation.config import Adapter
from nui_lambda_validation.errors import ValidationError
from nui_lambda_validation.extract import extract
from nui_lambda_validation.metadata import read_pins, read_roots

PINS_TEST = PINS + "pytest==8.4.2\n"


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
        ({"other.cpython-312t-x86_64-linux-gnu.so": elf()}, "NATIVE_ABI"),
        ({"other.cpython-312d-x86_64-linux-gnu.so": elf()}, "NATIVE_ABI"),
        ({"pkg\\evil.py": b""}, "UNSAFE_MEMBER"),
        ({"handler.py/nested.py": b""}, "UNSAFE_MEMBER"),
        ({"pytest-8.4.2-py3.12.egg-info/PKG-INFO": b""}, "METADATA_INVALID"),
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
    assert code_of(read_pins, tmp_path / "missing.txt") == "ADAPTER_CONFIG"


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
        "metadata-removed",
        "version-substituted",
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


def test_elf_class_checked_independently_of_machine(tmp_path, adapter):
    # ELF32 header that still carries x86_64 (62) at the e_machine offset: only the class check rejects it.
    header = bytearray(b"\x7fELF\x01\x01\x01" + b"\x00" * 13)
    header[18:20] = (62).to_bytes(2, "little")
    bad = mutate(tmp_path, {"other.cpython-312-x86_64-linux-gnu.so": bytes(header) + b"\x00" * 44})
    assert code_of(artifact.check, bad, adapter) == "NATIVE_ELF"
    not_elf = bytearray(b"MZ" + b"\x00" * 18)
    not_elf[18:20] = (62).to_bytes(2, "little")
    bad = mutate(tmp_path, {"other.cpython-312-x86_64-linux-gnu.so": bytes(not_elf)})
    assert code_of(artifact.check, bad, adapter) == "NATIVE_ELF"


def test_extension_count_is_exact(tmp_path, adapter):
    extra = mutate(tmp_path, {"helper/_more.cpython-312-x86_64-linux-gnu.so": elf()})
    with pytest.raises(ValidationError, match="2 extensions, expected 1") as caught:
        artifact.check(extra, adapter)
    assert caught.value.code == "NATIVE_EXPECTED"


def test_extraction_mismatch_detected(tmp_path, good_zip, monkeypatch):
    original = zipfile.ZipFile.extractall

    def tampering(self, path=None, *args, **kwargs):
        original(self, path, *args, **kwargs)
        (Path(path) / "handler.py").write_text("tampered\n")
        (Path(path) / "stray.py").write_text("")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", tampering)
    with pytest.raises(ValidationError) as caught:
        extract(good_zip, tmp_path / "out")
    assert caught.value.code == "EXTRACTION_MISMATCH"
    assert "stray.py" in caught.value.detail and "handler.py" in caught.value.detail


@pytest.mark.parametrize(
    "member",
    [
        "nui_lambda_validation/__init__.py",
        "vendor/nui_lambda_validation/__init__.py",
        "vendor/.venv-validation/pyvenv.cfg",
        "nui_lambda_validation-0.1.0.dist-info/METADATA",
        "vendor/nui_lambda_validation-0.1.0.dist-info/METADATA",
        "lib/nui_lambda_validation.egg-info/PKG-INFO",
        "conf/lambda-validation.toml",
        "requirements-validation.txt",
    ],
)
def test_validator_material_rejected_at_any_depth(tmp_path, adapter, member):
    assert code_of(artifact.check, mutate(tmp_path, {member: b""}), adapter) == "TOOL_IN_ARTIFACT"


def test_directory_entries_and_symlinks(tmp_path, adapter):
    members = dict(base_members(), **{"pkg/": b"", "pkg/mod.py": b""})
    artifact.check(write_zip(tmp_path / "dirs.zip", members), adapter)
    link = zipfile.ZipInfo("link.py")
    link.external_attr = (0o120777) << 16
    with zipfile.ZipFile(write_zip(tmp_path / "link.zip", base_members()), "a") as bundle:
        bundle.writestr(link, "handler.py")
    assert code_of(artifact.check, tmp_path / "link.zip", adapter) == "UNSAFE_MEMBER"


def test_corrupt_member_is_coded(tmp_path, adapter, good_zip):
    data = bytearray(good_zip.read_bytes())
    payload = elf()
    at = data.find(payload)
    data[at + 30] ^= 0xFF  # inside the stored .so body: a CRC failure on read
    bad = tmp_path / "crc.zip"
    bad.write_bytes(bytes(data))
    assert code_of(artifact.check, bad, adapter) == "UNREADABLE_ZIP"
    assert code_of(extract, bad, tmp_path / "out") == "UNREADABLE_ZIP"


def test_extract_refuses_duplicates(tmp_path):
    bad = mutate(tmp_path, duplicates=[("handler.py", b"")])
    assert code_of(extract, bad, tmp_path / "out") == "DUPLICATE_MEMBER"


def test_backstop_pattern_appears_exactly_once(tmp_path):
    line = f"grep -E '{backstop.SECRET_FILE_PATTERN}'\n"
    script = tmp_path / "check.sh"
    script.write_text(line)
    backstop.check_script(script)
    script.write_text(line + line)
    assert code_of(backstop.check_script, script) == "BACKSTOP_DRIFT"
    script.write_text(line + "grep -E '.env'\n")
    assert code_of(backstop.check_script, script) == "BACKSTOP_DRIFT"
    assert code_of(backstop.check_script, tmp_path / "missing.sh") == "BACKSTOP_DRIFT"


def test_adapter_paths_and_limits(tmp_path, repo, adapter):
    assert code_of(adapter.file, "../outside.txt") == "ADAPTER_CONFIG"
    path = repo / "lambda-validation.toml"
    for limit in ("0", str(250 * 1024 * 1024 + 1)):
        path.write_text(ADAPTER.replace("[zip]\n", f"[zip]\nuncompressed_limit = {limit}\n"))
        assert code_of(Adapter, path) == "ADAPTER_CONFIG"
    path.write_text(ADAPTER + '[dependencies.test_only]\npytest = ["pytest"]\n')
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"
    path.write_text(ADAPTER.replace('roots = "requirements.in"', 'roots = "requirements.in"\ntest_lock = "t.txt"'))
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"
    path.write_text(ADAPTER.replace('python_full_version = "3.12.16"', 'python_full_version = "3.12"'))
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"
    # Matches the X.Y prefix, so only the version-format check rejects it.
    path.write_text(ADAPTER.replace('python_full_version = "3.12.16"', 'python_full_version = "3.12.16rc1"'))
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"


def test_an_artifact_with_no_extension_fails_by_default(tmp_path, repo):
    path = repo / "lambda-validation.toml"
    path.write_text(ADAPTER.replace("extensions = 1\n", ""))
    bare = mutate(tmp_path, drop=["helper/_speed.cpython-312-x86_64-linux-gnu.so"])
    with pytest.raises(ValidationError, match="no native extensions") as caught:
        artifact.check(bare, Adapter(path))
    assert caught.value.code == "NATIVE_EXPECTED"
    path.write_text(ADAPTER.replace("extensions = 1\n", "require_any = false\n"))
    artifact.check(bare, Adapter(path))


@pytest.mark.parametrize(
    "dist_info, text",
    [
        ("helper-2.1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nVersion: 2.1.0\n"),
        ("helper-2.1.0.dist-info/METADATA", metadata("helper", "2.1.0", ["not a requirement !!"])),
        ("helper-2.1.0.dist-info/METADATA", metadata("helper", "2.1.0", requires_python="not-a-spec")),
        ("helper-2.1.0.dist-info/METADATA", metadata("helper", "two")),
    ],
)
def test_malformed_metadata_is_coded(tmp_path, adapter, dist_info, text):
    assert code_of(artifact.check, mutate(tmp_path, {dist_info: text}), adapter) == "METADATA_INVALID"


def test_versioned_egg_info_counts_as_its_distribution():
    from nui_lambda_validation.metadata import test_only

    production = {"app-lib": "1.0.0"}
    joint = dict(production, pytest="8.4.2")
    mapping = {"pytest": ["pytest"]}
    for leaked in (
        "pytest-8.4.2-py3.12.egg-info/PKG-INFO",
        "pytest.egg-info/PKG-INFO",
        "pytest-8.4.2.dist-info/RECORD",
    ):
        assert code_of(test_only, [leaked], production, joint, mapping) == "TEST_ONLY_LEAK"
    # A non-normalized hyphenated name keeps every part before the version.
    joint = dict(production, **{"pytest-cov": "6.0.0"})
    mapping = {"pytest-cov": ["pytest_cov"]}
    for leaked in ("pytest-cov-6.0.0.dist-info/RECORD", "pytest_cov-6.0.0.dist-info/RECORD"):
        assert code_of(test_only, [leaked], production, joint, mapping) == "TEST_ONLY_LEAK"


def test_root_markers_select_roots(tmp_path, repo, good_zip):
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\nold-only>=1; python_version < '3.12'\n")
    artifact.check(good_zip, adapter)
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\nx86-only>=1; platform_machine == 'x86_64'\n")
    assert code_of(artifact.check, good_zip, adapter) == "CLOSURE_ROOT"


def test_a_prerelease_never_satisfies_a_range(tmp_path, repo):
    # Strict, as the source checks were, whatever packaging's default for `in` is.
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n")
    members = base_members()
    del members["helper-2.1.0.dist-info/METADATA"]
    members["helper-2.2.0rc1.dist-info/METADATA"] = metadata("helper", "2.2.0rc1", requires_python=">=3.9")
    assert code_of(artifact.check, write_zip(tmp_path / "rc.zip", members), adapter) == "CLOSURE_DEPENDENCY"
    members = base_members()
    del members["app_lib-1.0.0.dist-info/METADATA"]
    members["app_lib-1.1rc1.dist-info/METADATA"] = metadata("app-lib", "1.1rc1", ["helper>=2,<3"])
    adapter = _roots_only(repo, "app-lib>=1,<2\nboto3>=1.34\n")
    assert code_of(artifact.check, write_zip(tmp_path / "root.zip", members), adapter) == "CLOSURE_ROOT"
    # A prerelease the requirement asks for by name is accepted, as `in` accepted it.
    adapter = _roots_only(repo, "app-lib==1.1rc1\nboto3>=1.34\n")
    artifact.check(write_zip(tmp_path / "explicit.zip", members), adapter)


def test_accepted_majors_shape(repo, good_zip):
    adapter = _roots_only(repo, "app-lib>=1\nboto3>=1.34\n", {"helper": "2"})
    assert code_of(artifact.check, good_zip, adapter) == "ADAPTER_CONFIG"
    (repo / "majors.json").write_text("not json")
    assert code_of(artifact.check, good_zip, adapter) == "ADAPTER_CONFIG"


def test_negatives_under_roots_only_and_test_only(tmp_path, repo, good_zip):
    results = negatives.run(good_zip, _roots_only(repo, "app-lib>=1\nboto3>=1.34\n"), tmp_path / "ro")
    assert results["metadata-removed"]["code"] == "CLOSURE_MISSING"
    assert "version-substituted" not in results
    (repo / "requirements-test.txt").write_text(PINS_TEST)
    (repo / "lambda-validation.toml").write_text(
        ADAPTER + 'test_lock = "requirements-test.txt"\n[dependencies.test_only]\npytest = ["pytest", "_pytest"]\n'
    )
    results = negatives.run(good_zip, Adapter(repo / "lambda-validation.toml"), tmp_path / "to")
    assert results["test-only-leak-pytest"]["code"] == "TEST_ONLY_LEAK"


def test_not_applicable_is_exact_and_recorded(tmp_path, repo, good_zip):
    path = repo / "lambda-validation.toml"
    path.write_text(ADAPTER + '[negatives.not_applicable]\nduplicate = "the packager dedupes"\n')
    results = negatives.run(good_zip, Adapter(path), tmp_path / "n1")
    assert "duplicate" not in results
    record = json.loads((tmp_path / "n1" / "negative-controls.json").read_text())
    assert record["not_applicable"] == {"duplicate": "the packager dedupes"}
    path.write_text(ADAPTER + '[negatives.not_applicable]\nduplicat = "typo"\n')
    assert code_of(negatives.run, good_zip, Adapter(path), tmp_path / "n2") == "ADAPTER_CONFIG"
    # sdk-removed cannot be built without an SDK path in [zip] required, and must be declared.
    path.write_text(ADAPTER.replace('required = ["handler.py", "boto3/__init__.py"]', 'required = ["handler.py"]'))
    assert code_of(negatives.run, good_zip, Adapter(path), tmp_path / "n3") == "ADAPTER_CONFIG"
    path.write_text(
        ADAPTER.replace('required = ["handler.py", "boto3/__init__.py"]', 'required = ["handler.py"]')
        + '[negatives.not_applicable]\nsdk-removed = "runtime-provided SDK baseline"\n'
    )
    assert "sdk-removed" not in negatives.run(good_zip, Adapter(path), tmp_path / "n4")


def test_version_substitution_must_touch_metadata_and_module(tmp_path, adapter):
    members = base_members()
    members["botocore/__init__.py"] = b"VERSION = 'elsewhere'\n"
    source = write_zip(tmp_path / "noversion.zip", members)
    assert code_of(negatives._substitute, source, tmp_path / "m.zip", "botocore-1.44.1.dist-info/METADATA") == (
        "NEGATIVE_INEFFECTIVE"
    )
    # A double-quoted module version is substituted too, so the mutant stays coherent.
    members["botocore/__init__.py"] = b'__version__ = "1.44.1"\n'
    source = write_zip(tmp_path / "double.zip", members)
    negatives._substitute(source, tmp_path / "d.zip", "botocore-1.44.1.dist-info/METADATA")
    with zipfile.ZipFile(tmp_path / "d.zip") as mutant:
        assert mutant.read("botocore/__init__.py") == b'__version__ = "1.44.2"\n'


def test_negatives_require_a_passing_base_and_named_test_only_roots(tmp_path, repo, adapter):
    leaky = mutate(tmp_path, {"config/env-dev.yml": b"x"})
    assert code_of(negatives.run, leaky, adapter, tmp_path / "n1") == "SECRET_FILE"
    (repo / "requirements-test.txt").write_text(PINS_TEST)
    (repo / "lambda-validation.toml").write_text(
        ADAPTER + 'test_lock = "requirements-test.txt"\n[dependencies.test_only]\npytest = []\n'
    )
    good = write_zip(tmp_path / "good.zip", base_members())
    assert code_of(negatives.run, good, Adapter(repo / "lambda-validation.toml"), tmp_path / "n2") == "ADAPTER_CONFIG"


def test_extraction_write_failure_is_coded(tmp_path, good_zip):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created beneath it")
    assert code_of(extract, good_zip, blocker / "out") == "UNREADABLE_ZIP"


def _corrupt_deflated(tmp_path, member):
    """Write the base artifact with ``member`` deflated, then break its deflate stream."""
    path = tmp_path / "deflated.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in base_members().items():
            bundle.writestr(name, data, zipfile.ZIP_DEFLATED if name == member else zipfile.ZIP_STORED)
    with zipfile.ZipFile(path) as bundle:
        info = bundle.getinfo(member)
    data = bytearray(path.read_bytes())
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    data[start] = 0xFF  # an invalid deflate block type: zlib.error, not a CRC failure
    path.write_bytes(bytes(data))
    with zipfile.ZipFile(path) as bundle, pytest.raises(Exception) as raw:
        bundle.read(member)
    assert type(raw.value).__module__ == "zlib"
    return path


def test_corrupt_deflated_member_is_coded(tmp_path, adapter):
    bad = _corrupt_deflated(tmp_path, "helper-2.1.0.dist-info/METADATA")
    assert code_of(artifact.check, bad, adapter) == "UNREADABLE_ZIP"
    assert code_of(extract, bad, tmp_path / "out") == "UNREADABLE_ZIP"


def test_check_zip_reads_every_member(tmp_path, adapter):
    # Not only the metadata and native headers later checks read: a corrupt source file fails too.
    bad = _corrupt_deflated(tmp_path, "handler.py")
    assert code_of(artifact.check, bad, adapter) == "UNREADABLE_ZIP"
    assert code_of(negatives.run, bad, adapter, tmp_path / "neg") == "UNREADABLE_ZIP"


@pytest.mark.parametrize("order", [("pkg", "pkg/"), ("pkg/", "pkg")])
def test_directory_entry_beside_a_file_of_the_same_name(tmp_path, adapter, order):
    members = base_members()
    for name in order:
        members[name] = b""
    assert code_of(artifact.check, write_zip(tmp_path / "clash.zip", members), adapter) == "UNSAFE_MEMBER"


@pytest.mark.parametrize(
    "member",
    [
        "nested/boto3-1.44.1.dist-info/METADATA",
        "botocore-1.44.1.dist-info/boto3-1.44.1.dist-info/METADATA",
        "vendor/helper-2.1.0.egg-info/PKG-INFO",
        "vendor/stray.dist-info/",
    ],
)
def test_distribution_metadata_below_the_top_level(tmp_path, adapter, member):
    data = metadata("boto3", "1.44.1") if member.endswith("METADATA") else b""
    bad = mutate(tmp_path, {member: data})
    with pytest.raises(ValidationError) as caught:
        artifact.check(bad, adapter)
    assert caught.value.code == "METADATA_INVALID" and "below the top level" in caught.value.detail


def test_files_inside_a_top_level_dist_info_still_pass(tmp_path, adapter):
    artifact.check(mutate(tmp_path, {"boto3-1.44.1.dist-info/licenses/LICENSE": b"MIT"}), adapter)


TOP_LEVEL = '[zip.forbidden]\nprefixes = ["tests/", "."]\ntop_level = ["scripts", "service-account.json"]\n'


def _top_level(repo):
    text = ADAPTER.replace('[zip.forbidden]\nprefixes = ["tests/", "."]\n', TOP_LEVEL)
    (repo / "lambda-validation.toml").write_text(text)
    return Adapter(repo / "lambda-validation.toml")


@pytest.mark.parametrize(
    "member, expected",
    [
        ("scripts", "FORBIDDEN_PATH"),
        ("scripts/deploy.sh", "FORBIDDEN_PATH"),
        ("scripts/", "FORBIDDEN_PATH"),
        ("service-account.json/x.txt", "FORBIDDEN_PATH"),
        ("pkg/scripts", None),
        ("scriptsx", None),
    ],
)
def test_top_level_names_are_exact_first_components(tmp_path, repo, member, expected):
    adapter = _top_level(repo)
    bad = mutate(tmp_path, {member: b""})
    if expected is None:
        artifact.check(bad, adapter)
    else:
        assert code_of(artifact.check, bad, adapter) == expected


def test_top_level_negatives_probe_the_bare_name(tmp_path, repo, good_zip, monkeypatch):
    adapter = _top_level(repo)
    added = {}
    real = negatives._copy

    def spy(source, target, add=(), drop=lambda name: False):
        added[Path(target).name] = [member for member, _ in add]
        return real(source, target, add, drop)

    monkeypatch.setattr(negatives, "_copy", spy)
    results = negatives.run(good_zip, adapter, tmp_path / "neg")
    assert results["forbidden-top_level-scripts"]["code"] == "FORBIDDEN_PATH"
    assert results["forbidden-top_level-service-account.json"]["code"] == "FORBIDDEN_PATH"
    probes = {member for members in added.values() for member in members}
    # The bare file is what the rule adds; a secret-shaped name is probed as a directory child.
    assert {"scripts", "service-account.json/x.txt"} <= probes


@pytest.mark.parametrize("names", ['["a/b"]', '[""]', '[".."]'])
def test_top_level_takes_bare_components(repo, names):
    text = ADAPTER.replace("[zip.forbidden]\n", f"[zip.forbidden]\ntop_level = {names}\n")
    (repo / "lambda-validation.toml").write_text(text)
    assert code_of(Adapter, repo / "lambda-validation.toml") == "ADAPTER_CONFIG"


def test_declared_plain_library_is_not_read_as_a_python_tag(tmp_path, repo):
    (repo / "lambda-validation.toml").write_text(
        ADAPTER.replace(
            'plain_libraries = ["vendor/libplain.so"]',
            'plain_libraries = ["vendor/libplain.so", "vendor/libcp311x.so"]',
        )
    )
    adapter = Adapter(repo / "lambda-validation.toml")
    artifact.check(mutate(tmp_path, {"vendor/libcp311x.so": elf()}), adapter)
    # Undeclared, the same kind of name still trips the foreign-tag scan; declared, its ELF is still checked.
    assert code_of(artifact.check, mutate(tmp_path, {"vendor/libcp311y.so": elf()}), adapter) == "NATIVE_ABI"
    assert code_of(artifact.check, mutate(tmp_path, {"vendor/libcp311x.so": elf(183)}), adapter) == "NATIVE_ELF"


CALENDAR_ROOTS = "app-lib>=1\nboto3>=1.34\ncertifi>=2026\n"
CALENDAR_ACCEPTED = {"app-lib": 1, "helper": 2, "boto3": 1, "botocore": 1, "s3transfer": 0, "certifi": 2026}


def _calendar(repo, accepted, calendar=None):
    adapter = _roots_only(repo, CALENDAR_ROOTS, accepted)
    if calendar is not None:
        path = repo / "lambda-validation.toml"
        rows = "".join(f'{name} = "{reason}"\n' for name, reason in calendar.items())
        path.write_text(path.read_text() + "[dependencies.calendar_versioned]\n" + rows)
        adapter = Adapter(path)
    return adapter


def test_calendar_versioned_majors_are_checked_for_presence_only(tmp_path, repo):
    members = dict(base_members(), **_dist_change("certifi", "2027.1.5"))
    members["certifi/__init__.py"] = b""
    rolled = write_zip(tmp_path / "rolled.zip", members)
    # Without the exemption a new year's certifi is major drift with no repository change.
    assert code_of(artifact.check, rolled, _calendar(repo, CALENDAR_ACCEPTED)) == "MAJOR_DRIFT"
    adapter = _calendar(repo, CALENDAR_ACCEPTED, {"Certifi": "major is the release year"})
    artifact.check(rolled, adapter)
    # A moved major that is not exempt still fails beside it.
    adapter = _calendar(repo, dict(CALENDAR_ACCEPTED, helper=3), {"certifi": "major is the release year"})
    assert code_of(artifact.check, rolled, adapter) == "MAJOR_DRIFT"


def test_calendar_versioned_presence_and_shape():
    from nui_lambda_validation.metadata import check_majors

    found = {"helper": {"version": "2.1.0"}}
    with pytest.raises(ValidationError) as caught:
        check_majors(found, {"helper": 2, "certifi": 2026}, {"certifi": "year"})
    assert caught.value.code == "MAJOR_DRIFT" and "removed=['certifi']" in caught.value.detail
    with pytest.raises(ValidationError) as caught:
        check_majors(dict(found, certifi={"version": "2027.1.5"}), {"helper": 2}, {"certifi": "year"})
    assert caught.value.code == "ADAPTER_CONFIG"


@pytest.mark.parametrize(
    "extra",
    [
        '[dependencies.calendar_versioned]\ncertifi = "year"\n',
        'accepted_majors = "majors.json"\n[dependencies.calendar_versioned]\ncertifi = " "\n',
    ],
)
def test_calendar_versioned_needs_a_snapshot_and_a_reason(repo, extra):
    (repo / "lambda-validation.toml").write_text(ADAPTER + extra)
    assert code_of(Adapter, repo / "lambda-validation.toml") == "ADAPTER_CONFIG"


@pytest.mark.parametrize(
    "members",
    [
        {"./tests/a.py": b""},
        {"./handler.py": b""},
        {"pkg//a.py": b""},
        {"pkg/./a.py": b""},
        {"pkg/sub": b"", "pkg//sub/": b""},
        {"pkg/sub//": b""},
    ],
)
def test_non_canonical_member_names(tmp_path, adapter, members):
    # Each extracts to a path the prefix rules and duplicate check, written against raw names, never see.
    assert code_of(artifact.check, mutate(tmp_path, members), adapter) == "UNSAFE_MEMBER"


@pytest.mark.parametrize(
    "name",
    [
        "other.cpython-312-aarch64-linux-gnu.so",
        "other.cpython-312-x86_64-linux-musl.so",
        "other.cpython-312.so",
    ],
)
def test_extension_platform_suffix_is_the_target(tmp_path, adapter, name):
    assert code_of(artifact.check, mutate(tmp_path, {name: elf()}), adapter) == "NATIVE_ABI"


def test_a_verify_stage_directory_is_tool_material(tmp_path, adapter):
    bad = mutate(tmp_path, {".serverless-validation-dev/app.zip": b""})
    assert code_of(artifact.check, bad, adapter) == "TOOL_IN_ARTIFACT"


@pytest.mark.parametrize(
    "old, new",
    [
        ('mode = "exact"\npins = "requirements.txt"', 'mode = "roots-only"\npins = "requirements.txt"'),
        ('roots = "requirements.in"', 'roots = ""'),
        ('roots = "requirements.in"', 'roots = "requirements.in"\naccepted_majors = ""'),
        (
            'roots = "requirements.in"',
            'roots = "requirements.in"\ntest_lock = " "\n[dependencies.test_only]\npytest = ["pytest"]',
        ),
        ("[zip.forbidden]\n", '[zip.forbidden]\ntop_level = ["scripts\\\\"]\n'),
    ],
)
def test_adapter_refuses_keys_that_would_never_be_read(repo, old, new):
    text = ADAPTER
    assert old in text
    (repo / "lambda-validation.toml").write_text(text.replace(old, new, 1))
    assert code_of(Adapter, repo / "lambda-validation.toml") == "ADAPTER_CONFIG"


@pytest.mark.parametrize("path", ["config/{other}.yml", "config/{}.yml", "config/{stage.yml"])
def test_config_path_names_only_the_stage(repo, path):
    (repo / "lambda-validation.toml").write_text(
        ADAPTER + f'[build]\nstages = ["dev"]\nconfig_path = "{path}"\nconfig_text = "x"\n'
    )
    assert code_of(Adapter, repo / "lambda-validation.toml") == "ADAPTER_CONFIG"


def test_extras_reached_later_are_walked_again(tmp_path, repo):
    # c is reached first without its extra (from a), then with [x] (from b): the [x] edge to d
    # must still be walked, so the absent d is found.
    adapter = _roots_only(repo, "b>=1\na>=1\nboto3>=1.34\n")
    members = {k: v for k, v in base_members().items() if not k.startswith(("app_lib", "helper"))}
    members["c/_speed.cpython-312-x86_64-linux-gnu.so"] = elf()
    members.update(_dist_change("a", "1.0", ["c>=1"]))
    members.update(_dist_change("b", "1.0", ["c[x]>=1"]))
    members.update(_dist_change("c", "1.0", ["d>=1; extra == 'x'"]))
    with pytest.raises(ValidationError) as caught:
        artifact.check(write_zip(tmp_path / "extras.zip", members), adapter)
    assert caught.value.code == "CLOSURE_MISSING" and caught.value.detail == "d"


def test_test_only_negative_prefers_a_non_native_root(tmp_path, repo, good_zip):
    (repo / "requirements-test.txt").write_text(PINS_TEST)
    (repo / "lambda-validation.toml").write_text(
        ADAPTER
        + 'test_lock = "requirements-test.txt"\n[dependencies.test_only]\n'
        + 'pytest = ["_cext.cpython-312-x86_64-linux-gnu.so", "pytest"]\n'
    )
    results = negatives.run(good_zip, Adapter(repo / "lambda-validation.toml"), tmp_path / "neg")
    assert results["test-only-leak-pytest"]["code"] == "TEST_ONLY_LEAK"
