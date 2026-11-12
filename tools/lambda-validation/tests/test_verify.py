"""The ``verify`` orchestration with packaging and Docker stubbed: which inputs it reads, and when it refuses."""

import json
import os
import subprocess

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
    calls = {"package": [], "digests": [], "run": []}
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
        return target / "app.zip", target / "cloudformation-template-update-stack.json"

    def digests(image):
        calls["digests"].append(image)
        return [f"{image}@sha256:{'0' * 64}"]

    def run(command, log):
        calls["run"].append(command)
        return 0

    monkeypatch.setattr(build, "package", package)
    monkeypatch.setattr(container, "image_digests", digests)
    monkeypatch.setattr(container, "run", run)
    return calls


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
    assert all(call["serverless_yml"].count("000000000000") == 2 for call in stubs["package"])
    assert all(call["config"] == "SYNTHETIC: value\n" for call in stubs["package"])
    assert stubs["package"][0]["serverless"] == project / "node_modules" / ".bin" / "serverless"
    # smoke and pip check per stage
    assert len(stubs["run"]) == 4


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
    assert code_of(cli.verify, Adapter(nested / "lambda-validation.toml"), tmp_path / "out") == "BUILD"


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
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    block = readme.split("```toml\n", 1)[1].split("```", 1)[0]
    (tmp_path / "lambda-validation.toml").write_text(block)
    Adapter(tmp_path / "lambda-validation.toml")
