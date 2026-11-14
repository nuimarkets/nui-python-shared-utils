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
        ([], {"PYTEST_ADDOPTS": "-p fakeplug"}),
        ([], {"PYTEST_ADDOPTS": "-p no:fakeplug"}),
        (["PYTEST_ADDOPTS"], {}),
        (["PYTEST_PLUGINS"], {}),
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


# The isolation contract, written out so that deleting or loosening a key in the module fails here.
EXPECTED_BASE_ENV = {
    "LANG": "C.UTF-8",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_XRAY_SDK_ENABLED": "false",
    "AWS_CONFIG_FILE": "/dev/null",
    "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
    "BOTO_CONFIG": "/dev/null",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}


def test_base_env_is_the_isolation_contract():
    assert offline.BASE_ENV == EXPECTED_BASE_ENV


XFAILS = """
import pytest

@pytest.mark.xfail(reason="expected")
def test_xfail():
    raise AssertionError

@pytest.mark.xfail(reason="flaky")
def test_xpass():
    pass
"""


@pytest.mark.parametrize("node", ["test_xfail", "test_xpass"])
def test_xfail_and_xpass_count_as_skips(project, capfd, node):
    # A JUnit skip count includes an xfail, and marking a failing test xfail must not turn the run green.
    (project / "tests" / "test_xfails.py").write_text(XFAILS)
    args = ["-q", "-p", "no:cacheprovider", f"tests/test_xfails.py::{node}"]
    declared = {name: "unused" for name in installed_plugins()}
    assert offline.run(sys.executable, project, args, not_loaded=declared) == 1
    assert f"unexpected=['tests/test_xfails.py::{node}']" in capfd.readouterr().out
    allowed = [f"tests/test_xfails.py::{node}"]
    assert offline.run(sys.executable, project, args, allowed_skips=allowed, not_loaded=declared) == 0


BOOTSTRAP_PROBE = """
import importlib.util, socket, sys
spec = importlib.util.spec_from_file_location("bootstrap", sys.argv[1])
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
seen = []

def record(*args, **kwargs):
    # Each probe must carry a timeout, or a guard that did not install hangs the run.
    if args and isinstance(args[0], socket.socket):
        seen.append(args[0].gettimeout())
    else:
        seen.append(kwargs.get("timeout"))
    raise {error}

bootstrap.blocked = record
try:
    bootstrap.guard()
finally:
    print("TIMEOUTS", seen)
"""


@pytest.mark.parametrize(
    "error, status",
    [
        ("bootstrap.OfflineBlocked('blocked')", 0),
        ("OSError('network unreachable')", 3),
        ("RuntimeError('some other error')", 3),
    ],
)
def test_guard_probe_accepts_only_its_own_refusal(tmp_path, error, status):
    import subprocess

    script = tmp_path / "probe.py"
    script.write_text(BOOTSTRAP_PROBE.format(error=error))
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), str(offline.BOOTSTRAP)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == status, result.stderr
    if status:
        assert "OFFLINE_GUARD: network guard ineffective" in result.stderr
        assert "Traceback" not in result.stderr
    else:
        assert "TIMEOUTS [2.0, 2.0, 2.0, 2.0]" in result.stdout


def fake_plugin(tmp_path):
    """An installed pytest11 plugin, visible to the app interpreter through PYTHONPATH."""
    site = tmp_path / "site"
    (site / "fakeplug-1.0.dist-info").mkdir(parents=True)
    (site / "fakeplug-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: fakeplug\nVersion: 1.0\n")
    # The entry point's name differs from its module, so each route must be matched by the right one.
    (site / "fakeplug-1.0.dist-info" / "entry_points.txt").write_text("[pytest11]\nfakeplug_ep = fakeplug\n")
    (site / "fakeplug.py").write_text("")
    return {"PYTHONPATH": str(site)}


# A conftest's pytest_plugins registers at configuration, a test module's during collection,
# an import inside a test only while tests run. One registered before the tests start must stop
# the run before any test body: MARKER is written by a test body or a runtest hook.
CONFTEST_ROUTE = "pytest_plugins = ['fakeplug']\n\ndef pytest_runtest_call(item):\n    open(MARKER, 'w').close()\n"
MODULE_ROUTE = "pytest_plugins = ['fakeplug']\n\ndef test_x():\n    open(MARKER, 'w').close()\n"
LATE_ROUTE = "def test_late(request):\n    request.config.pluginmanager.import_plugin('fakeplug')\n"


