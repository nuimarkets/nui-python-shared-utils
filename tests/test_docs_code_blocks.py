"""Every ``python`` fenced block in the repository's markdown parses as Python.

Readers copy these blocks into their code, so a block written as a call with annotation
syntax inside it (``Client(name: str = None)``) fails the moment it is pasted. Signature
references stay parseable by being written as ``class``/``def`` stubs.
"""

import ast
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_FENCE = re.compile(r"^\s*```(?:python|py)\s*$")
CLOSE_FENCE = re.compile(r"^\s*```\s*$")


def _markdown_files():
    return sorted([*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md")])


def _leading_whitespace(line):
    return line[: len(line) - len(line.lstrip(" \t"))]


def _strip_fence_indent(line, fence_indent):
    """Remove the fence's own indentation (a fence nested in a list item), keeping any beyond it."""
    matched = 0
    for fence_char, line_char in zip(fence_indent, line):
        if fence_char != line_char:
            break
        matched += 1
    return line[matched:]


def _python_blocks(path):
    """Yield (opening fence line number, block source) for each python block in ``path``."""
    block = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if block is None:
            if OPEN_FENCE.match(line):
                block = (number, _leading_whitespace(line), [])
        elif CLOSE_FENCE.match(line):
            yield block[0], "\n".join(block[2])
            block = None
        else:
            block[2].append(_strip_fence_indent(line, block[1]))
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


@pytest.mark.parametrize(
    "markdown, parses",
    [
        pytest.param("1. Step\n\n   ```python\n   x = 1\n   if x:\n       y = 2\n   ```\n", True, id="list-fence"),
        pytest.param("\t```python\n\tx = 1\n\n\tif x:\n\t\ty = 2\n\t```\n", True, id="tab-fence"),
        pytest.param("```python\n    x = 1\n```\n", False, id="excess-indent-top-level"),
        pytest.param("   ```python\n       x = 1\n   ```\n", False, id="excess-indent-list-fence"),
        pytest.param("   ```python\n   x = 1\n     y = 2\n   ```\n", False, id="uneven-indent"),
    ],
)
def test_fence_indentation_is_stripped_and_nothing_more(tmp_path, markdown, parses):
    path = tmp_path / "doc.md"
    path.write_text(markdown, encoding="utf-8")
    [(_, source)] = list(_python_blocks(path))
    if parses:
        ast.parse(source)
    else:
        with pytest.raises(SyntaxError):
            ast.parse(source)
