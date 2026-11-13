"""Run an application's pytest suite offline: cleared environment, isolated HOME and AWS config,
explicit plugins, an in-process TCP/UDP guard and an explicit skip policy.

The guard covers the test interpreter only. It is not an OS-level sandbox: a test that
spawns a subprocess gets the cleared environment but not the socket guard.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .errors import fail

BASE_ENV = {
    "LANG": "C.UTF-8",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_XRAY_SDK_ENABLED": "false",
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    "BOTO_CONFIG": os.devnull,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}
BOOTSTRAP = Path(__file__).with_name("_offline_bootstrap.py")
# Names the runner sets itself. An adapter that passed one through or overrode it would
# hand the suite back a real HOME, real AWS config or plugin autoload.
RESERVED_ENV = frozenset(BASE_ENV) | {"HOME", "NUI_LAMBDA_VALIDATION_POLICY", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}


def environment(home, passthrough=(), fixed=None, source=None):
    reserved = sorted((set(passthrough) | set(fixed or {})) & RESERVED_ENV)
    if reserved:
        fail("ADAPTER_CONFIG", f"the offline runner sets {reserved} itself")
    source = os.environ if source is None else source
    env = {"PATH": source.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
    env.update(BASE_ENV)
    for name in passthrough:
        if name in source:
            env[name] = source[name]
    env.update(fixed or {})
    return env


def run(python, root, args, plugins=(), allowed_skips=(), passthrough=(), fixed_env=None, not_loaded=None):
    """Return the pytest exit status from ``python -P <bootstrap>`` under the offline policy.

    ``plugins`` are ``pytest11`` entry-point names loaded with ``-p``; every other installed
    entry point must appear in ``not_loaded`` with a reason, or the run stops (exit 3)."""
    policy = {
        "root": str(Path(root).resolve()),
        "args": list(args),
        "plugins": list(plugins),
        "allowed_skips": list(allowed_skips),
        "not_loaded": dict(not_loaded or {}),
    }
    # absolute(), not resolve(): a venv's python is a symlink, and resolving it leaves the venv.
    python = Path(python).absolute()
    with (
        tempfile.TemporaryDirectory(prefix="lambda-validation-home-") as home,
        tempfile.TemporaryDirectory(prefix="lambda-validation-result-") as box,
    ):
        result = Path(box) / "result.json"
        policy["result"] = str(result)
        env = environment(home, passthrough, fixed_env)
        env["NUI_LAMBDA_VALIDATION_POLICY"] = json.dumps(policy)
        try:
            status = subprocess.run([str(python), "-P", str(BOOTSTRAP)], cwd=policy["root"], env=env).returncode
        except OSError as error:
            fail("OFFLINE_GUARD", f"cannot run the application interpreter {python}: {error}")
        if status == 0:
            try:
                record = json.loads(result.read_text())
            except (OSError, ValueError):
                fail("OFFLINE_GUARD", "the suite exited 0 without finishing (no result from the runner)")
            if record.get("status") != 0 or not record.get("ran"):
                fail("OFFLINE_GUARD", f"the suite exited 0 but the runner recorded {record}")
        return status
