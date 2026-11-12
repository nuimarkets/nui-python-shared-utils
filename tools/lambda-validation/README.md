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
`requirements-validation.txt` and `.venv-validation` out of the deployment package. Every
check fails a ZIP that carries any of them (`TOOL_IN_ARTIFACT`).

## Commands

Every failure exits 1 and prints `CODE: detail` on stderr. Codes are listed in
`nui_lambda_validation/errors.py`; negative controls assert the code, not the text.

| Command | What it does |
| --- | --- |
| `verify --config lambda-validation.toml --output DIR` | Builds every stage from `git archive HEAD` with synthetic configuration, then per stage: secret scan, `check-zip`, extraction proof, the adapter's smoke script in the Lambda image, `pip check` in the image; the first stage also runs `negatives`. The adapter, its lock files and its smoke script are read from the committed tree, never the working copy, and the adapter must sit at the repository's top level. Records the digests of the runtime image and of every `build_images` entry. Copies each compiled template out for the repository's own assertions. `DIR` must be new and outside the checkout |
| `check-zip ZIP --config ...` | Member safety, secrets, exclusions, required paths, size, native ABI/ELF, dependency pins and closure, test-only leaks, optional major drift |
| `negatives ZIP --config ... --output DIR` | Mutates the ZIP once per standard defect and once per configured exclusion rule; fails unless each mutant is rejected with its intended code |
| `extract ZIP DIR` | Extracts and proves the files on disk are exactly the members, byte for byte |
| `offline-pytest --config ... --python APP_PYTHON [-- ARGS]` | Runs the application's suite in its own interpreter under the offline policy below |
| `secret-scan DIR` | Deploy-time backstop: refuses a package directory with no ZIP, an unreadable ZIP, or a secret file |
| `check-backstop SCRIPT` | Fails when a shell backstop's `grep -E` pattern differs from the canonical one |
| `self-check --requirement FILE` | Fails unless the installed tool came from the pinned commit |

## Adapter: `lambda-validation.toml`

Every section, key and value type below is checked when the file is read, nested tables included: an unknown or misspelled key fails as `ADAPTER_CONFIG` rather than silently dropping the check it configures. `verify` also needs `[build]` `stages` (non-empty), `config_path` and `config_text`, and `[container]` `image` and `smoke`.

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
# test_lock = "requirements-test.txt"
# [dependencies.test_only]        # test-only distribution -> top-level names it installs
# pytest = ["pytest", "_pytest", "py.py"]

[negatives.not_applicable]        # mutant name -> reason, when a standard mutant cannot apply
# metadata-removed = "..."

[offline]
plugins = ["pytest_mock", "pytest_cov"]   # autoload is off; list every plugin the suite uses
args = ["tests"]
allowed_skips = []                         # exact node ids; any other skip fails the run
passthrough_env = []
[offline.not_loaded]                       # installed pytest11 plugins deliberately not loaded
# anyio = "suite has no async tests"
[offline.env]

[build]
stages = ["dev", "training", "prod"]
build_images = ["public.ecr.aws/sam/build-python3.12:latest-x86_64"]  # digests recorded; must be local
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
carries `pins.json` (exact mode) and `distributions.json`. Shared checks come first, then
the service's own assertions:

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
config files, the adapter's `passthrough_env` and `[offline.env]`), disables plugin
autoload, and blocks outbound TCP and UDP in the test interpreter, proving the guard with
probes before tests start. It runs the application's interpreter with `-P` so this package
is not on the application's path. It is not an OS-level sandbox: a subprocess a test
starts inherits the cleared environment but not the socket guard. Use the container checks
(`--network none`) for anything that must hold at the kernel level.

## Development

The suite runs from this directory in its own environment. The container tests use the
local `public.ecr.aws/lambda/python:3.12` and `:3.11` images and pull them when missing;
the workflow requires zero skips.

```bash
python -m venv .venv
.venv/bin/pip install . pytest==8.4.2
.venv/bin/python -m pytest -p no:cacheprovider -c /dev/null --rootdir . tests
```

## What stays out of this tool

Service assertions (handlers, auth, caches, templates), stage values, real deployments and
live readbacks belong to each repository and its release process.
