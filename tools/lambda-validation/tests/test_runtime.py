"""In-container checks. Host tests run them against a fake task root in a subprocess; container
tests run them in the real Lambda image against a synthetic artifact."""

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import metadata

from nui_lambda_validation import container, runtime

PACKAGE = Path(runtime.__file__).resolve().parent
IMAGE = "public.ecr.aws/lambda/python:3.12"


def fake_root(root, sdk_version="1.44.1", metadata_version=None, drop=()):
    versions = {"boto3": sdk_version, "botocore": sdk_version, "s3transfer": "0.19.3"}
    requires = {
        "boto3": [f"botocore<1.45.0,>={sdk_version}", "s3transfer<0.20.0,>=0.19.0"],
        "botocore": [],
        "s3transfer": [],
    }
    for name, version in versions.items():
        if name in drop:
            continue
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text(f"__version__ = '{version}'\n")
        info = root / f"{name}-{metadata_version or version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_bytes(metadata(name, metadata_version or version, requires[name]))
    return root


def run_host(root, body):
    """Execute ``body`` in a fresh interpreter with ``root`` first on the path, as the smoke does."""
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(root)!r})
        sys.path.insert(1, {str(PACKAGE.parent)!r})
        import pip  # the image ships pip; so does this venv
        from nui_lambda_validation import runtime
        root = Path({str(root)!r})
        try:
        {textwrap.indent(textwrap.dedent(body), '    ')}
        except runtime.RuntimeCheckError as error:
            print("CODE", error.code, error)
            raise SystemExit(1)
        print("OK")
        """
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True)
    return result.stdout + result.stderr


def test_sdk_owned_passes_and_mode_is_required(tmp_path):
    root = fake_root(tmp_path / "task")
    assert "OK" in run_host(root, "runtime.check_sdk('owned', root=root)")
    assert "CODE ADAPTER_CONFIG" in run_host(root, "runtime.check_sdk(None, root=root)")
    assert "CODE ADAPTER_CONFIG" in run_host(root, "runtime.check_sdk('', root=root)")


def test_sdk_version_and_constraint_failures(tmp_path):
    root = fake_root(tmp_path / "a", metadata_version="1.44.2")
    (root / "boto3-1.44.2.dist-info" / "METADATA").write_bytes(
        metadata("boto3", "1.44.2", ["botocore<1.45.0,>=1.44.1"])
    )
    assert "CODE SDK_VERSION" in run_host(root, "runtime.check_sdk('owned', root=root)")
    root = fake_root(tmp_path / "b")
    (root / "boto3-1.44.1.dist-info" / "METADATA").write_bytes(metadata("boto3", "1.44.1", ["botocore>=1.46"]))
    assert "CODE SDK_CONSTRAINT" in run_host(root, "runtime.check_sdk('owned', root=root)")


def test_sdk_provenance_when_metadata_lives_elsewhere(tmp_path):
    root = fake_root(tmp_path / "task")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.move(str(root / "botocore-1.44.1.dist-info"), elsewhere)
    body = f"sys.path.append({str(elsewhere)!r}); runtime.check_sdk('owned', root=root)"
    assert "CODE SDK_PROVENANCE" in run_host(root, body)


def test_inventory(tmp_path):
    root = fake_root(tmp_path / "task")
    pins = {"boto3": "1.44.1", "botocore": "1.44.1", "s3transfer": "0.19.3"}
    assert "OK" in run_host(root, f"runtime.check_inventory({pins!r}, root=root)")
    assert "CODE PIN_MISMATCH" in run_host(
        root, f"runtime.check_inventory({dict(pins, botocore='1.44.2')!r}, root=root)"
    )
    root = fake_root(tmp_path / "missing", drop=("s3transfer",))
    assert "CODE CLOSURE_MISSING" in run_host(root, "runtime.check_inventory(None, root=root)")


def test_network_guard_and_conftest(tmp_path):
    root = fake_root(tmp_path / "task")
    out = run_host(root, "print(runtime.install_network_guard())")
    assert "NETWORK_GUARD_EFFECTIVE" in out
    assert "CODE OFFLINE_GUARD" in run_host(root, "sys.modules['conftest'] = object(); runtime.assert_no_conftest()")


@pytest.mark.parametrize(
    "error, expected",
    [
        ("runtime.NetworkBlocked('blocked')", "NETWORK_GUARD_EFFECTIVE TIMEOUTS [2.0, 2.0, 2.0, 2.0]"),
        ("OSError('network unreachable')", "CODE OFFLINE_GUARD"),
        ("RuntimeError('some other error')", "CODE OFFLINE_GUARD"),
    ],
)
def test_network_guard_probe_accepts_only_its_own_refusal(tmp_path, error, expected):
    script = textwrap.dedent(
        f"""
        import socket, sys
        sys.path.insert(0, {str(PACKAGE.parent)!r})
        from nui_lambda_validation import runtime
        seen = []
        def record(*args, **kwargs):
            if args and isinstance(args[0], socket.socket):
                seen.append(args[0].gettimeout())
            else:
                seen.append(kwargs.get("timeout"))
            raise {error}
        runtime._blocked = record
        try:
            print(runtime.install_network_guard(), "TIMEOUTS", seen)
        except runtime.RuntimeCheckError as caught:
            print("CODE", caught.code, caught)
        """
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, timeout=60)
    out = result.stdout + result.stderr
    assert expected in out and "Traceback" not in out


def test_tool_isolation(tmp_path):
    root = fake_root(tmp_path / "task")
    body = "runtime.assert_tool_isolated(root=Path(runtime.__file__).resolve().parent.parent)"
    assert "CODE TOOL_IN_ARTIFACT" in run_host(root, body)


def docker_available():
    return (
        shutil.which("docker")
        and subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0
    )


@pytest.fixture(scope="module")
def image():
    if not docker_available():
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", IMAGE], check=True)
    return IMAGE


SMOKE = """
import sys
sys.path.insert(0, "/var/task")
from nui_lambda_validation import runtime
print(runtime.install_network_guard())
runtime.assert_tool_isolated()
runtime.check_sdk(sys.argv[1])
print("PINS", len(runtime.check_inventory(runtime.load_proof("pins.json"))))
"""


def run_container(tmp_path, root, mode):
    script = tmp_path / "smoke.py"
    script.write_text(SMOKE)
    proof = tmp_path / "proof"
    proof.mkdir(exist_ok=True)
    (proof / "pins.json").write_text(json.dumps({"boto3": "1.44.1", "botocore": "1.44.1", "s3transfer": "0.19.3"}))
    command = container.argv(IMAGE, root, "x86_64", script=script, args=(mode,), proof=proof)
    return subprocess.run(command, capture_output=True, text=True)


def test_container_owned_sdk(tmp_path, image):
    result = run_container(tmp_path, fake_root(tmp_path / "task"), "owned")
    assert result.returncode == 0, result.stderr
    assert "NETWORK_GUARD_EFFECTIVE" in result.stdout and "PINS 3" in result.stdout


def test_container_rejects_runtime_provided_sdk(tmp_path, image):
    # botocore missing from the artifact: the image's own copy must not satisfy ownership.
    result = run_container(tmp_path, fake_root(tmp_path / "task", drop=("botocore",)), "owned")
    assert result.returncode != 0
    assert "SDK_PROVENANCE" in result.stderr


def test_container_mount_is_read_only_and_offline(tmp_path, image):
    root = fake_root(tmp_path / "task")
    probe = (
        "import socket, pathlib\n"
        "try:\n    pathlib.Path('/var/task/x').write_text('x')\nexcept OSError:\n    print('RO_TASK')\n"
        "try:\n    pathlib.Path('/opt/nui-lambda-validation/nui_lambda_validation/x').write_text('x')\nexcept OSError:\n    print('RO_TOOL')\n"
        "s = socket.socket(); s.settimeout(2)\n"
        "try:\n    s.connect(('1.1.1.1', 443))\nexcept OSError:\n    print('NO_NETWORK')\n"
    )
    script = tmp_path / "probe.py"
    script.write_text(probe)
    result = subprocess.run(container.argv(IMAGE, root, "x86_64", script=script), capture_output=True, text=True)
    assert {"RO_TASK", "RO_TOOL", "NO_NETWORK"} <= set(result.stdout.split()), result.stdout + result.stderr


def test_container_runtime_module_on_older_image(tmp_path, image):
    """The 3.11 image ships an older vendored packaging (21.3); the module must still work there."""
    older = "public.ecr.aws/lambda/python:3.11"
    if subprocess.run(["docker", "image", "inspect", older], capture_output=True).returncode != 0:
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", "-q", older], check=True, capture_output=True)
    script = tmp_path / "smoke.py"
    script.write_text(SMOKE)
    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "pins.json").write_text(json.dumps({"boto3": "1.44.1", "botocore": "1.44.1", "s3transfer": "0.19.3"}))
    command = container.argv(older, fake_root(tmp_path / "task"), "x86_64", script=script, args=("owned",), proof=proof)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "PINS 3" in result.stdout


def test_sdk_module_imported_from_outside_the_artifact(tmp_path):
    # Metadata in the artifact, module code shadowed from elsewhere: only the module-file check catches it.
    root = fake_root(tmp_path / "task")
    shadow = tmp_path / "shadow" / "botocore"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("__version__ = '1.44.1'\n")
    body = f"sys.path.insert(0, {str(shadow.parent)!r}); runtime.check_sdk('owned', root=root)"
    out = run_host(root, body)
    assert "CODE SDK_PROVENANCE" in out and "imported from" in out


def test_every_code_raised_is_declared():
    """The runtime module raises its own error type, so nothing validates its codes at raise time."""
    import ast

    from nui_lambda_validation.errors import CODES

    package = Path(runtime.__file__).parent
    raised = set()
    for source in package.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) in ("fail", "_fail")
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                raised.add(node.args[0].value)
    assert raised and raised <= CODES, sorted(raised - CODES)


NATIVE_SMOKE = """
import sys
sys.path.insert(0, "/var/task")
from nui_lambda_validation import runtime
try:
    runtime.check_native(sys.argv[1:])
