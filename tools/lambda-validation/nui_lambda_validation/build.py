"""Build deployment packages from committed source with synthetic configuration, never the working tree."""

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

from .errors import fail


def clean_env(home, extra=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "CI": "1",
        "NO_COLOR": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "SLS_TELEMETRY_DISABLED": "1",
        "SLS_INTERACTIVE_SETUP_ENABLE": "0",
    }
    env.update(extra or {})
    return env


def committed_source(root, destination, rev="HEAD"):
    """Extract ``git archive <rev>`` into a new ``destination``; return the head and archive hash."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(root) or destination.exists():
        fail("BUILD", f"destination must be new and outside the checkout: {destination}")
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", rev], text=True).strip()
    archive = subprocess.check_output(["git", "-C", str(root), "archive", head])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(destination, filter="data")
    return {"head": head, "source_archive_sha256": hashlib.sha256(archive).hexdigest()}


def prepare(tree, root, substitutions=(), files=None):
    """Link the checkout's ``node_modules``, apply exact-count substitutions, write synthetic files."""
    tree, root = Path(tree), Path(root)
    (tree / "node_modules").symlink_to(root / "node_modules", target_is_directory=True)
    for item in substitutions:
        path = tree / item["file"]
        text = path.read_text()
        found = text.count(item["old"])
        if found != item["count"]:
            fail("BUILD", f"{item['file']}: {item['old']!r} occurs {found} times, adapter expects {item['count']}")
        path.write_text(text.replace(item["old"], item["new"]))
    for relative, text in (files or {}).items():
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def package(tree, stage, serverless, env, log):
    """``serverless package`` one stage into a fresh directory; return (zip, template)."""
    target = Path(tree) / f".serverless-validation-{stage}"
    with open(log, "w") as output:
        result = subprocess.run(
            [str(serverless), "package", "--stage", stage, "--package", str(target)],
            cwd=tree,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    if result.returncode != 0:
        fail("BUILD", f"serverless package --stage {stage} exited {result.returncode}; see {log}")
    zips = sorted(target.glob("*.zip"))
    if len(zips) != 1:
        fail("BUILD", f"expected one ZIP in {target}, found {[z.name for z in zips]}")
    return zips[0], target / "cloudformation-template-update-stack.json"
