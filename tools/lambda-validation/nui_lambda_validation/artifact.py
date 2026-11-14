"""Compose the host-side checks for one deployment ZIP under an adapter."""

import json

from . import native, zipcheck
from .config import Adapter
from .errors import fail
from .metadata import (
    check_majors,
    closure,
    read_pins,
    read_roots,
    target_environment,
    test_only,
    zip_distributions,
)


def check(archive, adapter: Adapter):
    """Member checks, native checks, then metadata."""
    target = adapter.target
    report = zipcheck.inspect(archive, adapter.get("zip"))
    deps = adapter.require("dependencies")
    with zipcheck.open_zip(archive) as bundle:
        report["native"] = native.inspect(bundle, target["python"], target["machine"], adapter.get("native"))
        distributions = zip_distributions(bundle)
        pins = read_pins(adapter.file(deps["pins"])) if deps["mode"] == "exact" else None
        roots = read_roots(adapter.file(deps["roots"]))
        environment = target_environment(target["python_full_version"], target["machine"])
        report["closure"] = closure(distributions, roots, environment, pins)
        if deps.get("accepted_majors"):
            path = adapter.file(deps["accepted_majors"])
            try:
                accepted = json.loads(path.read_text())
            except (OSError, ValueError) as error:
                fail("ADAPTER_CONFIG", f"{path}: {error}")
            check_majors(distributions, accepted, deps.get("calendar_versioned"))
        if deps.get("test_lock"):
            joint = read_pins(adapter.file(deps["test_lock"]))
            report["test_only_exclusions"] = test_only(
                bundle.namelist(),
                pins or {n: d["version"] for n, d in distributions.items()},
                joint,
                deps.get("test_only", {}),
            )
    report["distributions"] = {name: data["version"] for name, data in sorted(distributions.items())}
    report["dependency_mode"] = deps["mode"]
    return report
