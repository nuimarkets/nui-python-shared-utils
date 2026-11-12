"""Run a smoke script against an extracted artifact in a matching Lambda image, offline and read-only."""

import json
import subprocess
from pathlib import Path

TOOL_MOUNT = "/opt/nui-lambda-validation"
PLATFORM = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}


def argv(image, extracted, machine, env=None, script=None, args=(), proof=None):
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
        "/var/task",
        "-v",
        f"{Path(extracted).resolve()}:/var/task:ro",
        "-v",
        f"{package_dir}:{TOOL_MOUNT}/nui_lambda_validation:ro",
        "-e",
        f"PYTHONPATH={TOOL_MOUNT}",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "HOME=/tmp",
        "-e",
        "AWS_EC2_METADATA_DISABLED=true",
        "-e",
        "AWS_CONFIG_FILE=/dev/null",
        "-e",
        "AWS_SHARED_CREDENTIALS_FILE=/dev/null",
    ]
    if proof is not None:
        command += ["-v", f"{Path(proof).resolve()}:/proof:ro"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    if script is not None:
        command += ["-v", f"{Path(script).resolve()}:/smoke.py:ro", image, "/smoke.py", *args]
    else:
        command += [image, *args]
    return command


def run(command, log):
    with open(log, "w") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT).returncode


def image_digests(image):
    data = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))
    return data[0].get("RepoDigests", [])
