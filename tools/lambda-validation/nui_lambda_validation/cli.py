"""``nui-lambda-validation``: one entry point for the shared checks. Exit 1 prints ``CODE: detail``."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

from . import artifact, backstop, build, container, negatives, offline
from .config import Adapter
from .errors import ValidationError, fail
from .extract import extract
from .metadata import read_pins, zip_distributions
from .zipcheck import open_zip

_PIN = re.compile(r"^nui-lambda-validation @ git\+https://[^@\s]+@([0-9a-f]{40})#subdirectory=tools/lambda-validation$")


REQUIREMENT = "requirements-validation.txt"


def self_check(requirement):
    """The installed tool must come from exactly the commit the requirement file pins."""
    try:
        text = Path(requirement).read_text()
    except OSError as error:
        fail("SELF_CHECK", f"{requirement}: {error}")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    match = _PIN.match(lines[0]) if len(lines) == 1 else None
    if not match:
        fail("SELF_CHECK", f"{requirement}: expected one full-SHA VCS line for nui-lambda-validation")
    try:
        dist = importlib_metadata.distribution("nui-lambda-validation")
    except importlib_metadata.PackageNotFoundError:
        fail("SELF_CHECK", "nui-lambda-validation is not installed as a distribution")
    # The record describes the installed copy; a different copy earlier on sys.path would run instead.
    if Path(str(dist.locate_file("nui_lambda_validation/cli.py"))).resolve() != Path(__file__).resolve():
        fail("SELF_CHECK", f"running {__file__}, not the installed distribution's copy")
    raw = dist.read_text("direct_url.json")
    try:
        commit = json.loads(raw or "{}").get("vcs_info", {}).get("commit_id")
    except (ValueError, AttributeError) as error:
        fail("SELF_CHECK", f"unreadable direct_url.json: {error}")
    if commit != match.group(1):
        fail("SELF_CHECK", f"installed from {commit}, requirement pins {match.group(1)}")
    return {"commit": commit}


def verify(adapter, output, stages=None, keep_extracted=False):
    """Committed-source build of every stage, then secret scan, artifact checks, extraction proof,
    container smoke, pip check and (first stage) negative controls. Templates are copied for the
    adapter's own assertions."""
    output = Path(output).resolve()
    if output.is_relative_to(adapter.root) or output.exists():
        fail("BUILD", f"output must be new and outside the checkout: {output}")
    output.mkdir(parents=True)
    home = output / "home"
    home.mkdir()
    tree = output / "source"
    manifest = build.committed_source(adapter.root, tree)
    # Every verification input (this adapter, its locks, its smoke script) comes from the
    # committed tree, so an uncommitted edit cannot weaken a run the manifest attributes to HEAD.
    # Only the serverless binary comes from the checkout, through its node_modules.
    committed_path = tree / adapter.path.name
    if not committed_path.is_file():
        fail("BUILD", f"{adapter.path.name} is not committed at {manifest['head']}")
    checkout, adapter = adapter, Adapter(committed_path)
    # The tool that checks this commit must be the one this commit pins, read from the same
    # committed tree, so a working-copy pin edit cannot stand in for the committed one.
    manifest["tool"] = self_check(tree / REQUIREMENT)
    spec = adapter.require("build", ("stages", "config_path", "config_text"))
    box = adapter.require("container", ("image", "smoke"))
    if not spec["stages"]:
        fail("ADAPTER_CONFIG", "[build] stages is empty, so nothing would be built or checked")
    unknown = sorted(set(stages or ()) - set(spec["stages"]))
    if unknown:
        fail("ADAPTER_CONFIG", f"stages not in [build] stages: {unknown}")
    stages = stages or spec["stages"]
    files = {spec["config_path"].format(stage=stage): spec["config_text"] for stage in spec["stages"]}
    build.prepare(tree, checkout.root, spec.get("substitutions", []), files)
    env = build.clean_env(home)
    deps = adapter.require("dependencies")
    proof = output / "proof"
    proof.mkdir()
    if deps["mode"] == "exact":
        (proof / "pins.json").write_text(json.dumps(read_pins(adapter.file(deps["pins"])), indent=2) + "\n")
    image = box["image"]
    machine = adapter.target["machine"]
    ids = {name: container.ensure_image(name, machine) for name in [image, *spec.get("build_images", [])]}
    # Every run below names the checked ID, never the tag, so the record is what executed.
    run_image = ids[image]
    manifest.update(
        image=image,
        image_id=run_image,
        image_platform=container.PLATFORM[machine],
        image_platform_id=container.platform_id(run_image, machine),
        image_digests=container.image_digests(run_image),
        build_images={name: container.image_digests(ids[name]) for name in spec.get("build_images", [])},
        build_image_ids={name: ids[name] for name in spec.get("build_images", [])},
        artifacts=[],
    )
    serverless = checkout.file(spec.get("serverless", "node_modules/.bin/serverless"))
    for index, stage in enumerate(stages):
        archive, template = build.package(tree, stage, serverless, env, output / f"build-{stage}.log")
        backstop.secret_scan(archive.parent)
        report = artifact.check(archive, adapter)
        with open_zip(archive) as bundle:
            dists = zip_distributions(bundle)
        report.update(stage=stage, path=str(archive))
        (output / f"zip-{stage}.json").write_text(json.dumps(report, indent=2) + "\n")
        if template.exists():
            shutil.copyfile(template, output / f"template-{stage}.json")
        if index == 0:
            report["negatives"] = negatives.run(archive, adapter, output / f"negatives-{stage}")
        extracted = output / f"extracted-{stage}"
        report["extraction"] = extract(archive, extracted)
        (proof / "distributions.json").write_text(
            json.dumps({n: {"version": d["version"], "requires": d["requires"]} for n, d in dists.items()}, indent=2)
            + "\n"
        )
        smoke_env = dict(box.get("env", {}), VALIDATION_STAGE=stage)
        script = adapter.file(box["smoke"])
        major, minor = adapter.target["python"].split(".")
        version_log = output / f"python-version-{stage}.log"
        same = f"import sys; sys.exit(0 if sys.version_info[:2] == ({int(major)}, {int(minor)}) else 1)"
        if container.run(container.argv(run_image, extracted, machine, args=("-c", same)), version_log):
            fail("BUILD", f"{image} is not Python {adapter.target['python']}, the [target] the host checks used")
        smoke_log = output / f"smoke-{stage}.log"
        if container.run(container.argv(run_image, extracted, machine, smoke_env, script, proof=proof), smoke_log):
            fail("BUILD", f"container smoke failed for {stage} ({_runtime_failure(smoke_log)}); see {smoke_log}")
        # The artifact on PYTHONPATH: `python -m pip` drops the working directory from sys.path.
        pip_log = output / f"pip-check-{stage}.log"
        pip_check = container.argv(
            run_image, extracted, machine, smoke_env, args=("-m", "pip", "check"), pythonpath=container.TASK_ROOT
        )
        if container.run(pip_check, pip_log):
            fail("BUILD", f"pip check failed for {stage}; see {pip_log}")
        if not keep_extracted:
            shutil.rmtree(extracted)
        manifest["artifacts"].append(report)
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _runtime_failure(log):
    """The in-container check's own code, from the last ``RuntimeCheckError`` in the smoke log."""
    found = re.findall(r"RuntimeCheckError: ([A-Z_]+: .*)", Path(log).read_text(errors="replace"))
    return found[-1][:300] if found else "no runtime check code in the log"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nui-lambda-validation", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("self-check", help="installed commit equals the pinned requirement")
    p.add_argument("--requirement", required=True, type=Path)
    p = sub.add_parser("check-zip", help="host-side checks of one ZIP")
    p.add_argument("zip", type=Path)
    p.add_argument("--config", required=True, type=Path)
    p = sub.add_parser("negatives", help="standard mutants of one ZIP must be rejected for the intended reason")
    p.add_argument("zip", type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("extract", help="extract and prove member-to-file hashes")
    p.add_argument("zip", type=Path)
    p.add_argument("destination", type=Path)
    p = sub.add_parser("verify", help="committed-source build and every artifact check, per stage")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--stage", action="append")
    p.add_argument("--keep-extracted", action="store_true")
    p = sub.add_parser("offline-pytest", help="run the app's pytest suite offline under the adapter policy")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--python", required=True, type=Path, help="the application's own interpreter")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p = sub.add_parser("secret-scan", help="deploy-time secret backstop on a package directory")
    p.add_argument("directory", type=Path)
    p = sub.add_parser("check-backstop", help="shell backstop greps for the canonical pattern")
    p.add_argument("script", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "offline-pytest":
            adapter = Adapter(args.config)
            policy = adapter.require("offline")
            extra = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
            return offline.run(
                args.python,
                adapter.root,
                list(policy.get("args", [])) + extra,
                policy.get("plugins", []),
                policy.get("allowed_skips", []),
                policy.get("passthrough_env", []),
                policy.get("env", {}),
                policy.get("not_loaded", {}),
            )
        if args.command == "self-check":
            result = self_check(args.requirement)
        elif args.command == "check-zip":
            result = artifact.check(args.zip, Adapter(args.config))
        elif args.command == "negatives":
            result = negatives.run(args.zip, Adapter(args.config), args.output)
        elif args.command == "extract":
            result = extract(args.zip, args.destination)
        elif args.command == "verify":
            result = verify(Adapter(args.config), args.output, args.stage, args.keep_extracted)
        elif args.command == "secret-scan":
            result = backstop.secret_scan(args.directory)
        else:
            result = backstop.check_script(args.script)
    except ValidationError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError) as error:
        # A missing file, interpreter, git or docker still exits with a code rather than a traceback.
        print(f"BUILD: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
