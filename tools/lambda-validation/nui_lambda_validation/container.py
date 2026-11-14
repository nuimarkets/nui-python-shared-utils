"""Run a smoke script against an extracted artifact in a matching Lambda image, offline and read-only."""

import json
import re
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
        # Never pull here: the image is the local one ensure_image checked and recorded.
        "--pull",
        "never",
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
    """The local image ID to run ``image`` by, pulled for the target platform when the host
    does not have it, so a cold host builds the same as a warm one and consumers carry no pull
    step of their own. A cached image that does not provide the target platform counts as
    absent. The ID is read before the platform check, and the check reads that ID rather than
    the tag, so the runs (``argv`` with ``--pull never``) execute the image that was checked
    and whose digests are recorded, even if the tag moves meanwhile."""
    reference = _local_id(image)
    if reference is None or not _provides(reference, machine):
        result = subprocess.run(
            ["docker", "pull", "--platform", PLATFORM[machine], image], capture_output=True, text=True
        )
        if result.returncode != 0:
            fail("BUILD", f"cannot pull image {image}: {result.stderr.strip()[:300]}")
        reference = _local_id(image)
        if reference is None or not _provides(reference, machine):
            fail("BUILD", f"image {image} does not provide {PLATFORM[machine]} after a pull for it")
    return reference


# A confirmed absence, or an image that does not hold the requested platform. Anything else
# (a daemon or permission error, a docker without `image inspect --platform`) is not.
ABSENT = re.compile(r"No such (image|object)|specified platform")


def _inspect(*args):
    """The inspected record, or None on a confirmed absence."""
    result = subprocess.run(["docker", "image", "inspect", *args], capture_output=True, text=True)
    if result.returncode == 0:
        return json.loads(result.stdout)[0]
    if not ABSENT.search(result.stderr):
        fail("BUILD", f"cannot inspect image {args[-1]}: {result.stderr.strip()[:300]}")
    return None


def _local_id(image):
    found = _inspect(image)
    return None if found is None else found["Id"]


def _provides(reference, machine):
    """Whether the local image ``reference`` holds the target platform. Under the containerd
    image store one ID can hold several platforms, so the check asks for the platform rather
    than reading the image's default one."""
    found = _inspect("--platform", PLATFORM[machine], reference)
    return found is not None and f"{found.get('Os')}/{found.get('Architecture')}" == PLATFORM[machine]


def image_digests(image):
    """Repository digests of a local image, by tag or by ID. An image that cannot be inspected, or has no
    digest to record, fails rather than leaving the manifest without its provenance."""
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    if result.returncode != 0:
        fail("BUILD", f"cannot inspect image {image}: {result.stderr.strip()[:300]}")
    digests = json.loads(result.stdout)[0].get("RepoDigests", [])
    if not digests:
        fail("BUILD", f"image {image} has no repository digest to record")
    return digests
