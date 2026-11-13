"""Run inside the application's own interpreter as ``python -P _offline_bootstrap.py``.

Stdlib only until pytest, which comes from the application's environment. The
launcher (``offline.run``) has already replaced the process environment. This
file is passed by path, never imported, so the checker package is not on the
application's ``sys.path``: ``-P`` stops its directory being prepended.
"""

import json
import os
import socket
import sys


class OfflineBlocked(RuntimeError):
    """Raised by the guard. A RuntimeError, so suites that expect one keep working, but its own
    class, so a probe can tell the guard's refusal from any other error."""


def blocked(*args, **kwargs):
    raise OfflineBlocked("offline test: outbound networking blocked")


PROBE_TIMEOUT = 2.0


def _sock(kind):
    sock = socket.socket(socket.AF_INET, kind)
    sock.settimeout(PROBE_TIMEOUT)
    return sock


def guard():
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    socket.socket.sendmsg = blocked
    socket.create_connection = blocked
    # Each probe carries a timeout, so a guard that did not install fails here rather than
    # hanging on an unroutable address; any outcome but the guard's own refusal stops the run.
    for probe in (
        lambda: socket.create_connection(("192.0.2.1", 443), timeout=PROBE_TIMEOUT),
        lambda: _sock(socket.SOCK_STREAM).connect(("192.0.2.1", 443)),
        lambda: _sock(socket.SOCK_DGRAM).sendto(b"probe", ("192.0.2.1", 9)),
        lambda: _sock(socket.SOCK_DGRAM).sendmsg([b"probe"], [], 0, ("192.0.2.1", 9)),
    ):
        try:
            probe()
        except OfflineBlocked:
            continue
        except Exception as error:
            print(f"OFFLINE_GUARD: network guard ineffective: {type(error).__name__}: {error}", file=sys.stderr)
            raise SystemExit(3) from None
        print("OFFLINE_GUARD: network guard ineffective", file=sys.stderr)
        raise SystemExit(3)


def plugin_inventory(policy):
    """With autoload off, a plugin nobody lists silently stops loading. Every installed
    ``pytest11`` entry point must be loaded or declared not loaded."""
    from importlib.metadata import entry_points

    # xdist workers are new interpreters this guard never reaches.
    if "xdist" in policy["plugins"]:
        print("OFFLINE_GUARD: xdist workers run without the network guard", file=sys.stderr)
        raise SystemExit(3)
    installed = sorted({entry.name for entry in entry_points(group="pytest11")})
    known = set(policy["plugins"]) | set(policy["not_loaded"])
    unlisted = [name for name in installed if name not in known]
    absent = [name for name in policy["plugins"] if name not in installed]
    if unlisted or absent:
        print(f"OFFLINE_GUARD: pytest plugins unlisted={unlisted} listed-but-absent={absent}", file=sys.stderr)
        raise SystemExit(3)
    # Plugins load only through the policy list: a `-p NAME` in the arguments would load one
    # the adapter declared not loaded. `-p no:NAME` disables, so it stays allowed.
    args = policy["args"]
    for index, arg in enumerate(args):
        value = args[index + 1] if arg == "-p" and index + 1 < len(args) else arg[2:] if arg.startswith("-p") else None
        if value is not None and not value.startswith("no:"):
            print(f"OFFLINE_GUARD: load plugins through [offline] plugins, not {arg} in the arguments", file=sys.stderr)
            raise SystemExit(3)
    return installed


