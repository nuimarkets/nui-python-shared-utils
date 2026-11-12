"""Adapter configuration: one ``lambda-validation.toml`` per repository."""

from pathlib import Path

import tomllib

from .errors import fail

SECTIONS = {"target", "zip", "native", "dependencies", "negatives", "offline", "build", "container"}
MODES = {"exact", "roots-only"}


class Adapter:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        try:
            self.data = tomllib.loads(self.path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            fail("ADAPTER_CONFIG", f"{path}: {error}")
        unknown = set(self.data) - SECTIONS
        if unknown:
            fail("ADAPTER_CONFIG", f"unknown sections {sorted(unknown)}")
        target = self.require("target")
        for key in ("python", "python_full_version", "machine"):
            if key not in target:
                fail("ADAPTER_CONFIG", f"[target] needs {key}")
        if not target["python_full_version"].startswith(target["python"] + "."):
            fail("ADAPTER_CONFIG", "[target] python_full_version does not match python")
        deps = self.require("dependencies")
        if deps.get("mode") not in MODES:
            fail("ADAPTER_CONFIG", f"[dependencies] mode must be one of {sorted(MODES)}")
        if deps["mode"] == "exact" and "pins" not in deps:
            fail("ADAPTER_CONFIG", "[dependencies] exact mode needs pins")
        if "roots" not in deps:
            fail("ADAPTER_CONFIG", "[dependencies] needs roots")

    def require(self, section):
        if section not in self.data:
            fail("ADAPTER_CONFIG", f"{self.path}: missing [{section}]")
        return self.data[section]

    def get(self, section):
        return self.data.get(section, {})

    def file(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            fail("ADAPTER_CONFIG", f"path escapes the repository: {relative}")
        return path

    @property
    def target(self):
        return self.data["target"]
