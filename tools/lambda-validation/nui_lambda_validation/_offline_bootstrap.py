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


def blocked(*args, **kwargs):
    raise RuntimeError("offline test: outbound networking blocked")


def guard():
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    socket.socket.sendmsg = blocked
    socket.create_connection = blocked
    for probe in (
        lambda: socket.create_connection(("192.0.2.1", 443)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("192.0.2.1", 443)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"probe", ("192.0.2.1", 9)),
    ):
        try:
            probe()
        except RuntimeError:
            continue
        print("OFFLINE_GUARD: network guard ineffective", file=sys.stderr)
        raise SystemExit(3)


def plugin_inventory(policy):
    """With autoload off, a plugin nobody lists silently stops loading. Every installed
    ``pytest11`` entry point must be loaded or declared not loaded."""
    from importlib.metadata import entry_points

    installed = sorted({entry.name for entry in entry_points(group="pytest11")})
    known = set(policy["plugins"]) | set(policy["not_loaded"])
    unlisted = [name for name in installed if name not in known]
    absent = [name for name in policy["plugins"] if name not in installed]
    if unlisted or absent:
        print(f"OFFLINE_GUARD: pytest plugins unlisted={unlisted} listed-but-absent={absent}", file=sys.stderr)
        raise SystemExit(3)
    return installed


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

        def pytest_runtest_logreport(self, report):
            # An xfail reports as skipped with ``wasxfail``; it is not a skip.
            if report.skipped and not hasattr(report, "wasxfail"):
                (self.allowed_seen if report.nodeid in allowed else self.unexpected).append(report.nodeid)

        def pytest_collectreport(self, report):
            if report.skipped:
                self.unexpected.append(report.nodeid or "<collection>")

        def pytest_sessionfinish(self, session, exitstatus):
            if self.unexpected:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

        def pytest_terminal_summary(self, terminalreporter):
            terminalreporter.write_line(
                f"SKIP_POLICY allowed={sorted(self.allowed_seen)} unexpected={sorted(self.unexpected)}"
            )
            if self.unexpected:
                terminalreporter.write_line("UNEXPECTED_SKIP: run fails on skips outside the adapter allow-list")

    arguments = []
    for plugin in policy["plugins"]:
        arguments += ["-p", plugin]
    return pytest.main(arguments + policy["args"], plugins=[SkipPolicy()])


if __name__ == "__main__":
    raise SystemExit(main())
