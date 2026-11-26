"""Exercise the installed executable outside the source tree with networking blocked."""

import os
from pathlib import Path
import re
import subprocess
import sys
import venv

import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def installed_cli(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("installed-cli")
    repo = Path(__file__).resolve().parents[1]
    wheel_dir = workspace / "wheels"
    wheel_dir.mkdir()
    setup_env = os.environ.copy()
    setup_env.pop("PYTHONPATH", None)
    setup_env.pop("PYTHONHOME", None)

    def setup_command(args):
        result = subprocess.run(args, cwd=workspace, env=setup_env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    setup_command([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(repo)])
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    environment = workspace / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    executable = scripts / ("slack-channel-setup.exe" if os.name == "nt" else "slack-channel-setup")
    setup_command([str(python), "-m", "pip", "install", f"{wheels[0]}[slack]"])

    guard = workspace / "guard"
    guard.mkdir()
    loaded = guard / "loaded.txt"
    attempts = guard / "attempts.txt"
    (guard / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "import socket\n"
        "root = Path(__file__).parent\n"
        "(root / 'loaded.txt').write_text('loaded')\n"
        "def blocked(*args, **kwargs):\n"
        "    with (root / 'attempts.txt').open('a') as stream:\n"
        "        stream.write('API or network access attempted\\n')\n"
        "    raise AssertionError('API and network access are forbidden in CLI smoke tests')\n"
        "socket.create_connection = blocked\n"
        "socket.getaddrinfo = blocked\n"
        "socket.socket.connect = blocked\n"
        "socket.socket.connect_ex = blocked\n"
        "from slack_sdk import WebClient\n"
        "WebClient.api_call = blocked\n"
    )
    smoke_env = setup_env.copy()
    smoke_env.pop("SLACK_BOT_TOKEN", None)
    smoke_env["PYTHONPATH"] = str(guard)
    smoke_env["PYTHONNOUSERSITE"] = "1"

    loaded.write_text("")
    attempts.write_text("")
    control = subprocess.run(
        [str(python), "-c", "from slack_sdk import WebClient; WebClient(token='synthetic').api_call('auth.test')"],
        cwd=workspace,
        env=smoke_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert control.returncode != 0
    assert loaded.read_text() == "loaded"
    assert "API or network access attempted" in attempts.read_text()
    assert "API and network access are forbidden" in control.stderr

    def run(args):
        loaded.write_text("")
        attempts.write_text("")
        result = subprocess.run(
            args,
            cwd=workspace,
            env=smoke_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert loaded.read_text() == "loaded", result.stderr
        assert attempts.read_text() == "", result.stdout + result.stderr
        return result

    location = run([str(python), "-c", "import nui_shared_utils; print(nui_shared_utils.__file__)"])
    assert location.returncode == 0, location.stderr
    assert Path(location.stdout.strip()).is_relative_to(environment)
    metadata = run(
        [str(python), "-c", "from importlib.metadata import version; print(version('nui-python-shared-utils'))"]
    )
    assert metadata.returncode == 0, metadata.stderr
    return workspace, executable, run, metadata.stdout.strip()


def test_installed_entrypoint_help_and_version(installed_cli):
    _, executable, run, version = installed_cli
    help_result = run([str(executable), "--help"])
    assert help_result.returncode == 0, help_result.stderr
    assert re.search(r"^\s+--config PATH\s", help_result.stdout, re.MULTILINE), help_result.stdout
    version_result = run([str(executable), "--version"])
    assert version_result.returncode == 0, version_result.stderr
    assert f"version {version}" in version_result.stdout


def test_installed_validate_only_accepts_valid_yaml_without_token_or_api(installed_cli):
    workspace, executable, run, _ = installed_cli
    config = workspace / "valid.yaml"
    config.write_text(
        "channels:\n  - name: sample-alerts\n    description: Synthetic channel\n"
        "    purpose: Synthetic alerts\n    topic: Test setup\n"
    )
    result = run([str(executable), "--config", str(config), "--validate-only"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All channel names are valid" in result.stdout


def test_installed_validate_only_rejects_invalid_yaml_without_api(installed_cli):
    workspace, executable, run, _ = installed_cli
    config = workspace / "invalid.yaml"
    config.write_text("service: synthetic\n")
    result = run([str(executable), "--config", str(config), "--validate-only"])
    assert result.returncode != 0, result.stdout + result.stderr
    assert "missing 'channels' section" in result.stderr