except runtime.RuntimeCheckError as error:
    print("CODE", error.code)
else:
    print("OK")
"""


def test_check_native_imports_from_the_artifact_with_this_abi(tmp_path, image):
    # A real extension module, taken from the image itself so its ABI matches the interpreter.
    name = "_csv.cpython-312-x86_64-linux-gnu.so"
    data = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/bin/cat",
            IMAGE,
            f"/var/lang/lib/python3.12/lib-dynload/{name}",
        ],
        capture_output=True,
        check=True,
    ).stdout
    root = fake_root(tmp_path / "task")
    (root / "vendored").mkdir()
    (root / "vendored" / "__init__.py").write_text("")
    (root / "vendored" / name).write_bytes(data)
    script = tmp_path / "native.py"
    script.write_text(NATIVE_SMOKE)

    def check(*modules):
        command = container.argv(IMAGE, root, "x86_64", script=script, args=modules)
        result = subprocess.run(command, capture_output=True, text=True)
        return result.stdout + result.stderr

    assert "OK" in check("vendored._csv")
    # importlib.metadata has already loaded the image's own _csv: imported from outside the artifact.
    assert "CODE NATIVE_EXPECTED" in check("_csv")
    assert "CODE NATIVE_EXPECTED" in check("boto3")  # not a shared object
    (root / "vendored" / name).rename(root / "vendored" / "_csv.so")
    assert "CODE NATIVE_ABI" in check("vendored._csv")  # loads, but carries no cpython-312 or abi3 tag


def test_inventory_duplicates_constraints_and_manifest(tmp_path):
    root = fake_root(tmp_path / "dup")
    shutil.copytree(root / "boto3-1.44.1.dist-info", root / "Boto3-1.44.1.dist-info")
    assert "CODE METADATA_DUPLICATE" in run_host(root, "runtime.check_inventory(None, root=root)")
    root = fake_root(tmp_path / "constraint")
    (root / "boto3-1.44.1.dist-info" / "METADATA").write_bytes(metadata("boto3", "1.44.1", ["botocore>=1.46"]))
    assert "CODE CLOSURE_DEPENDENCY" in run_host(root, "runtime.check_inventory(None, root=root)")
    root = fake_root(tmp_path / "manifest")
    manifest = {
        "boto3": {"version": "1.44.1", "requires": ["botocore<1.45.0,>=1.44.1", "s3transfer<0.20.0,>=0.19.0"]},
        "botocore": {"version": "1.44.1", "requires": []},
        "s3transfer": {"version": "0.19.3", "requires": []},
    }
    assert "OK" in run_host(root, f"runtime.check_inventory(None, root=root, manifest={manifest!r})")
    manifest["s3transfer"]["version"] = "0.19.4"
    out = run_host(root, f"runtime.check_inventory(None, root=root, manifest={manifest!r})")
    assert "CODE EXTRACTION_MISMATCH" in out


def test_tool_metadata_beside_the_mount_or_importable(tmp_path):
    root = fake_root(tmp_path / "task")
    mount = tmp_path / "mount"
    shutil.copytree(PACKAGE, mount / "nui_lambda_validation", ignore=shutil.ignore_patterns("__pycache__"))

    def isolated(extra_path):
        # -S: no site-packages, so an installed copy of this tool cannot answer for the mount.
        script = textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            sys.path[:0] = [{str(mount)!r}, {str(extra_path)!r}]
            from nui_lambda_validation import runtime
            try:
                runtime.assert_tool_isolated(root=Path({str(root)!r}))
            except runtime.RuntimeCheckError as error:
                print("CODE", error.code)
            else:
                print("OK")
            """
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", script], capture_output=True, text=True)
        return result.stdout + result.stderr

    assert "OK" in isolated(root)
    info = root / "nui_lambda_validation-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: nui-lambda-validation\nVersion: 0.1.0\n")
    assert "CODE TOOL_IN_ARTIFACT" in isolated(root)
    shutil.rmtree(info)
    # Any distribution metadata beside the mount, not only the tool's own name.
    (mount / "unrelated-1.0.dist-info").mkdir()
    assert "CODE TOOL_IN_ARTIFACT" in isolated(root)


