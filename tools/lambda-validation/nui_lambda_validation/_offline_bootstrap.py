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
    # the adapter declared not loaded. `-p no:NAME` disables, so it stays allowed here; one that
    # disables a declared plugin is caught after configuration (PluginPolicy).
    args = policy["args"]
    for index, arg in enumerate(args):
        value = args[index + 1] if arg == "-p" and index + 1 < len(args) else arg[2:] if arg.startswith("-p") else None
        if value is not None and not value.startswith("no:"):
            print(f"OFFLINE_GUARD: load plugins through [offline] plugins, not {arg} in the arguments", file=sys.stderr)
            raise SystemExit(3)
    return installed


def target(entry):
    """The object an entry point names, if its module is already imported. Nothing is imported
    here: a module that was never imported holds no registered plugin."""
    found = sys.modules.get(entry.module)
    for part in (entry.attr or "").split(".") if entry.attr else ():
        found = getattr(found, part, None)
    return found if found is not None else object()


class PluginPolicy:
    """No installed plugin outside the policy list may register, however it arrives (an argument,
    an environment variable, a conftest or test module's ``pytest_plugins``, an import mid-run).
    The hook is historic, so it also sees every plugin registered before this one."""

    def __init__(self, policy):
        from importlib.metadata import entry_points

        self.points = {entry.name: entry for entry in entry_points(group="pytest11")}
        self.entries = {name: entry.module for name, entry in self.points.items()}
        self.declared = list(policy["plugins"])
        self.forbidden = {name: module for name, module in self.entries.items() if name not in self.declared}
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

    def active(self, manager):
        """Installed plugins pytest holds now: under the entry point's name, or as the very object
        the entry point names (a module that ``pytest_plugins`` or an import registered under its
        own name). A blocked name (`-p no:NAME` after the runner's own `-p NAME`) maps to None, so
        it is not active, and a registered module does not stand in for an object inside it."""
        held = [plugin for _, plugin in manager.list_name_plugin() if plugin is not None]
        return sorted(
            name
            for name, entry in self.points.items()
            if manager.get_plugin(name) is not None or any(plugin is target(entry) for plugin in held)
        )

    def pytest_collection_finish(self, session):
        # Before any test runs: everything arguments, ini addopts, configuration and collection
        # registered or blocked. LOADED is read here, from pytest, not from the policy.
        import pytest

        active = self.active(session.config.pluginmanager)
        print("LOADED", active, flush=True)
        inactive = [name for name in self.declared if name not in active]
        if inactive:
            print(f"OFFLINE_GUARD: plugins in [offline] plugins not loaded: {inactive}", file=sys.stderr)
            pytest.exit("declared plugins not loaded", returncode=3)
        if self.loaded:
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
        "DECLARED",
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
            self.phases = {}
            self.collected = False
            # Every item that has to finish, by id, held so no id is reused. It only grows:
            # the items collection left, each item whose protocol started, and session.items
            # at the end, so no hook that shrinks session.items shrinks what has to finish.
            self.required = {}
            self.complete = False

        def require(self, items):
            for item in items:
                self.required.setdefault(id(item), item)

        def pytest_collection_finish(self, session):
            self.collected = True
            self.require(session.items)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item, nextitem):
            # Per collected item object: a retry plugin reruns inside one protocol call, and
            # the same file passed twice gives two items with one node id. An item finished
            # when its protocol returned and pytest made this item's own reports inside that
            # call: a teardown, and a call unless setup did not pass (a setup skip or error is
            # the one way pytest skips the call). A protocol raising (pytest.exit) never
            # finished, and an override that returns True without running the item, runs
            # another item, or runs setup and teardown alone, reports no such pair for it.
            self.require([item])
            self.phases.pop(id(item), None)
            outcome = yield
            try:
                outcome.get_result()
            except BaseException:
                return
            phases = self.phases.get(id(item), {})
            if "teardown" in phases and ("call" in phases or phases.get("setup", "passed") != "passed"):
                self.finished.add(id(item))

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            try:
                report = outcome.get_result()
            except BaseException:
                return
            self.phases.setdefault(id(item), {})[call.when] = report.outcome

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
            self.require(getattr(session, "items", ()))
            self.complete = self.collected and all(key in self.finished for key in self.required)
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
