# nui-lambda-validation

Build-time checks for Python AWS Lambda deployment ZIPs: what is in the ZIP, whether its
dependency metadata is coherent for the target runtime, whether its native code matches
the target ABI and architecture, and whether it behaves in the real Lambda image with no
network. Also an offline runner for the application's own pytest suite.

It is a **separate distribution** from `nui-python-shared-utils`. It never ships inside a
Lambda ZIP, does not depend on the runtime library, and has one dependency of its own,
`packaging==26.3`.

## Install and pin

Consumers pin a full commit SHA in `requirements-validation.txt`, one line:

```text
nui-lambda-validation @ git+https://github.com/nuimarkets/nui-python-shared-utils@<40-hex-sha>#subdirectory=tools/lambda-validation
```

Install it into its own environment, never the application's, then prove which commit is
installed (pip keeps an already-installed copy when the version string matches):

```bash
python -m venv .venv-validation
.venv-validation/bin/pip install -r requirements-validation.txt
.venv-validation/bin/nui-lambda-validation self-check --requirement requirements-validation.txt
```

Add `.venv-validation/` to `.gitignore` and keep `lambda-validation.toml`,
`requirements-validation.txt` and `.venv-validation` out of the deployment package.
`check-zip`, `negatives` and `verify` fail a ZIP that carries any of them, at any depth
(`TOOL_IN_ARTIFACT`).