def test_container_pip_check_sees_the_artifact(tmp_path, image):
    consistent = fake_root(tmp_path / "ok")
    broken = fake_root(tmp_path / "broken")
    (broken / "boto3-1.44.1.dist-info" / "METADATA").write_bytes(metadata("boto3", "1.44.1", ["botocore>=1.46"]))

    def pip_check(root):
        command = container.argv(IMAGE, root, "x86_64", args=("-m", "pip", "check"), pythonpath=container.TASK_ROOT)
        return subprocess.run(command, capture_output=True, text=True)

    assert pip_check(consistent).returncode == 0
    result = pip_check(broken)
    assert result.returncode != 0 and "botocore" in result.stdout + result.stderr


def test_inventory_rejects_a_prerelease_in_a_range(tmp_path):
    root = fake_root(tmp_path / "task")
    shutil.rmtree(root / "s3transfer-0.19.3.dist-info")
    info = root / "s3transfer-0.19.4rc1.dist-info"
    info.mkdir()
    (info / "METADATA").write_bytes(metadata("s3transfer", "0.19.4rc1"))
    assert "CODE CLOSURE_DEPENDENCY" in run_host(root, "runtime.check_inventory(None, root=root)")
    # boto3 naming the prerelease explicitly accepts it.
    (root / "boto3-1.44.1.dist-info" / "METADATA").write_bytes(
        metadata("boto3", "1.44.1", ["botocore<1.45.0,>=1.44.1", "s3transfer==0.19.4rc1"])
    )
    assert "OK" in run_host(root, "runtime.check_inventory(None, root=root)")


