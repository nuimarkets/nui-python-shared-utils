"""Run a smoke script against an extracted artifact in a matching Lambda image, offline and read-only."""

import json
import subprocess
from pathlib import Path

from .errors import fail

TOOL_MOUNT = "/opt/nui-lambda-validation"
TASK_ROOT = "/var/task"
PLATFORM = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}
# Set by argv itself, after the adapter's env, so docker's last-wins `-e` keeps these.
FIXED_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "HOME": "/tmp",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_CONFIG_FILE": "/dev/null",
    "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
}
RESERVED_ENV = frozenset(FIXED_ENV) | {"PYTHONPATH", "VALIDATION_STAGE"}


def argv(image, extracted, machine, env=None, script=None, args=(), proof=None, pythonpath=TOOL_MOUNT):
    """``pythonpath`` is the smoke's import path, the tool mount by default. ``python -m pip``
    drops the working directory from ``sys.path``, so a pip run over the artifact passes
    ``TASK_ROOT`` here or it inspects only the image's own packages."""
    package_dir = Path(__file__).resolve().parent
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        PLATFORM[machine],
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop",
        "ALL",
        "--entrypoint",
        "/var/lang/bin/python",
        "-w",
        TASK_ROOT,
        "-v",
        f"{Path(extracted).resolve()}:{TASK_ROOT}:ro",
        "-v",
        f"{package_dir}:{TOOL_MOUNT}/nui_lambda_validation:ro",
    ]
    if proof is not None:
        command += ["-v", f"{Path(proof).resolve()}:/proof:ro"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for key, value in dict(FIXED_ENV, PYTHONPATH=pythonpath).items():
        command += ["-e", f"{key}={value}"]
    if script is not None:
        command += ["-v", f"{Path(script).resolve()}:/smoke.py:ro", image, "/smoke.py", *args]
    else:
        command += [image, *args]
    return command


def run(command, log):
    with open(log, "w") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT).returncode


def ensure_image(image, machine):
    """Pull ``image`` for the target platform when the host does not have it, so a cold host
    builds the same as a warm one and consumers carry no pull step of their own."""
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
        return image
    result = subprocess.run(["docker", "pull", "--platform", PLATFORM[machine], image], capture_output=True, text=True)
    if result.returncode != 0:
        fail("BUILD", f"cannot pull image {image}: {result.stderr.strip()[:300]}")
    return image


def image_digests(image):
    """Repository digests of a local image. An image that cannot be inspected, or has no
    digest to record, fails rather than leaving the manifest without its provenance."""
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    if result.returncode != 0:
        fail("BUILD", f"cannot inspect image {image}: {result.stderr.strip()[:300]}")
    digests = json.loads(result.stdout)[0].get("RepoDigests", [])
    if not digests:
        fail("BUILD", f"image {image} has no repository digest to record")
    return digests