@pytest.mark.parametrize(
    "args, conftest, module, message, loaded",
    [
        (["-p", "fakeplug_ep"], None, None, "not -p in the arguments", None),
        (["-pfakeplug_ep"], None, None, "not -pfakeplug_ep in the arguments", None),
        # Registered by module name, so LOADED finds it by its entry point's module.
        ([], CONFTEST_ROUTE, None, "plugins loaded outside [offline] plugins: ['fakeplug_ep']", "['fakeplug_ep']"),
        ([], None, MODULE_ROUTE, "outside [offline] plugins: ['fakeplug_ep']", "['fakeplug_ep']"),
        ([], None, LATE_ROUTE, "outside [offline] plugins: ['fakeplug_ep']", "[]"),
    ],
)
def test_a_plugin_declared_not_loaded_cannot_load_another_way(
    project, tmp_path, capfd, args, conftest, module, message, loaded
):
    env = fake_plugin(tmp_path)
    marker = tmp_path / "a-test-ran"
    prefix = f"MARKER = {str(marker)!r}\n"
    if conftest:
        (project / "tests" / "conftest.py").write_text(prefix + conftest)
    if module:
        (project / "tests" / "test_plugin_route.py").write_text(prefix + module)
    declared = {name: "unused" for name in installed_plugins()}
    declared["fakeplug_ep"] = "declared not loaded"
    skips = ["tests/test_skips.py::test_allowed"]
    run_args = ["-q", "-p", "no:cacheprovider", *args, "tests"]
    status = offline.run(
        sys.executable, project, run_args, allowed_skips=skips, passthrough=["KEPT"], fixed_env=env, not_loaded=declared
    )
    assert status == 3
    out, err = capfd.readouterr()
    assert message in err
    if loaded is not None:
        assert f"LOADED {loaded}" in out
    assert not marker.exists(), "a test ran with a plugin the policy refused"


def object_plugin(tmp_path):
    """An installed pytest11 plugin whose entry point names an object, not a module, so pytest
    holds it only under its entry-point name. Its pytest_configure leaves a marker."""
    site = tmp_path / "site"
    marker = tmp_path / "plugin-configured"
    (site / "fakeplug-1.0.dist-info").mkdir(parents=True)
    (site / "fakeplug-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: fakeplug\nVersion: 1.0\n")
    (site / "fakeplug-1.0.dist-info" / "entry_points.txt").write_text("[pytest11]\nfakeplug_ep = fakeplug:hooks\n")
    (site / "fakeplug.py").write_text(
        "class Hooks:\n    def pytest_configure(self, config):\n"
        f"        open({str(marker)!r}, 'w').close()\n\n\nhooks = Hooks()\n"
    )
    return {"PYTHONPATH": str(site)}, marker


def run_declared(project, env, args):
    return offline.run(
        sys.executable,
        project,
        ["-q", "-p", "no:cacheprovider", *args, "tests"],
        plugins=["fakeplug_ep"],
        allowed_skips=["tests/test_skips.py::test_allowed"],
        passthrough=["KEPT"],
        fixed_env=env,
        not_loaded={name: "unused" for name in installed_plugins()},
    )


BLOCKED_IN_CONFTEST = "def pytest_configure(config):\n    config.pluginmanager.set_blocked('fakeplug_ep')\n"
# The entry point's module, registered under its own name, without the object the entry point names.
MODULE_WITHOUT_OBJECT = "pytest_plugins = ['fakeplug']\n"
# Another object put under the declared plugin's own name, after the plugin itself is removed.
IMPOSTOR_UNDER_THE_NAME = (
    "def pytest_configure(config):\n"
    "    config.pluginmanager.unregister(name='fakeplug_ep')\n"
    "    config.pluginmanager.register(object(), 'fakeplug_ep')\n"
)


