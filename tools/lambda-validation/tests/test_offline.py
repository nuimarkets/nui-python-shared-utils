"""The offline runner against a throwaway project, under a hostile inherited environment."""

import sys
import textwrap

import pytest

from nui_lambda_validation import offline

HOSTILE_TESTS = """
import os, socket, sys
from pathlib import Path

BASE_ENV = __BASE_ENV__

def test_environment_is_cleared():
    assert "AWS_ACCESS_KEY_ID" not in os.environ
    assert "SECRET_TOKEN" not in os.environ
    for name, value in BASE_ENV.items():
        assert os.environ[name] == value, name
    assert not (Path(os.environ["HOME"]) / ".aws").exists()
    assert os.environ.get("KEPT") == "yes"

# Each probe carries a timeout, so a guard that regresses fails here instead of hanging the job.

def test_tcp_blocked():
    try:
        socket.create_connection(("192.0.2.1", 443), timeout=2)
    except RuntimeError:
        return
    raise AssertionError("tcp allowed")

def test_tcp_connect_ex_blocked():
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect_ex(("192.0.2.1", 443))
    except RuntimeError:
        return
    raise AssertionError("connect_ex allowed")

def test_udp_blocked():
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.1", 9))
    except RuntimeError:
        return
    raise AssertionError("udp allowed")

def test_udp_sendmsg_blocked():
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendmsg([b"x"], [], 0, ("192.0.2.1", 9))
    except RuntimeError:
        return
    raise AssertionError("sendmsg allowed")

def test_checker_directory_not_on_path():
    import importlib.util
    # -P keeps the bootstrap's own directory (the checker package) off sys.path.
    assert importlib.util.find_spec("_offline_bootstrap") is None
    assert importlib.util.find_spec("negatives") is None
    assert not any(p.endswith("elsewhere") for p in sys.path)  # inherited PYTHONPATH dropped
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_hostile.py").write_text(HOSTILE_TESTS.replace("__BASE_ENV__", repr(offline.BASE_ENV)))
    (root / "tests" / "test_skips.py").write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.skip(reason="allowed")
            def test_allowed():
                pass

            @pytest.mark.xfail(reason="expected")
            def test_xfail():
                raise AssertionError
            """
        )
    )
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text("[default]\naws_access_key_id=AKIAHOSTILE\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAHOSTILE")
    monkeypatch.setenv("SECRET_TOKEN", "hostile")
    monkeypatch.setenv("KEPT", "yes")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "elsewhere"))
    return root


def installed_plugins():
    from importlib.metadata import entry_points

    return sorted({entry.name for entry in entry_points(group="pytest11")})


def run(project, **kwargs):
    kwargs.setdefault("not_loaded", {name: "unused by this project" for name in installed_plugins()})
    return offline.run(
        sys.executable, project, ["-q", "-p", "no:cacheprovider", "tests"], passthrough=["KEPT"], **kwargs
    )


def test_hostile_environment_with_allowed_skip(project):
    assert run(project, allowed_skips=["tests/test_skips.py::test_allowed"]) == 0


def test_unexpected_skip_fails(project, capfd):
    assert run(project) == 1
    assert "UNEXPECTED_SKIP" in capfd.readouterr().out


def test_collection_skip_fails(project):
    (project / "tests" / "test_module_skip.py").write_text(
        "import pytest\npytest.skip('module', allow_module_level=True)\n"
    )
    assert run(project, allowed_skips=["tests/test_skips.py::test_allowed"]) == 1


def test_failing_test_still_fails(project):
    (project / "tests" / "test_fail.py").write_text("def test_fail():\n    assert False\n")
    assert run(project, allowed_skips=["tests/test_skips.py::test_allowed"]) == 1


def test_environment_builder_drops_unlisted():
    env = offline.environment(
        "/h", passthrough=["KEEP"], fixed={"X": "1"}, source={"PATH": "/p", "KEEP": "k", "DROP": "d"}
    )
    assert env["KEEP"] == "k" and env["X"] == "1" and "DROP" not in env and env["HOME"] == "/h"


def test_unlisted_plugin_stops_the_run(project, tmp_path, capfd):
    # A fake installed plugin: a pytest11 entry point visible to the app interpreter.
    site = tmp_path / "site"
    (site / "fakeplug-1.0.dist-info").mkdir(parents=True)
    (site / "fakeplug-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: fakeplug\nVersion: 1.0\n")
    (site / "fakeplug-1.0.dist-info" / "entry_points.txt").write_text("[pytest11]\nfakeplug = fakeplug\n")
    (site / "fakeplug.py").write_text("")
    declared = {name: "unused" for name in installed_plugins()}
    skips = ["tests/test_skips.py::test_allowed"]
    env = {"PYTHONPATH": str(site)}
    assert run(project, not_loaded=declared, allowed_skips=skips, fixed_env=env) == 3
    assert "unlisted=['fakeplug']" in capfd.readouterr().err
    assert run(project, not_loaded=dict(declared, fakeplug="unused"), allowed_skips=skips, fixed_env=env) == 0
    assert run(project, not_loaded=declared, plugins=["fakeplug"], allowed_skips=skips, fixed_env=env) == 0
    # A listed plugin that is not installed also stops the run.
    assert run(project, not_loaded=declared, plugins=["no_such_plugin"], allowed_skips=skips) == 3


@pytest.mark.parametrize(
    "passthrough, fixed",
    [
        (["HOME"], {}),
        (["AWS_SHARED_CREDENTIALS_FILE"], {}),
        ([], {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""}),
        ([], {"BOTO_CONFIG": "/x"}),
    ],
)
def test_reserved_names_cannot_be_passed_or_set(passthrough, fixed):
    from nui_lambda_validation.errors import ValidationError

    with pytest.raises(ValidationError) as caught:
        offline.environment("/h", passthrough=passthrough, fixed=fixed, source={"HOME": "/real", "PATH": "/p"})
    assert caught.value.code == "ADAPTER_CONFIG"


def test_relative_interpreter_resolves_from_the_callers_directory(project, tmp_path, monkeypatch):
    import os

    from nui_lambda_validation.errors import ValidationError

    monkeypatch.chdir(tmp_path)
    relative = os.path.relpath(sys.executable, tmp_path)
    declared = {name: "unused" for name in installed_plugins()}
    skips = ["tests/test_skips.py::test_allowed"]
    assert (
        offline.run(
            relative,
            project,
            ["-q", "-p", "no:cacheprovider", "tests"],
            allowed_skips=skips,
            passthrough=["KEPT"],
            not_loaded=declared,
        )
        == 0
    )
    with pytest.raises(ValidationError) as caught:
        offline.run(tmp_path / "no-python", project, ["tests"])
    assert caught.value.code == "OFFLINE_GUARD"
