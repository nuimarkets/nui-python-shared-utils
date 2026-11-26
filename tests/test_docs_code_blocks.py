"""Every ``python`` fenced block in the repository's markdown parses as Python.

Readers copy these blocks into their code, so a block written as a call with annotation
syntax inside it (``Client(name: str = None)``) fails the moment it is pasted. Signature
references stay parseable by being written as ``class``/``def`` stubs.
"""

import ast
from pathlib import Path
import re
import textwrap

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_FENCE = re.compile(r"^\s*```(?:python|py)\s*$")
CLOSE_FENCE = re.compile(r"^\s*```\s*$")
# Local working docs kept at the repo root are untracked, so CI never sees them.
LOCAL_DOC_PREFIXES = ("PLAN_", "HANDOVER_", "TRACKING_")


def _markdown_files():
    root_docs = [path for path in REPO_ROOT.glob("*.md") if not path.name.startswith(LOCAL_DOC_PREFIXES)]
    return sorted([*root_docs, *(REPO_ROOT / "docs").rglob("*.md")])


def _python_blocks(path):
    """Yield (opening fence line number, block source) for each python block in ``path``."""
    block = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if block is None:
            if OPEN_FENCE.match(line):
                block = (number, [])
        elif CLOSE_FENCE.match(line):
            # A fence nested in a list item is indented with its content.
            yield block[0], textwrap.dedent("\n".join(block[1]))
            block = None
        else:
            block[1].append(line)
    if block is not None:
        raise AssertionError(f"{path.relative_to(REPO_ROOT)}:{block[0]}: python fence is never closed")


def _cases():
    for path in _markdown_files():
        for number, source in _python_blocks(path):
            yield pytest.param(source, id=f"{path.relative_to(REPO_ROOT)}:{number}")


CASES = list(_cases())


def test_markdown_has_python_blocks_to_check():
    # Guards the collection itself: a path or fence regex that matched nothing would
    # otherwise pass every parametrized case vacuously.
    assert len(CASES) > 100


@pytest.mark.parametrize("source", CASES)
def test_python_block_parses(source):
    ast.parse(source)
