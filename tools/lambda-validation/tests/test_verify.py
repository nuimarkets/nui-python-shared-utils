"""The ``verify`` orchestration with packaging and Docker stubbed: which inputs it reads, and when it refuses."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import ADAPTER, PINS, ROOTS, base_members, write_zip

from nui_lambda_validation import build, cli, container
from nui_lambda_validation.config import Adapter
from nui_lambda_validation.errors import ValidationError

BUILD = """
[build]
stages = ["dev", "prod"]
config_path = "config/env-{stage}.yml"
config_text = "SYNTHETIC: value\\n"
build_images = ["example.invalid/build-python3.12:x86_64"]
[[build.substitutions]]
file = "serverless.yml"
old = "ACCOUNT"
new = "000000000000"
count = 2

[container]
image = "example.invalid/lambda-python:3.12"
smoke = "smoke.py"
"""

# A git that reads no user or system configuration, so the test cannot depend on or touch it.
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=GIT_ENV,
    )


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    files = {
        "lambda-validation.toml": ADAPTER + BUILD,
        "requirements.txt": PINS,
        "requirements.in": ROOTS,
        "serverless.yml": "account: ACCOUNT\nrole: arn:ACCOUNT\n",
        "smoke.py": "print('committed smoke')\n",
        ".gitignore": "node_modules/\n",
        "requirements-validation.txt": LINE.format(SHA),
    }
    for name, text in files.items():
        (root / name).write_text(text)
    (root / "node_modules" / ".bin").mkdir(parents=True)
    (root / "node_modules" / ".bin" / "serverless").write_text("")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    calls = {"package": [], "digests": [], "ensure": [], "run": [], "fail": {}, "log": {}}
    good = write_zip(tmp_path / "artifact.zip", base_members())

    def package(tree, stage, serverless, env, log):
        calls["package"].append(
            {
                "tree": tree,
                "stage": stage,
                "serverless": serverless,
                "serverless_yml": (tree / "serverless.yml").read_text(),
                "config": (tree / f"config/env-{stage}.yml").read_text(),
            }
        )
        target = tree / f".serverless-validation-{stage}"
        target.mkdir()
        (target / "app.zip").write_bytes(good.read_bytes())
        (target / "cloudformation-template-update-stack.json").write_text(json.dumps({"stage": stage}))
        return target / "app.zip", target / "cloudformation-template-update-stack.json"

    def ensure(image, machine):
        calls["ensure"].append((image, machine, len(calls["digests"])))
        return image_id(image)

    def digests(reference):
        # RepoDigests name the repository, whichever reference (tag or ID) is inspected.
        calls["digests"].append(reference)
        names = {image_id(name): name for name, _, _ in calls["ensure"]}
        return [f"{names.get(reference, reference)}@sha256:{'0' * 64}"]

    def run(command, log):
        index = len(calls["run"])
        calls["run"].append(command)
        if index in calls["log"]:
            Path(log).write_text(calls["log"][index])
        return calls["fail"].get(index, 0)

    monkeypatch.setattr(build, "package", package)
    monkeypatch.setattr(container, "ensure_image", ensure)
    monkeypatch.setattr(container, "image_digests", digests)
    monkeypatch.setattr(container, "platform_id", lambda reference, machine: f"{reference}/{machine}")
    installed = json.dumps({"vcs_info": {"commit_id": SHA}})
    monkeypatch.setattr(cli.importlib_metadata, "distribution", lambda name: FakeDistribution(installed))
    monkeypatch.setattr(container, "run", run)
    return calls


def image_id(name):
    """A stand-in local image ID, distinct per image name."""
    return "sha256:" + format(sum(map(ord, name)), "064x")


def code_of(call, *args):
    with pytest.raises(ValidationError) as caught:
        call(*args)
    return caught.value.code


def test_verify_runs_every_stage_and_records_both_images(tmp_path, project, stubs):
    manifest = cli.verify(Adapter(project / "lambda-validation.toml"), tmp_path / "out")
    assert [a["stage"] for a in manifest["artifacts"]] == ["dev", "prod"]
    assert manifest["build_images"] == {
        "example.invalid/build-python3.12:x86_64": [f"example.invalid/build-python3.12:x86_64@sha256:{'0' * 64}"]
    }
    assert manifest["image_digests"] and "negatives" in manifest["artifacts"][0]
    # Provenance names the checked image itself: its ID, its platform, and digests read by that ID.
    runtime, builder = "example.invalid/lambda-python:3.12", "example.invalid/build-python3.12:x86_64"
    assert manifest["image_id"] == image_id(runtime) and manifest["image_platform"] == "linux/amd64"
    assert manifest["image_platform_id"] == f"{image_id(runtime)}/x86_64"
    assert manifest["build_image_ids"] == {builder: image_id(builder)}
    assert stubs["digests"] == [image_id(runtime), image_id(builder)]
    # Every container run executes that ID, never the mutable tag, and never pulls.
    for command in stubs["run"]:
        assert image_id(runtime) in command and runtime not in command
        assert command[command.index("--pull") + 1] == "never"
    # Both images are pulled when missing, for the target platform, before any digest is read.
    assert stubs["ensure"] == [
        ("example.invalid/lambda-python:3.12", "x86_64", 0),
        ("example.invalid/build-python3.12:x86_64", "x86_64", 0),
    ]
    assert manifest["tool"] == {"commit": SHA}
    assert all(call["serverless_yml"].count("000000000000") == 2 for call in stubs["package"])
    assert all(call["config"] == "SYNTHETIC: value\n" for call in stubs["package"])
    assert stubs["package"][0]["serverless"] == project / "node_modules" / ".bin" / "serverless"
    # interpreter version, smoke and pip check per stage
    assert len(stubs["run"]) == 6
    version, smoke, pip_check = stubs["run"][:3]
    assert version[-2:] == ["-c", "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"]
    assert "/smoke.py" in smoke and "PYTHONPATH=/opt/nui-lambda-validation" in smoke
    # python -m pip drops the working directory from sys.path, so the artifact is put on PYTHONPATH.
    assert pip_check[-2:] == ["pip", "check"] and "PYTHONPATH=/var/task" in pip_check
    assert pip_check.index("PYTHONPATH=/var/task") > max(
        i for i, a in enumerate(pip_check) if a == "VALIDATION_STAGE=dev"
    )


@pytest.mark.parametrize("index, message", [(0, "is not Python 3.12"), (1, "SDK_PROVENANCE"), (2, "pip check failed")])
def test_verify_fails_on_each_container_step(tmp_path, project, stubs, index, message):
    stubs["fail"][index] = 1
    stubs["log"][
        1
    ] = "Traceback ...\nnui_lambda_validation.runtime.RuntimeCheckError: SDK_PROVENANCE: boto3 from /var/lang\n"
    with pytest.raises(ValidationError, match=message) as caught:
        cli.verify(Adapter(project / "lambda-validation.toml"), tmp_path / "out")
    assert caught.value.code == "BUILD"


def test_verify_reads_inputs_from_the_committed_tree(tmp_path, project, stubs):
    # Uncommitted edits that would weaken or break the run must not reach it.
    (project / "requirements.txt").write_text(PINS.replace("helper==2.1.0", "helper==9.9.9"))
    (project / "smoke.py").write_text("print('uncommitted smoke')\n")
    (project / "lambda-validation.toml").write_text((ADAPTER + BUILD).replace("extensions = 1", "extensions = 7"))
    out = tmp_path / "out"
    cli.verify(Adapter(project / "lambda-validation.toml"), out)
    assert json.loads((out / "proof" / "pins.json").read_text())["helper"] == "2.1.0"
    smoke_mounts = [arg for command in stubs["run"] for arg in command if arg.endswith(":/smoke.py:ro")]
    assert smoke_mounts and all(arg.startswith(str((out / "source").resolve())) for arg in smoke_mounts)


def test_verify_refuses_an_uncommitted_adapter(tmp_path, project, stubs):
    git(project, "rm", "-q", "--cached", "lambda-validation.toml")
    git(project, "commit", "-q", "-m", "drop adapter")
    assert code_of(cli.verify, Adapter(project / "lambda-validation.toml"), tmp_path / "out") == "BUILD"
    assert stubs["package"] == []


@pytest.mark.parametrize(
    "edit, stages",
    [
        (('stages = ["dev", "prod"]', "stages = []"), None),
        (("", ""), ["qa"]),
        (('config_text = "SYNTHETIC: value\\n"\n', ""), None),
        (('smoke = "smoke.py"\n', ""), None),
    ],
)
def test_verify_refuses_an_incomplete_build_before_building(tmp_path, project, stubs, edit, stages):
    path = project / "lambda-validation.toml"
    path.write_text(path.read_text().replace(*edit))
    git(project, "commit", "-q", "--allow-empty", "-am", "edit")
    assert code_of(cli.verify, Adapter(path), tmp_path / "out", stages) == "ADAPTER_CONFIG"
    assert stubs["package"] == []


def test_adapter_must_sit_at_the_repository_top(tmp_path, project, stubs):
    nested = project / "sub"
    nested.mkdir()
    (nested / "lambda-validation.toml").write_text(ADAPTER + BUILD)
    git(project, "add", "sub")
    git(project, "commit", "-q", "-m", "nested adapter")
    with pytest.raises(ValidationError) as caught:
        cli.verify(Adapter(nested / "lambda-validation.toml"), tmp_path / "out")
    assert caught.value.code == "BUILD" and "top of a git repository" in caught.value.detail


def test_substitution_count_is_exact(tmp_path):
    item = {"file": "f.txt", "old": "@", "new": "-"}
    for count, outcome in ((1, "BUILD"), (3, "BUILD"), (2, "x-x-x")):
        tree = tmp_path / f"tree-{count}"
        tree.mkdir()
        (tree / "f.txt").write_text("x@x@x")
        if outcome == "BUILD":
            assert code_of(build.prepare, tree, tmp_path, [dict(item, count=count)], {}) == "BUILD"
        else:
            build.prepare(tree, tmp_path, [dict(item, count=count)], {})
            assert (tree / "f.txt").read_text() == outcome


def test_image_without_digest_fails(monkeypatch):
    def inspect(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{"RepoDigests": []}]), stderr="")

    monkeypatch.setattr(container.subprocess, "run", inspect)
    assert code_of(container.image_digests, "local-only:latest") == "BUILD"

    def missing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="No such image")

    monkeypatch.setattr(container.subprocess, "run", missing)
    assert code_of(container.image_digests, "absent:latest") == "BUILD"


@pytest.mark.parametrize(
    "old, new",
    [
        ('prefixes = ["tests/", "."]', 'prefixs = ["tests/", "."]'),
        ("extensions = 1", 'extensions = "1"'),
        ("extensions = 1", "extensions = true"),
        ('required = ["handler.py", "boto3/__init__.py"]', 'required = "handler.py"'),
        ('mode = "exact"', 'mode = "exact"\npin = "requirements.txt"'),
        ('machine = "x86_64"', 'machine = "arm64"'),
        ("count = 2\n", ""),
        ('smoke = "smoke.py"', 'smoke = "smoke.py"\nenv = {A = 1}'),
    ],
)
def test_adapter_rejects_unknown_keys_and_wrong_types(project, old, new):
    path = project / "lambda-validation.toml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"


def test_readme_example_is_a_valid_adapter(tmp_path):
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    block = readme.split("```toml\n", 1)[1].split("```", 1)[0]
    (tmp_path / "lambda-validation.toml").write_text(block)
    Adapter(tmp_path / "lambda-validation.toml")


@pytest.mark.parametrize(
    "extra",
    [
        '[offline]\npassthrough_env = ["HOME"]\n',
        '[offline]\npassthrough_env = ["AWS_SHARED_CREDENTIALS_FILE"]\n',
        '[offline.env]\nPYTEST_DISABLE_PLUGIN_AUTOLOAD = ""\n',
        '[container.env]\nPYTHONPATH = "/var/task"\n',
        '[container.env]\nAWS_CONFIG_FILE = "/root/.aws/config"\n',
    ],
)
def test_adapter_cannot_undo_runner_or_container_isolation(tmp_path, extra):
    path = tmp_path / "lambda-validation.toml"
    text = ADAPTER + extra
    if extra.startswith("[container.env]"):
        text = ADAPTER + '[container]\nimage = "i"\nsmoke = "s.py"\n' + extra
    path.write_text(text)
    assert code_of(Adapter, path) == "ADAPTER_CONFIG"


@pytest.mark.parametrize("error", [FileNotFoundError("serverless"), subprocess.CalledProcessError(128, "git")])
def test_main_codes_os_and_subprocess_failures(tmp_path, monkeypatch, capsys, error):
    def boom(*args):
        raise error

    monkeypatch.setattr(cli, "verify", boom)
    path = tmp_path / "lambda-validation.toml"
    path.write_text(ADAPTER)
    assert cli.main(["verify", "--config", str(path), "--output", str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err.startswith(f"BUILD: {type(error).__name__}")


def test_prepare_stays_inside_the_build_tree(tmp_path):
    root = tmp_path / "checkout"
    (root / "node_modules").mkdir(parents=True)
    (root / "node_modules" / "x.js").write_text("ACCOUNT")
    tree = tmp_path / "tree"
    tree.mkdir()
    item = {"file": "node_modules/x.js", "old": "ACCOUNT", "new": "0", "count": 1}
    assert code_of(build.prepare, tree, root, [item], {}) == "ADAPTER_CONFIG"
    assert (root / "node_modules" / "x.js").read_text() == "ACCOUNT"
    tree2 = tmp_path / "tree2"
    tree2.mkdir()
    assert code_of(build.prepare, tree2, root, [], {"../escaped.yml": "x"}) == "ADAPTER_CONFIG"
    assert not (tmp_path / "escaped.yml").exists()
    tree3 = tmp_path / "tree3"
    tree3.mkdir()
    missing = {"file": "missing.yml", "old": "a", "new": "b", "count": 1}
    assert code_of(build.prepare, tree3, root, [missing], {}) == "BUILD"


SHA = "0123456789abcdef0123456789abcdef01234567"
LINE = "nui-lambda-validation @ git+https://github.com/example/repo@{}#subdirectory=tools/lambda-validation\n"


class FakeDistribution:
    def __init__(self, direct_url, location=None):
        self.direct_url = direct_url
        self.location = location or cli.__file__

    def read_text(self, name):
        assert name == "direct_url.json"
        return self.direct_url

    def locate_file(self, path):
        assert path == "nui_lambda_validation/cli.py"
        return self.location


@pytest.mark.parametrize(
    "requirement, direct_url, location, outcome",
    [
        (LINE.format(SHA), json.dumps({"vcs_info": {"commit_id": SHA}}), None, "ok"),
        ("# pinned for CI\n" + LINE.format(SHA), json.dumps({"vcs_info": {"commit_id": SHA}}), None, "ok"),
        (LINE.format(SHA), json.dumps({"vcs_info": {"commit_id": "f" * 40}}), None, "SELF_CHECK"),
        (LINE.format(SHA), None, None, "SELF_CHECK"),
        (LINE.format(SHA), "not json", None, "SELF_CHECK"),
        (LINE.format(SHA[:12]), json.dumps({"vcs_info": {"commit_id": SHA}}), None, "SELF_CHECK"),
        (LINE.format(SHA) * 2, json.dumps({"vcs_info": {"commit_id": SHA}}), None, "SELF_CHECK"),
        (LINE.format(SHA), json.dumps({"vcs_info": {"commit_id": SHA}}), "/elsewhere/cli.py", "SELF_CHECK"),
    ],
)
def test_self_check(tmp_path, monkeypatch, requirement, direct_url, location, outcome):
    path = tmp_path / "requirements-validation.txt"
    path.write_text(requirement)
    monkeypatch.setattr(cli.importlib_metadata, "distribution", lambda name: FakeDistribution(direct_url, location))
    if outcome == "ok":
        assert cli.self_check(path) == {"commit": SHA}
    else:
        assert code_of(cli.self_check, path) == outcome


def test_self_check_needs_an_installed_distribution(tmp_path, monkeypatch):
    path = tmp_path / "requirements-validation.txt"
    path.write_text(LINE.format(SHA))

    def missing(name):
        raise cli.importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(cli.importlib_metadata, "distribution", missing)
    assert code_of(cli.self_check, path) == "SELF_CHECK"
    assert code_of(cli.self_check, tmp_path / "absent.txt") == "SELF_CHECK"


def test_verify_binds_the_tool_to_the_committed_pin(tmp_path, project, stubs):
    # The installed tool is SHA. The commit pins another; a working-copy edit back to SHA must not count.
    (project / "requirements-validation.txt").write_text(LINE.format("f" * 40))
    git(project, "commit", "-q", "-am", "pin another tool")
    (project / "requirements-validation.txt").write_text(LINE.format(SHA))
    with pytest.raises(ValidationError) as caught:
        cli.verify(Adapter(project / "lambda-validation.toml"), tmp_path / "out")
    assert caught.value.code == "SELF_CHECK" and "f" * 40 in caught.value.detail
    assert stubs["package"] == [] and stubs["ensure"] == []
    # No committed pin at all also fails, before anything is built.
    git(project, "rm", "-q", "-f", "requirements-validation.txt")
    git(project, "commit", "-q", "-m", "drop the pin")
    (project / "requirements-validation.txt").write_text(LINE.format(SHA))
    assert code_of(cli.verify, Adapter(project / "lambda-validation.toml"), tmp_path / "out2") == "SELF_CHECK"
    assert stubs["package"] == []


class FakeDocker:
    """``docker image inspect`` and ``docker pull`` over an image store held in memory, shaped
    like the containerd store: a tag names one ID, and one ID may hold several platforms.
    ``registry`` is what a pull of each tag would fetch for a requested platform."""

    def __init__(
        self, store=None, registry=None, inspect_error=None, platform_error=None, pull_error=None, unpulled=()
    ):
        self.tags = dict(store or {})  # tag -> (id, platforms)
        self.unpulled = set(unpulled)  # (id, platform) listed in the index, content never pulled
        self.platform_ids = {}  # (id, platform) -> the platform image's own ID, when it differs
        self.registry = registry or {}  # tag -> {platform: (id, platforms)}
        self.inspect_error, self.platform_error, self.pull_error = inspect_error, platform_error, pull_error
        self.commands = []

    def _by_reference(self, reference):
        for image_id, platforms in self.tags.values():
            if reference == image_id:
                return image_id, platforms
        return self.tags.get(reference)

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            platform = command[4] if command[3] == "--platform" else None
            error = self.platform_error if platform else self.inspect_error
            if error:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=error)
            found = self._by_reference(command[-1])
            if found is None:
                stderr = f"Error response from daemon: No such image: {command[-1]}"
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)
            image_id, platforms = found
            if platform is None:
                platform = platforms[0]
            elif platform not in platforms:
                stderr = (
                    f"Error response from daemon: image with reference {command[-1]} was found"
                    f" but does not provide the specified platform ({platform})"
                )
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)
            system, architecture = platform.split("/")
            if (image_id, platform) in self.unpulled:
                # A platform listed in the local index whose content was never pulled: the
                # containerd store answers rc 0 with an empty platform, not an error.
                system = architecture = ""
            record_id = (
                self.platform_ids.get((image_id, platform), image_id) if command[3] == "--platform" else image_id
            )
            record = [{"Id": record_id, "Os": system, "Architecture": architecture, "RepoDigests": []}]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(record), stderr="")
        if command[1] == "pull":
            assert command[2] == "--platform"
            fetched = self.registry.get(command[-1], {}).get(command[3])
            if self.pull_error or fetched is None:
                stderr = self.pull_error or "manifest unknown"
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)
            self.tags[command[-1]] = fetched
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker call {command}")

    def pulls(self):
        return [command for command in self.commands if command[1] == "pull"]


AMD = "sha256:" + "a" * 64
ARM = "sha256:" + "b" * 64
BOTH = "sha256:" + "c" * 64


@pytest.mark.parametrize("machine, platform", [("x86_64", "linux/amd64"), ("aarch64", "linux/arm64")])
def test_ensure_image_runs_a_cached_image_that_provides_the_target_by_its_id(monkeypatch, machine, platform):
    # One ID holding both platforms, as the containerd store keeps a multi-platform pull.
    docker = FakeDocker({"img:1": (BOTH, ["linux/amd64", "linux/arm64"])})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert container.ensure_image("img:1", machine) == BOTH
    # The platform is checked on the ID read first, not on the tag, so a tag moved meanwhile
    # cannot put another image between the check and the run.
    assert docker.commands == [
        ["docker", "image", "inspect", "img:1"],
        ["docker", "image", "inspect", "--platform", platform, BOTH],
    ]


@pytest.mark.parametrize(
    "machine, platform, cached, wanted",
    [("x86_64", "linux/amd64", (ARM, ["linux/arm64"]), AMD), ("aarch64", "linux/arm64", (AMD, ["linux/amd64"]), ARM)],
)
def test_a_cached_image_of_another_platform_is_pulled_for_the_target(monkeypatch, machine, platform, cached, wanted):
    registry = {"img:1": {platform: (wanted, [platform])}}
    docker = FakeDocker({"img:1": cached}, registry)
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert container.ensure_image("img:1", machine) == wanted
    assert docker.pulls() == [["docker", "pull", "--platform", platform, "img:1"]]
    # The ID it returns, and the digests read through it, are the pulled image's.
    assert docker.commands[-1] == ["docker", "image", "inspect", "--platform", platform, wanted]


def test_platform_id_names_the_platform_image_inside_an_index(monkeypatch):
    docker = FakeDocker({"img:1": (BOTH, ["linux/amd64", "linux/arm64"])})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert container.platform_id(BOTH, "x86_64") == BOTH
    # The record the daemon returns for the platform is what is named, not the reference.
    docker.platform_ids = {(BOTH, "linux/arm64"): ARM}
    assert container.platform_id(BOTH, "aarch64") == ARM
    docker = FakeDocker({"img:1": (AMD, ["linux/amd64"])})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert code_of(container.platform_id, AMD, "aarch64") == "BUILD"
    docker = FakeDocker({"img:1": (BOTH, ["linux/amd64"])}, unpulled={(BOTH, "linux/amd64")})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert code_of(container.platform_id, BOTH, "x86_64") == "BUILD"


def test_a_listed_but_unpulled_platform_is_pulled(monkeypatch):
    # The index names linux/amd64 but holds no content for it; the pull fetches it.
    registry = {"img:1": {"linux/amd64": (AMD, ["linux/amd64"])}}
    docker = FakeDocker({"img:1": (BOTH, ["linux/arm64", "linux/amd64"])}, registry, unpulled={(BOTH, "linux/amd64")})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert container.ensure_image("img:1", "x86_64") == AMD
    assert docker.pulls() == [["docker", "pull", "--platform", "linux/amd64", "img:1"]]


def test_a_cached_image_that_misreports_its_platform_is_not_accepted(monkeypatch):
    # A daemon that answers a platform inspect with a record of another platform.
    docker = FakeDocker({"img:1": (ARM, ["linux/arm64"])})

    def ignores_platform(command, **kwargs):
        if command[3:4] == ["--platform"]:
            command = command[:3] + command[5:]
        return docker(command, **kwargs)

    monkeypatch.setattr(container.subprocess, "run", ignores_platform)
    with pytest.raises(ValidationError) as caught:
        container.ensure_image("img:1", "x86_64")
    assert caught.value.code == "BUILD" and "manifest unknown" in caught.value.detail


@pytest.mark.parametrize("cached", [None, (ARM, ["linux/arm64"])])
def test_a_pull_that_still_lacks_the_target_platform_fails(monkeypatch, cached):
    # The pull succeeds but the tag still names an image without linux/amd64.
    docker = FakeDocker({"img:1": cached} if cached else {}, {"img:1": {"linux/amd64": (ARM, ["linux/arm64"])}})
    monkeypatch.setattr(container.subprocess, "run", docker)
    with pytest.raises(ValidationError) as caught:
        container.ensure_image("img:1", "x86_64")
    assert caught.value.code == "BUILD" and "does not provide linux/amd64 after a pull" in caught.value.detail
    assert len(docker.pulls()) == 1


def test_ensure_image_pulls_a_missing_image_and_reports_a_failed_pull(monkeypatch):
    docker = FakeDocker({}, {"img:1": {"linux/amd64": (AMD, ["linux/amd64"])}})
    monkeypatch.setattr(container.subprocess, "run", docker)
    assert container.ensure_image("img:1", "x86_64") == AMD
    assert docker.pulls() == [["docker", "pull", "--platform", "linux/amd64", "img:1"]]
    docker = FakeDocker({}, {"img:1": {"linux/amd64": (AMD, ["linux/amd64"])}}, pull_error="denied: pull access")
    monkeypatch.setattr(container.subprocess, "run", docker)
    with pytest.raises(ValidationError) as caught:
        container.ensure_image("img:1", "x86_64")
    assert caught.value.code == "BUILD" and "denied: pull access" in caught.value.detail


@pytest.mark.parametrize(
    "errors",
    [
        {"inspect_error": "permission denied while trying to connect"},
        # A docker whose image inspect has no --platform is an error, not an absent image.
        {"platform_error": "unknown flag: --platform"},
    ],
)
def test_ensure_image_does_not_pull_over_an_inspect_error(monkeypatch, errors):
    docker = FakeDocker({"img:1": (AMD, ["linux/amd64"])}, **errors)
    monkeypatch.setattr(container.subprocess, "run", docker)
    with pytest.raises(ValidationError) as caught:
        container.ensure_image("img:1", "x86_64")
    assert caught.value.code == "BUILD" and next(iter(errors.values())) in caught.value.detail
    assert docker.pulls() == []


def test_verify_runs_only_the_named_stage_and_copies_its_template(tmp_path, project, stubs):
    manifest = cli.verify(Adapter(project / "lambda-validation.toml"), tmp_path / "out", stages=["prod"])
    assert [call["stage"] for call in stubs["package"]] == ["prod"]
    assert [a["stage"] for a in manifest["artifacts"]] == ["prod"]
    assert json.loads((tmp_path / "out" / "template-prod.json").read_text()) == {"stage": "prod"}
    assert not (tmp_path / "out" / "template-dev.json").exists()


def test_verify_output_must_be_outside_the_checkout(project, stubs):
    with pytest.raises(ValidationError) as caught:
        cli.verify(Adapter(project / "lambda-validation.toml"), project / "evidence")
    assert caught.value.code == "BUILD" and "outside the checkout" in caught.value.detail
    assert not (project / "evidence").exists()
