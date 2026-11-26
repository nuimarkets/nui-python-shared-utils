"""Check built distributions against pyproject.toml (Python 3.11+, build environment)."""

import argparse
import configparser
from email.parser import BytesParser
from pathlib import Path
import tarfile
import tomllib
import zipfile

from packaging.requirements import Requirement
from packaging.markers import Marker


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def check_metadata(raw: bytes, project: dict) -> None:
    metadata = BytesParser().parsebytes(raw)
    check(metadata["Name"] == project["name"], "Distribution name differs from pyproject.toml")
    check(metadata["Requires-Python"] == project["requires-python"], "Python requirement differs from pyproject.toml")
    expected = {str(Requirement(value)) for value in project["dependencies"]}
    for extra, values in project["optional-dependencies"].items():
        for value in values:
            requirement = Requirement(value)
            marker = f"({requirement.marker}) and " if requirement.marker else ""
            requirement.marker = Marker(f'{marker}extra == "{extra}"')
            expected.add(str(requirement))
    actual = {str(Requirement(value)) for value in metadata.get_all("Requires-Dist", [])}
    check(
        actual == expected, f"Dependency metadata differs: missing={expected - actual}, unexpected={actual - expected}"
    )
    check(
        set(metadata.get_all("Provides-Extra", [])) == set(project["optional-dependencies"]),
        "Published extras differ from pyproject.toml",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="Directory containing one wheel and one source distribution")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    configuration = tomllib.loads((root / "pyproject.toml").read_text())
    project = configuration["project"]
    wheels = list(args.dist.glob("*.whl"))
    sources = list(args.dist.glob("*.tar.gz"))
    check(len(wheels) == len(sources) == 1, "Expected exactly one wheel and one source distribution")
    expected_files = {path.relative_to(root).as_posix() for path in (root / "nui_shared_utils").rglob("*.py")}
    for package, patterns in configuration["tool"]["setuptools"].get("package-data", {}).items():
        package_root = root.joinpath(*package.split("."))
        for pattern in patterns:
            expected_files.update(
                path.relative_to(root).as_posix() for path in package_root.glob(pattern) if path.is_file()
            )
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        check(len(metadata_paths) == 1, "Expected one wheel metadata directory")
        info = metadata_paths[0].rsplit("/", 1)[0]
        check_metadata(wheel.read(metadata_paths[0]), project)
        payload = {name for name in names if not name.startswith(info + "/") and not name.endswith("/")}
        check(
            payload == expected_files,
            f"Wheel payload differs: missing={expected_files - payload}, unexpected={payload - expected_files}",
        )
        entry_points = configparser.ConfigParser()
        entry_points.read_string(wheel.read(info + "/entry_points.txt").decode())
        check(
            dict(entry_points["console_scripts"]) == project["scripts"], "CLI entry points differ from pyproject.toml"
        )
    with tarfile.open(sources[0]) as source:
        members = source.getmembers()
        prefixes = {member.name.split("/", 1)[0] for member in members}
        check(len(prefixes) == 1, "Expected one source distribution root")
        prefix = prefixes.pop() + "/"
        paths = {member.name.removeprefix(prefix) for member in members if member.isfile()}
        check(expected_files <= paths, f"Source distribution omits package files: {expected_files - paths}")
        check(
            not any(path.startswith(("tools/", "nui_lambda_validation/")) for path in paths),
            "Source distribution includes the separate validator",
        )
        metadata = source.extractfile(prefix + "PKG-INFO")
        check(metadata is not None, "Source distribution has no PKG-INFO")
        check_metadata(metadata.read(), project)
    print(
        f"Distribution contracts passed: {len(expected_files)} package files, {len(project['optional-dependencies'])} extras, CLI entry points"
    )


if __name__ == "__main__":
    main()
