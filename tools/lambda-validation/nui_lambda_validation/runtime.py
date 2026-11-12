"""Checks that run inside the Lambda runtime image against the extracted ZIP at ``/var/task``.

Stdlib only, plus the image's own ``pip._vendor.packaging``: this module is mounted
read-only beside the artifact and must not bring a distribution of its own into the
interpreter, so nothing it loads can satisfy or pollute the application's metadata.
A failure raises ``RuntimeCheckError`` with a code from ``errors.CODES``.
"""

import ctypes
import importlib
import importlib.metadata as metadata
import socket
import sys
from pathlib import Path

TASK_ROOT = Path("/var/task")
SDK = ("boto3", "botocore", "s3transfer")
SDK_MODES = ("owned", "baseline-runtime")


class RuntimeCheckError(AssertionError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _fail(code, detail):
    raise RuntimeCheckError(code, detail)


def _requirement(raw):
    from pip._vendor.packaging.requirements import Requirement

    return Requirement(raw)


def _satisfies(specifier, version):
    """The host's rule (``metadata._contains``): a prerelease only where the specifier names one."""
    return specifier.contains(version, prereleases=bool(specifier.prereleases))


def _normalize(name):
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def install_network_guard():
    """Refuse outbound TCP and UDP from this interpreter, and prove it with probes."""

    def blocked(*args, **kwargs):
        raise RuntimeError("offline check: outbound networking blocked")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    socket.socket.sendmsg = blocked
    socket.create_connection = blocked
    probes = (
        lambda: socket.create_connection(("192.0.2.1", 443)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("192.0.2.1", 443)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"probe", ("192.0.2.1", 9)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendmsg([b"probe"], [], 0, ("192.0.2.1", 9)),
    )
    for probe in probes:
        try:
            probe()
        except RuntimeError:
            continue
        _fail("OFFLINE_GUARD", "network guard ineffective")
    return "NETWORK_GUARD_EFFECTIVE"


def assert_no_conftest():
    leaked = sorted(name for name in sys.modules if name == "conftest" or name.endswith(".conftest"))
    if leaked:
        _fail("OFFLINE_GUARD", f"test configuration imported into the runtime check: {leaked}")


def assert_tool_isolated(root=TASK_ROOT):
    """The checker itself must not be visible as a distribution, nor live in the artifact."""
    here = Path(__file__).resolve().parent
    if here.is_relative_to(root):
        _fail("TOOL_IN_ARTIFACT", f"checker loaded from the artifact: {here}")
    if any(here.parent.glob("*.dist-info")):
        _fail("TOOL_IN_ARTIFACT", f"checker mount carries distribution metadata: {here.parent}")
    try:
        metadata.distribution("nui-lambda-validation")
    except metadata.PackageNotFoundError:
        return
    _fail("TOOL_IN_ARTIFACT", "nui-lambda-validation metadata is importable in the runtime")


def check_sdk(mode, root=TASK_ROOT, names=SDK):
    """``owned``: each SDK module and its metadata come from the artifact, versions agree, and
    boto3's own requirements on the others hold. ``baseline-runtime``: report origins only, for
    a baseline artifact that relied on the runtime-provided SDK. There is no default."""
    if mode not in SDK_MODES:
        _fail("ADAPTER_CONFIG", f"SDK mode must be one of {SDK_MODES}, got {mode!r}")
    modules = {name: importlib.import_module(name) for name in names}
    report = {
        name: {"file": module.__file__, "version": getattr(module, "__version__", None)}
        for name, module in modules.items()
    }
    if mode == "baseline-runtime":
        return report
    for name, module in modules.items():
        if not Path(module.__file__).resolve().is_relative_to(root):
            _fail("SDK_PROVENANCE", f"{name} imported from {module.__file__}")
        dist = metadata.distribution(name)
        if Path(dist.locate_file("")).resolve() != root.resolve():
            _fail("SDK_PROVENANCE", f"{name} metadata located at {dist.locate_file('')}")
        if dist.version != module.__version__:
            _fail("SDK_VERSION", f"{name} module {module.__version__} != metadata {dist.version}")
    wanted = {_normalize(n) for n in names}
    for raw in metadata.requires(names[0]) or []:
        req = _requirement(raw)
        if _normalize(req.name) in wanted and (req.marker is None or req.marker.evaluate({"extra": ""})):
            version = metadata.version(req.name)
            if not _satisfies(req.specifier, version):
                _fail("SDK_CONSTRAINT", f"{names[0]} requires {req}, artifact has {version}")
    return report


def check_native(modules, plain_libraries=(), root=TASK_ROOT):
    """Import each named extension from the artifact with this interpreter's ABI; load declared plain libraries."""
    tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    loaded = []
    for name in modules:
        path = Path(importlib.import_module(name).__file__)
        if not path.resolve().is_relative_to(root) or path.suffix != ".so":
            _fail("NATIVE_EXPECTED", f"{name} loaded from {path}")
        if tag not in path.name and ".abi3.so" not in path.name:
            _fail("NATIVE_ABI", f"{name}: {path.name} is not {tag} or abi3")
        loaded.append(str(path))
    for relative in plain_libraries:
        path = root / relative
        if not path.is_file():
            _fail("NATIVE_EXPECTED", f"declared library missing: {path}")
        ctypes.CDLL(str(path))
        loaded.append(str(path))
    return loaded


def load_proof(name, directory="/proof"):
    """Read a JSON file the host wrote for this smoke (``pins.json``, ``distributions.json``)."""
    import json

    return json.loads((Path(directory) / name).read_text())


def check_inventory(pins=None, root=TASK_ROOT, manifest=None):
    """Distributions installed in the artifact: unique, equal to ``pins`` when given, every
    unconditional requirement (markers evaluated in this runtime) satisfied inside the artifact,
    and identical to a host-side ``manifest`` when given."""
    from pip._vendor.packaging.version import Version

    installed = {}
    for dist in metadata.distributions(path=[str(root)]):
        key = _normalize(dist.metadata["Name"])
        if key in installed:
            _fail("METADATA_DUPLICATE", key)
        installed[key] = dist
    if pins is not None:
        actual = {name: dist.version for name, dist in installed.items()}
        if actual != pins:
            _fail("PIN_MISMATCH", f"artifact {sorted(set(actual) ^ set(pins))} or versions differ from pins")
    for name, dist in installed.items():
        for raw in dist.requires or []:
            req = _requirement(raw)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            dependency = _normalize(req.name)
            if dependency not in installed:
                _fail("CLOSURE_MISSING", f"{name} requires {req}, absent from artifact")
            if not _satisfies(req.specifier, Version(installed[dependency].version)):
                _fail("CLOSURE_DEPENDENCY", f"{name} requires {req}, artifact has {installed[dependency].version}")
    if manifest is not None:
        seen = {name: {"version": d.version, "requires": d.requires or []} for name, d in installed.items()}
        if seen != manifest:
            _fail("EXTRACTION_MISMATCH", "runtime distribution view differs from the host manifest")
    return {name: dist.version for name, dist in sorted(installed.items())}