class PluginPolicy:
    """No installed plugin outside the policy list may register, however it arrives (an argument,
    an environment variable, a conftest or test module's ``pytest_plugins``, an import mid-run).
    The hook is historic, so it also sees every plugin registered before this one."""

    def __init__(self, policy):
        from importlib.metadata import entry_points

        self.forbidden = {
            entry.name: entry.module for entry in entry_points(group="pytest11") if entry.name not in policy["plugins"]
        }
        self.loaded = []

    # No `plugin_name` argument: pytest 7's hookspec lacks it, and pluggy refuses a hookimpl
    # that asks for one, so the name comes from the manager.
    def pytest_plugin_registered(self, plugin, manager):
        plugin_name = manager.get_name(plugin)
        module = getattr(plugin, "__name__", None)
        for name, entry_module in self.forbidden.items():
            if (plugin_name == name or module == entry_module) and name not in self.loaded:
                self.loaded.append(name)

    def report(self):
        print(f"OFFLINE_GUARD: plugins loaded outside [offline] plugins: {sorted(self.loaded)}", file=sys.stderr)

    def pytest_collection_finish(self, session):
        # Before any test runs: everything configuration and collection registered.
        if self.loaded:
            import pytest

            self.report()
            pytest.exit("plugins loaded outside the policy", returncode=3)


def main():
    policy = json.loads(os.environ.pop("NUI_LAMBDA_VALIDATION_POLICY"))
    if os.path.dirname(os.path.abspath(__file__)) in [os.path.abspath(p) for p in sys.path if p]:
        print("OFFLINE_GUARD: checker directory is on sys.path", file=sys.stderr)
        raise SystemExit(3)
    plugins = plugin_inventory(policy)
    guard()
    os.chdir(policy["root"])
    sys.path.insert(0, policy["root"])
    print(
        "NETWORK_GUARD_EFFECTIVE INTERPRETER",
        sys.executable,
        sys.version.split()[0],
        "SOURCE",
        policy["root"],
        "INSTALLED_PLUGINS",
        plugins,
        "LOADED",
        policy["plugins"],
        flush=True,
    )
    import pytest

    allowed = set(policy["allowed_skips"])

    class SkipPolicy:
        def __init__(self):
            self.unexpected = []
            self.allowed_seen = []
            self.ran = 0
            self.finished = set()
            self.complete = False

        def pytest_runtest_logfinish(self, nodeid, location):
            # A set of node ids, not a count: a retry plugin finishes one item more than once.
            self.finished.add(nodeid)

        def pytest_runtest_logreport(self, report):
            # An xfail reports as skipped with ``wasxfail`` and a non-strict xpass as passed
            # with it. Neither proves the test passes, so both count as skips: a JUnit check
            # counts an xfail as skipped, and marking a failing test xfail must not turn CI green.
            if report.skipped or (report.passed and hasattr(report, "wasxfail")):
                (self.allowed_seen if report.nodeid in allowed else self.unexpected).append(report.nodeid)
            if report.when == "call":
                self.ran += 1

        def pytest_collectreport(self, report):
            if report.skipped:
                nodeid = report.nodeid or "<collection>"
                (self.allowed_seen if nodeid in allowed else self.unexpected).append(nodeid)

        def pytest_sessionfinish(self, session, exitstatus):
            # pytest.exit(returncode=0) in a test or fixture ends the loop with a 0 and the rest
            # unrun: a pass needs every collected item to have finished.
            self.complete = {item.nodeid for item in getattr(session, "items", ())} <= self.finished
            if self.unexpected:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

        def pytest_terminal_summary(self, terminalreporter):
            terminalreporter.write_line(
                f"SKIP_POLICY allowed={sorted(self.allowed_seen)} unexpected={sorted(self.unexpected)}"
            )
            if self.unexpected:
                terminalreporter.write_line(
                    "UNEXPECTED_SKIP: run fails on skips, xfails and xpasses outside the adapter allow-list"
                )

    arguments = []
    for plugin in policy["plugins"]:
        arguments += ["-p", plugin]
    skips = SkipPolicy()
    plugins = PluginPolicy(policy)
    status = int(pytest.main(arguments + policy["args"], plugins=[skips, plugins]))
    if plugins.loaded and status != 3:
        # Registered while tests ran, after the collection check.
        plugins.report()
        status = 3
    # The launcher trusts a 0 only beside this record, so a run that never reached here
    # (`os._exit(0)` in a test) or ran no test (`--collect-only`) cannot pass.
    with open(policy["result"], "w") as record:
        json.dump({"status": status, "ran": skips.ran, "complete": skips.complete}, record)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