@pytest.mark.parametrize(
    "args, conftest",
    [
        # After the runner's own `-p fakeplug_ep`, so pytest blocks and unregisters it.
        (["-p", "no:fakeplug_ep"], None),
        (["-pno:fakeplug_ep"], None),
        ([], BLOCKED_IN_CONFTEST),
        (["-p", "no:fakeplug_ep"], MODULE_WITHOUT_OBJECT),
        ([], IMPOSTOR_UNDER_THE_NAME),
    ],
)
def test_a_declared_plugin_that_is_not_loaded_stops_the_run(project, tmp_path, capfd, args, conftest):
    env, _ = object_plugin(tmp_path)
    if conftest:
        (project / "tests" / "conftest.py").write_text(conftest)
    # A test body that leaves a marker: the refusal must come before any test runs.
    marker = tmp_path / "a-test-ran"
    (project / "tests" / "test_marker.py").write_text(f"def test_marker():\n    open({str(marker)!r}, 'w').close()\n")
    assert run_declared(project, env, args) == 3
    out, err = capfd.readouterr()
    assert "OFFLINE_GUARD: plugins in [offline] plugins not loaded: ['fakeplug_ep']" in err
    assert "LOADED []" in out
    assert not marker.exists(), "a test ran with a declared plugin missing"


@pytest.mark.parametrize("ini", [None, "[pytest]\naddopts = -p no:fakeplug_ep\n"])
def test_a_declared_plugin_that_loads_is_reported_loaded(project, tmp_path, capfd, ini):
    # pytest puts ini addopts (and PYTEST_ADDOPTS, which the runner reserves) before the
    # arguments, so the runner's own `-p fakeplug_ep` comes later and unblocks it: an ini
    # `-p no:NAME` cannot disable a declared plugin, and the run reports it loaded.
    env, marker = object_plugin(tmp_path)
    if ini:
        (project / "pytest.ini").write_text(ini)
    assert run_declared(project, env, []) == 0
    assert "LOADED ['fakeplug_ep']" in capfd.readouterr().out
    assert marker.exists(), "the declared plugin never configured"


def test_xdist_is_refused(project, capfd):
    assert run(project, plugins=["xdist"]) == 3
    assert "xdist workers run without the network guard" in capfd.readouterr().err


@pytest.mark.parametrize(
    "extra_test, args",
    [
        ("import os\n\ndef test_a_exit():\n    os._exit(0)\n\ndef test_b_fails():\n    assert False\n", []),
        (None, ["--collect-only"]),
        (None, ["--setup-only"]),
        # A passing test first, so the run has one and the exit is what has to be caught.
        (
            "import pytest\n\ndef test_a_passes():\n    pass\n\ndef test_b_exit():\n"
            "    pytest.exit('stop', returncode=0)\n\ndef test_c_fails():\n    assert False\n",
            [],
        ),
        (
            "import pytest\n\n@pytest.fixture\ndef stop():\n    pytest.exit('stop', returncode=0)\n\n"
            "def test_a_passes():\n    pass\n\ndef test_b_exit(stop):\n    pass\n\ndef test_c_fails():\n    assert False\n",
            [],
        ),
    ],
)
def test_a_run_that_never_finished_or_ran_nothing_is_not_a_pass(project, extra_test, args):
    from nui_lambda_validation.errors import ValidationError

    if extra_test:
        (project / "tests" / "test_exit.py").write_text(extra_test)
    declared = {name: "unused" for name in installed_plugins()}
    with pytest.raises(ValidationError) as caught:
        offline.run(
            sys.executable,
            project,
            ["-q", "-p", "no:cacheprovider", *args, "tests"],
            allowed_skips=["tests/test_skips.py::test_allowed"],
            passthrough=["KEPT"],
            not_loaded=declared,
        )
    assert caught.value.code == "OFFLINE_GUARD"


def test_collection_skip_can_be_allowed_by_its_node_id(project):
    (project / "tests" / "test_module_skip.py").write_text(
        "import pytest\npytest.skip('module', allow_module_level=True)\n"
    )
    skips = ["tests/test_skips.py::test_allowed", "tests/test_module_skip.py"]
    assert run(project, allowed_skips=skips) == 0


def test_a_run_of_allowed_skips_alone_is_not_a_pass(project):
    from nui_lambda_validation.errors import ValidationError

    declared = {name: "unused" for name in installed_plugins()}
    with pytest.raises(ValidationError) as caught:
        offline.run(
            sys.executable,
            project,
            ["-q", "-p", "no:cacheprovider", "tests/test_skips.py"],
            allowed_skips=["tests/test_skips.py::test_allowed"],
            not_loaded=declared,
        )
    assert caught.value.code == "OFFLINE_GUARD" and "'ran': 0" in caught.value.detail


