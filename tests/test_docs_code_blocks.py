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
FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
PYTHON_LANGUAGES = {"python", "py"}


def _markdown_files():
    return sorted([*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md")])


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
    # Every fenced block is tracked, python or not, so a fence-like line inside another
    # block is that block's content. A block closes on a bare fence of its own character
    # at least as long as its opener (CommonMark).
    block = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fence = FENCE.match(line)
        if block is None:
            if fence and not (fence["marker"][0] == "`" and "`" in fence["info"]):
                info = fence["info"].split()
                is_python = bool(info) and info[0] in PYTHON_LANGUAGES
                block = (number, fence["indent"], fence["marker"], is_python, [])
        elif (
            fence
            and not fence["info"].strip()
            and fence["marker"][0] == block[2][0]
            and len(fence["marker"]) >= len(block[2])
        ):
            if block[3]:
                yield block[0], "\n".join(block[4])
            block = None
        elif block[3]:
            block[4].append(_strip_fence_indent(line, block[1]))
    # CommonMark closes an open block at the end of the document. An unclosed python block
    # is still refused: it is almost always a missing closer swallowing the prose after it.
    if block is not None and block[3]:
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


@pytest.mark.parametrize(
    "markdown, expected",
    [
        pytest.param("~~~python\nx = 1\n~~~\n", ["x = 1"], id="tilde-fence"),
        pytest.param("````python\nx = 1\n````\n", ["x = 1"], id="long-backtick-fence"),
        pytest.param("```python\nx = 1\n````\n", ["x = 1"], id="longer-closing-fence"),
        pytest.param(
            "````text\n```python\nbad(:\n```\n````\n```python\ny = 2\n```\n",
            ["y = 2"],
            id="python-fence-inside-text-fence",
        ),
        pytest.param(
            "```markdown\n```python\nbad(:\n```\n```python\ny = 2\n```\n", ["y = 2"], id="python-info-line-is-content"
        ),
        pytest.param("```python\nx = 1\n```\n```text\nunclosed\n", ["x = 1"], id="unclosed-text-fence-at-eof"),
    ],
)
def test_fences_follow_commonmark(tmp_path, markdown, expected):
    path = tmp_path / "doc.md"
    path.write_text(markdown, encoding="utf-8")
    assert [source for _, source in _python_blocks(path)] == expected