`verify` runs the same check against the committed `requirements-validation.txt` (from
`git archive HEAD`, at the repository's top level) before it builds anything, and fails
`SELF_CHECK` when the running tool is not the commit pinned there.

## Commands

Every failure exits 1 and prints `CODE: detail` on stderr. `offline-pytest` is the one
exception once the suite starts: it exits with pytest's own status, and a refusal inside the
test interpreter (the network guard, the plugin policy) exits 3 with an `OFFLINE_GUARD:`
line. A refusal by the launcher, before the suite starts or after it exits 0 without a
finished run, is `CODE: detail` with exit 1 like every other command. Codes are listed in `nui_lambda_validation/errors.py`; negative controls
assert the code, not the text. A failure in the container smoke reports as `BUILD` with the
in-container check's own code in its detail.

| Command | What it does |
| --- | --- |
| `verify --config lambda-validation.toml --output DIR` | Fails `SELF_CHECK` unless the running tool is the commit the committed `requirements-validation.txt` pins. Pulls the runtime image and every `build_images` entry that is not on the host (`--platform` from `[target] machine`; a failed pull is `BUILD`), so a consumer needs no pull step of its own. Builds every stage from `git archive HEAD` with synthetic configuration, then per stage: secret scan, `check-zip`, extraction proof, a check that the image runs the `[target]` Python, the adapter's smoke script in the Lambda image, and `pip check` in the image over the artifact together with the image's own packages, as Lambda resolves them (`/var/task` first on `PYTHONPATH`, since `python -m pip` drops the working directory from `sys.path`; the artifact-only closure is the host check and `runtime.check_inventory`); the first stage also runs `negatives`. Each stage's package is written to `.serverless-validation-<stage>/` inside the build tree, so the repository's package patterns must exclude `.serverless-*/**`. `[container.env]` may not set a variable the container command sets itself. The adapter, its lock files and its smoke script are read from the committed tree, never the working copy, and the adapter must sit at the repository's top level. Records the digests of the runtime image and of every `build_images` entry. Copies each compiled template out for the repository's own assertions. `DIR` must be new and outside the checkout |
| `check-zip ZIP --config ...` | Member safety, secrets, exclusions, required paths, size, native ABI/ELF, dependency pins and closure, test-only leaks, optional major drift. Every member is read, so a corrupt one fails `UNREADABLE_ZIP` wherever it is, as does a member compressed with anything but stored or deflated (the verdict on bzip2, LZMA or zstd would depend on the Python running the check). Member names must be canonical (`./a.py`, `pkg//a.py` fail `UNSAFE_MEMBER`). A CPython extension must be named `.cpython-XY-<machine>-linux-gnu.so` or `.abi3.so` for the `[target]` (`NATIVE_ABI`). Distribution metadata is read from top-level `*.dist-info/METADATA` only, and a `*.dist-info` or `*.egg-info` directory at any lower depth, or a top-level one in any other case (`.DIST-INFO`), fails `METADATA_INVALID`. A requirement marker on `platform_release` or `platform_version` fails `MARKER_UNSUPPORTED`: only the check host could answer it, not the target. A `.serverless-validation-<stage>` directory is `TOOL_IN_ARTIFACT` |
| `negatives ZIP --config ... --output DIR` | Mutates the ZIP once per standard defect and once per configured exclusion rule; fails unless each mutant is rejected with its intended code. A standard mutant the artifact cannot support (no SDK path in `[zip] required`, no `botocore` metadata) must be declared in `[negatives.not_applicable]` with a reason, and the reasons are written beside the results |
| `extract ZIP DIR` | Extracts and proves the files on disk are exactly the members, byte for byte |
| `offline-pytest --config ... --python APP_PYTHON [-- ARGS]` | Runs the application's suite in its own interpreter under the offline policy below |
| `secret-scan DIR` | Deploy-time backstop: refuses a package directory with no ZIP, an unreadable ZIP, or a secret file |
| `check-backstop SCRIPT` | Fails when a shell backstop's `grep -E` pattern differs from the canonical one |
| `self-check --requirement FILE` | Fails unless the installed tool came from the pinned commit |

## Adapter: `lambda-validation.toml`

Every section, key and value type below is checked when the file is read, nested tables included: an unknown or misspelled key fails as `ADAPTER_CONFIG` rather than silently dropping the check it configures. So does a key that would never be read: an empty `pins`, `roots`, `accepted_majors` or `test_lock`, `pins` outside exact mode, `calendar_versioned` without `accepted_majors`, a `top_level` value with a slash or backslash, or a `config_path` placeholder other than `{stage}`. `verify` also needs `[build]` `stages` (non-empty), `config_path` and `config_text`, and `[container]` `image` and `smoke`.

```toml
[target]
python = "3.12"
python_full_version = "3.12.16"   # used for markers and Requires-Python
machine = "x86_64"                # or aarch64

[zip]
required = ["handler.py", "boto3/__init__.py"]
# uncompressed_limit = 262144000  # default 250 MiB; layers are not included
[zip.forbidden]
prefixes = ["tests/", "."]        # whole-path string prefixes
parts = ["fixtures"]              # an exact path component at any depth
part_prefixes = [".env", "env-"]  # a component starting with the value
suffixes = [".tsv"]               # whole-path endings
root_suffixes = [".md"]           # endings of top-level files only
top_level = ["scripts", "venv"]   # first path component equals the value: a bare top-level
                                  # file `scripts` as well as anything under `scripts/`

[native]
extensions = 10                   # optional exact count of CPython extension modules
require = ["yaml/_yaml.cpython-312-x86_64-linux-gnu.so"]
plain_libraries = ["vendor.libs/libfoo-1a2b.so.1"]   # shared objects with no ABI tag
# require_any = false            # default true: a ZIP with no CPython extension fails

[dependencies]
mode = "exact"                    # or "roots-only" for range requirements
pins = "requirements.txt"         # exact mode: one == per distribution, no markers
roots = "requirements.in"         # the direct requirements the closure starts from
# accepted_majors = "lambda-validation-majors.json"  # roots-only: fail if a major moves
# [dependencies.calendar_versioned]  # in accepted_majors, but the major is a year: presence only
# certifi = "major is the release year"
# test_lock = "requirements-test.txt"
# [dependencies.test_only]        # test-only distribution -> top-level names it installs
# pytest = ["pytest", "_pytest", "py.py"]

[negatives.not_applicable]        # mutant name -> reason, when a standard mutant cannot apply
# metadata-removed = "..."

[offline]
plugins = ["pytest_mock", "pytest_cov"]   # autoload is off; list every plugin the suite uses
args = ["tests"]
allowed_skips = []                         # exact node ids; any other skip, xfail or xpass fails the run
passthrough_env = []
[offline.not_loaded]                       # installed pytest11 plugins deliberately not loaded
# anyio = "suite has no async tests"
[offline.env]

[build]
stages = ["dev", "training", "prod"]
build_images = ["public.ecr.aws/sam/build-python3.12:latest-x86_64"]  # pulled if missing; digests recorded
config_path = "config/env-{stage}.yml"
# serverless = "node_modules/.bin/serverless"   # default, resolved in the checkout
config_text = """SYNTHETIC: value
"""
[[build.substitutions]]
file = "serverless.yml"
old = "${aws:accountId}"
new = "000000000000"
count = 2                                  # exact occurrence count, or the build fails

[container]
image = "public.ecr.aws/lambda/python:3.12"
smoke = "scripts/runtime-smoke.py"
[container.env]
AWS_LAMBDA_FUNCTION_NAME = "synthetic"
```

## The smoke script

The smoke runs in the Lambda image with the extracted ZIP read-only at `/var/task`, no
network, a read-only root, and this package mounted read-only at
`/opt/nui-lambda-validation` on `PYTHONPATH`. Only the package directory is mounted, with no
distribution metadata, so it cannot satisfy or pollute the application's own. `/proof`
carries `pins.json` (exact mode only) and `distributions.json`; a roots-only adapter passes
`check_inventory(None, manifest=runtime.load_proof("distributions.json"))` instead. A declared
plain library must resolve inside `/var/task` and load. Shared checks come first, then the
service's own assertions:

```python
import sys
sys.path.insert(0, "/var/task")
from nui_lambda_validation import runtime

runtime.install_network_guard()
runtime.assert_tool_isolated()
runtime.check_sdk("owned")                 # or "baseline-runtime"; no default
runtime.check_native(["yaml._yaml"], plain_libraries=["vendor.libs/libfoo-1a2b.so.1"])
runtime.check_inventory(runtime.load_proof("pins.json"))
runtime.assert_no_conftest()
# ... handler assertions for this service ...
```

`runtime` uses the standard library and the image's own `pip._vendor.packaging` only.

## Offline runner limits

The runner clears the environment (keeping only `PATH`, a temporary `HOME`, null AWS
config files, the adapter's `passthrough_env` and `[offline.env]`, neither of which may name
a variable the runner sets itself, `PYTEST_ADDOPTS` or `PYTEST_PLUGINS`), disables plugin
autoload, and replaces every
`socket.socket` connect, connect_ex, sendto and sendmsg in the test interpreter, loopback and
Unix sockets included, proving the guard with probes before tests start (each probe has a
timeout, and any outcome but the guard's own refusal stops the run with exit 3). An xfailed
test and a non-strict xpass count as skips: each fails the run unless its exact node id is in
`allowed_skips`; so does a module-level skip, by its file's node id. Plugins load only from
`[offline] plugins`: a positive `-p NAME` in the arguments stops the run, as does any
installed plugin the list does not name that registers at any point, however it arrives: a
`conftest.py` or test module's `pytest_plugins`, or an import while tests run. Every plugin
the list names must load: the runner reads which plugins pytest holds after configuration and
collection, before any test runs, prints them as `LOADED`, and stops the run (exit 3) if a
listed one is missing, whether a `-p no:NAME` in the arguments or a `conftest.py` blocked it.
A `-p no:NAME` in ini `addopts` cannot disable one, since pytest reads addopts before the
runner's own `-p NAME`. `xdist` is refused,
since its workers would run without the guard. A run that exits 0 counts only when the runner
recorded it finishing every collected test with at least one test run, so `os._exit(0)` or
`pytest.exit(returncode=0)` in a test, or `--collect-only`, fails with `OFFLINE_GUARD`. Name resolution is not blocked. It runs the application's interpreter, which must be Python 3.11 or newer with pytest 7 or newer, with
`-P` so this package is not on the application's path; install this tool in its own
environment, as above, or the suite can still import it. It is not an OS-level sandbox: a
subprocess a test starts inherits the cleared environment but not the socket guard. Use the
container checks (`--network none`) for anything that must hold at the kernel level.

## Development

The suite runs from this directory in its own environment. The container tests use the
local `public.ecr.aws/lambda/python` images 3.11 to 3.14 and pull them when missing; the
workflow runs it on Python 3.11 to 3.14 and requires zero skips.

`verify` runs `self-check` first, so it needs the tool installed from a commit, never from a
working copy (an editable or directory install records no commit). To try an unpushed
change against a consumer, commit it, install it into the consumer's validation venv with
`pip install --force-reinstall --no-deps "git+file:///path/to/nui-python-shared-utils@<sha>#subdirectory=tools/lambda-validation"`
(pip keeps an installed copy of the same version otherwise), commit that sha in the
consumer's `requirements-validation.txt` line, which keeps its `git+https://` URL since only
the commit is compared, and run `self-check` before `verify`.

```bash
python -m venv .venv
.venv/bin/pip install . pytest==8.4.2
.venv/bin/python -m pytest -p no:cacheprovider -c /dev/null --rootdir . tests
```

## What stays out of this tool

Service assertions (handlers, auth, caches, templates), stage values, real deployments and
live readbacks belong to each repository and its release process.