def test_registration_hook_runs_on_pytest_7():
    # pytest 7's pytest_plugin_registered hookspec has no plugin_name, and pluggy refuses a
    # hookimpl that asks for an argument the hookspec lacks.
    import importlib.util
    import inspect

    spec = importlib.util.spec_from_file_location("bootstrap_for_signature", offline.BOOTSTRAP)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    parameters = list(inspect.signature(bootstrap.PluginPolicy.pytest_plugin_registered).parameters)
    assert parameters == ["self", "plugin", "manager"]


# A conftest that runs one test twice, as a retry plugin does: two finish events for one item.
RETRY_CONFTEST = """
import pytest
from _pytest.runner import runtestprotocol


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name != "test_a_retried":
        return None
    for _ in range(2):
        item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
        runtestprotocol(item, nextitem=nextitem, log=True)
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
"""

RETRIED_THEN_PASSING = "def test_a_retried():\n    pass\n\ndef test_b_passes():\n    pass\n"
RETRIED_THEN_EXIT = (
    "import pytest\n\ndef test_a_retried():\n    pass\n\ndef test_b_exit():\n    pytest.exit('stop', returncode=0)\n"
)


@pytest.mark.parametrize("suite, complete", [(RETRIED_THEN_PASSING, True), (RETRIED_THEN_EXIT, False)])
def test_completion_is_per_item_not_per_finish(tmp_path, suite, complete):
    # A retried item is one finished item; its extra finish must not stand in for an unrun one.
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "retry"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(RETRY_CONFTEST)
    (root / "tests" / "test_retry.py").write_text(suite)
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    if complete:
        assert offline.run(sys.executable, root, args, not_loaded=declared) == 0
    else:
        with pytest.raises(ValidationError) as caught:
            offline.run(sys.executable, root, args, not_loaded=declared)
        assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


# The same file passed twice is collected twice, with one node id per test.
DUPLICATED = (
    "import pytest\n\nCALLS = []\n\ndef test_a_passes():\n    pass\n\ndef test_b_second_time_exits():\n"
    "    CALLS.append(1)\n    if len(CALLS) == 2:\n        pytest.exit('stop', returncode=0)\n"
)


def test_completion_counts_each_collected_item_not_each_node_id(tmp_path):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "dup"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_dup.py").write_text(DUPLICATED)
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests/test_dup.py", "tests/test_dup.py"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


# A conftest protocol override that claims an item without running it, as a skip-by-hook does.
BYPASS_CONFTEST = """
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name == "test_b_bypassed":
        return True
    return None
"""

# An override that runs setup and teardown for an item and never its call.
SETUP_TEARDOWN_CONFTEST = """
import pytest
from _pytest.runner import call_and_report


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name != "test_b_bypassed":
        return None
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    call_and_report(item, "setup", log=True)
    call_and_report(item, "teardown", log=True, nextitem=nextitem)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
"""

# An override that runs another item in place of the one it was handed.
SUBSTITUTE_CONFTEST = """
import pytest
from _pytest.runner import runtestprotocol


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name != "test_b_bypassed":
        return None
    other = next(i for i in item.session.items if i.name == "test_a_passes")
    item.ihook.pytest_runtest_logstart(nodeid=other.nodeid, location=other.location)
    runtestprotocol(other, nextitem=nextitem, log=True)
    item.ihook.pytest_runtest_logfinish(nodeid=other.nodeid, location=other.location)
    return True
"""


@pytest.mark.parametrize(
    "conftest",
    [BYPASS_CONFTEST, SUBSTITUTE_CONFTEST, SETUP_TEARDOWN_CONFTEST],
    ids=["bypass", "substitute", "setup-teardown-only"],
)
def test_completion_needs_the_item_to_run_not_only_its_protocol_to_return(tmp_path, conftest):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "bypass"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(conftest)
    (root / "tests" / "test_bypass.py").write_text(
        "def test_a_passes():\n    pass\n\ndef test_b_bypassed():\n    assert False\n"
    )
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


# A conftest that empties session.items before the runner's own sessionfinish reads it.
SHRINK_CONFTEST = """
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    session.items[:] = []
"""

# A consumer's own makereport hookwrapper, as a reporting plugin adds, around a real suite.
WRAPPER_CONFTEST = """
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    outcome.get_result().user_properties.append(("phase", call.when))
"""


def test_completion_holds_the_collected_items_against_a_later_shrink(tmp_path):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "shrink"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(SHRINK_CONFTEST)
    (root / "tests" / "test_shrink.py").write_text(
        "import pytest\n\ndef test_a_passes():\n    pass\n\ndef test_b_exit():\n"
        "    pytest.exit('stop', returncode=0)\n\ndef test_c_fails():\n    assert False\n"
    )
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


