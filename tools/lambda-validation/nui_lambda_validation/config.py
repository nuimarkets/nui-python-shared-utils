"""Adapter configuration: one ``lambda-validation.toml`` per repository.

Every section, key and value type is checked when the file is read: a misspelled key
would otherwise silently drop the check it configures.
"""

from pathlib import Path

import tomllib

from .errors import fail

MODES = {"exact", "roots-only"}
MACHINES = {"x86_64", "aarch64"}

# Value kinds. A dict is a nested table with exactly these keys allowed; a one-item list
# is an array of such tables, each of which must carry every key.
STR, INT, BOOL = "str", "int", "bool"
STRS = "list[str]"
STR_MAP = "map[str, str]"
STRS_MAP = "map[str, list[str]]"
SCHEMA = {
    "target": {"python": STR, "python_full_version": STR, "machine": STR},
    "zip": {
        "required": STRS,
        "uncompressed_limit": INT,
        "forbidden": {
            "prefixes": STRS,
            "parts": STRS,
            "part_prefixes": STRS,
            "suffixes": STRS,
            "root_suffixes": STRS,
        },
    },
    "native": {"extensions": INT, "require": STRS, "plain_libraries": STRS, "require_any": BOOL},
    "dependencies": {
        "mode": STR,
        "pins": STR,
        "roots": STR,
        "accepted_majors": STR,
        "test_lock": STR,
        "test_only": STRS_MAP,
    },
    "negatives": {"not_applicable": STR_MAP},
    "offline": {
        "plugins": STRS,
        "args": STRS,
        "allowed_skips": STRS,
        "passthrough_env": STRS,
        "env": STR_MAP,
        "not_loaded": STR_MAP,
    },
    "build": {
        "stages": STRS,
        "config_path": STR,
        "config_text": STR,
        "serverless": STR,
        "build_images": STRS,
        "substitutions": [{"file": STR, "old": STR, "new": STR, "count": INT}],
    },
    "container": {"image": STR, "smoke": STR, "env": STR_MAP},
}


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check(value, kind, where):
    if isinstance(kind, dict):
        if not isinstance(value, dict):
            fail("ADAPTER_CONFIG", f"{where} must be a table")
        unknown = sorted(set(value) - set(kind))
        if unknown:
            fail("ADAPTER_CONFIG", f"{where}: unknown keys {unknown}")
        for key, item in value.items():
            _check(item, kind[key], f"{where}.{key}")
        return
    if isinstance(kind, list):
        if not isinstance(value, list):
            fail("ADAPTER_CONFIG", f"{where} must be an array of tables")
        for index, item in enumerate(value):
            _check(item, kind[0], f"{where}[{index}]")
            missing = sorted(set(kind[0]) - set(item))
            if missing:
                fail("ADAPTER_CONFIG", f"{where}[{index}] needs {missing}")
        return
    if kind == STR:
        ok = isinstance(value, str)
    elif kind == INT:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == BOOL:
        ok = isinstance(value, bool)
    elif kind == STRS:
        ok = _strings(value)
    elif kind == STR_MAP:
        ok = isinstance(value, dict) and all(isinstance(item, str) for item in value.values())
    else:  # STRS_MAP
        ok = isinstance(value, dict) and all(_strings(item) for item in value.values())
    if not ok:
        fail("ADAPTER_CONFIG", f"{where} must be {kind}, got {value!r}")


class Adapter:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        try:
            self.data = tomllib.loads(self.path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            fail("ADAPTER_CONFIG", f"{path}: {error}")
        unknown = set(self.data) - set(SCHEMA)
        if unknown:
            fail("ADAPTER_CONFIG", f"unknown sections {sorted(unknown)}")
        _check(self.data, SCHEMA, "adapter")
        target = self.require("target")
        for key in ("python", "python_full_version", "machine"):
            if key not in target:
                fail("ADAPTER_CONFIG", f"[target] needs {key}")
        if not target["python_full_version"].startswith(target["python"] + "."):
            fail("ADAPTER_CONFIG", "[target] python_full_version does not match python")
        if target["machine"] not in MACHINES:
            fail("ADAPTER_CONFIG", f"[target] machine must be one of {sorted(MACHINES)}")
        deps = self.require("dependencies")
        if deps.get("mode") not in MODES:
            fail("ADAPTER_CONFIG", f"[dependencies] mode must be one of {sorted(MODES)}")
        if deps["mode"] == "exact" and "pins" not in deps:
            fail("ADAPTER_CONFIG", "[dependencies] exact mode needs pins")
        if "roots" not in deps:
            fail("ADAPTER_CONFIG", "[dependencies] needs roots")

    def require(self, section, keys=()):
        if section not in self.data:
            fail("ADAPTER_CONFIG", f"{self.path}: missing [{section}]")
        table = self.data[section]
        missing = [key for key in keys if key not in table]
        if missing:
            fail("ADAPTER_CONFIG", f"[{section}] needs {missing}")
        return table

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