# (requirement, installed version, satisfied). The host and every image must agree on each row.
PRERELEASE_TABLE = [
    ("botocore<2.0a.0,>=1.37.4", "1.44.1", True),
    ("botocore<2.0a.0,>=1.37.4", "1.44.1rc1", False),
    ("x>=1,<2", "1.1rc1", False),
    ("x==1.1rc1", "1.1rc1", True),
    ("x>=2.0b1", "2.0b2", True),
    ("x>2.0b1", "2.0b2", False),
    ("x~=1.1rc1", "1.1rc2", True),
    ("x==1.1.*", "1.1.0rc1", False),
    ("x!=1.0rc1", "1.0rc2", False),
    ("x<=2.0rc1", "1.5rc1", True),
    ("x===1.0rc1", "1.0rc1", True),
    ("x", "1.0rc1", False),
]

TABLE_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from pip._vendor.packaging.requirements import Requirement
from nui_lambda_validation import runtime
for line in sys.stdin.read().splitlines():
    raw, version = line.split("|")
    print(runtime._satisfies(Requirement(raw).specifier, version))
"""


def test_prerelease_rule_is_the_same_on_the_host_and_in_both_images(tmp_path, image):
    from packaging.requirements import Requirement

    from nui_lambda_validation import metadata as host

    expected = [str(ok) for _, _, ok in PRERELEASE_TABLE]
    assert [str(host._contains(Requirement(r).specifier, v)) for r, v, _ in PRERELEASE_TABLE] == expected
    rows = "\n".join(f"{r}|{v}" for r, v, _ in PRERELEASE_TABLE)
    script = tmp_path / "table.py"
    script.write_text(TABLE_SCRIPT)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), str(PACKAGE.parent)], input=rows, capture_output=True, text=True
    )
    assert result.stdout.split() == expected, result.stderr
    for name in (IMAGE, "public.ecr.aws/lambda/python:3.11"):
        command = container.argv(
            name, fake_root(tmp_path / name.rsplit(":", 1)[1]), "x86_64", script=script, args=(container.TOOL_MOUNT,)
        )
        command[2:2] = ["--interactive"]  # docker run -i: the rows arrive on stdin
        result = subprocess.run(command, input=rows, capture_output=True, text=True)
        assert result.stdout.split() == expected, (name, result.stdout, result.stderr)


def test_declared_library_missing(tmp_path):
    root = fake_root(tmp_path / "task")
    out = run_host(root, "runtime.check_native([], plain_libraries=['vendor/missing.so'], root=root)")
    assert "CODE NATIVE_EXPECTED" in out, out