def test_completion_accepts_a_real_suite_under_a_consumer_makereport_wrapper(tmp_path):
    # A setup skip leaves no call report and still finishes its item.
    root = tmp_path / "wrapped"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(WRAPPER_CONFTEST)
    (root / "tests" / "test_wrapped.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef skipped():\n    pytest.skip('allowed')\n\n"
        "def test_a_passes():\n    pass\n\ndef test_b_setup_skip(skipped):\n    pass\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_c_param(n):\n    assert n\n"
    )
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    run = offline.run(
        sys.executable, root, args, not_loaded=declared, allowed_skips=["tests/test_wrapped.py::test_b_setup_skip"]
    )
    assert run == 0


# A conftest that appends items after collection; the first appended one ends the run.
GROW_CONFTEST = """
import pytest


def _late():
    pytest.exit("stop", returncode=0)


def _never():
    assert False


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    parent = session.items[0].parent
    session.items.append(pytest.Function.from_parent(parent, name="test_late", callobj=_late))
    session.items.append(pytest.Function.from_parent(parent, name="test_never", callobj=_never))
"""


def test_completion_includes_items_added_after_collection(tmp_path):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "grow"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(GROW_CONFTEST)
    (root / "tests" / "test_grow.py").write_text("def test_a_passes():\n    pass\n")
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


# Appends items, then removes them again before the runner's own sessionfinish reads the list.
GROW_THEN_SHRINK_CONFTEST = (
    GROW_CONFTEST
    + """

@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    session.items[:] = [item for item in session.items if item.name == "test_a_passes"]
"""
)


def test_completion_keeps_an_appended_item_that_started_after_it_is_removed(tmp_path):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "grow-shrink"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(GROW_THEN_SHRINK_CONFTEST)
    (root / "tests" / "test_grow.py").write_text("def test_a_passes():\n    pass\n")
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail


# An override that runs an item's setup and call and never its teardown, so a failing
# finalizer would never report.
SETUP_CALL_CONFTEST = """
import pytest
from _pytest.runner import call_and_report


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if item.name != "test_b_no_teardown":
        return None
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    call_and_report(item, "setup", log=True)
    call_and_report(item, "call", log=True)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
"""

# A run loop that runs the first collected item only, then drops the rest from session.items.
LOOP_DROPS_COLLECTED_CONFTEST = """
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    first = session.items[0]
    first.config.hook.pytest_runtest_protocol(item=first, nextitem=None)
    session.items[:] = [first]
    return True
"""

# A run loop that appends an item, then runs only the collected ones.
LOOP_SKIPS_APPENDED_CONFTEST = """
import pytest


def _never():
    assert False


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    collected = list(session.items)
    session.items.append(pytest.Function.from_parent(collected[0].parent, name="test_never", callobj=_never))
    for index, item in enumerate(collected):
        nextitem = collected[index + 1] if index + 1 < len(collected) else None
        item.config.hook.pytest_runtest_protocol(item=item, nextitem=nextitem)
    return True
"""


@pytest.mark.parametrize(
    "conftest, suite",
    [
        (SETUP_CALL_CONFTEST, "def test_a_passes():\n    pass\n\ndef test_b_no_teardown():\n    pass\n"),
        (LOOP_DROPS_COLLECTED_CONFTEST, "def test_a_passes():\n    pass\n\ndef test_b_dropped():\n    assert False\n"),
        (LOOP_SKIPS_APPENDED_CONFTEST, "def test_a_passes():\n    pass\n"),
    ],
    ids=["no-teardown", "loop-drops-collected", "loop-skips-appended"],
)
def test_completion_needs_every_required_item_run_through_teardown(tmp_path, conftest, suite):
    from nui_lambda_validation.errors import ValidationError

    root = tmp_path / "loop"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text(conftest)
    (root / "tests" / "test_loop.py").write_text(suite)
    declared = {name: "unused" for name in installed_plugins()}
    args = ["-q", "-p", "no:cacheprovider", "tests"]
    with pytest.raises(ValidationError) as caught:
        offline.run(sys.executable, root, args, not_loaded=declared)
    assert caught.value.code == "OFFLINE_GUARD" and "'complete': False" in caught.value.detail
