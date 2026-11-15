"""The page that describes the library names the modules the library has (L-1).

``docs/concepts/configkit.md`` is the design argument a consumer reads before adopting the library, and its module
tree is the page's one canonical list of what the package contains. It listed ``optional.py`` and ``log.py``, two
modules the fold-in removed -- the rules live in ``faults.require`` and ``ports.logger`` -- so a reader was sent to
a file that does not exist. This test keeps the tree equal to the directory.
"""

from __future__ import annotations

import re
from pathlib import Path

#: The repository root: ``packages/configkit/tests/configkit`` -> four parents up.
REPOSITORY = Path(__file__).resolve().parents[4]

#: The page whose module tree this test reads.
PAGE = REPOSITORY / "docs/concepts/configkit.md"

#: ``packages/configkit/src/configkit``: the package the tree must describe.
PACKAGE = Path(__file__).resolve().parents[2] / "src/configkit"

#: ``packages/configkit/tests/configkit``: the suite the page's test table must describe.
SUITE = Path(__file__).resolve().parents[1] / "configkit"

#: The heading the tree block sits under.
_PLACEMENT = "## The placement, and why it is one directory"

#: The heading the test table sits under.
_TESTS = "## Tests the library carries"


def _modules_the_tree_names() -> set[str]:
    """Every ``*.py`` name the page's module tree lists under ``src/configkit/``."""
    page = PAGE.read_text(encoding="utf-8")
    block = page.split(_PLACEMENT, 1)[1].split("```", 2)[1]
    named: set[str] = set()
    inside = False
    for line in block.splitlines():
        if "src/configkit/" in line:
            inside = True
        elif inside and line.strip().startswith("tests/"):
            break
        if inside:
            named.update(re.findall(r"\b\w+\.py\b", line))
    return named


def test_the_module_tree_names_exactly_the_modules_the_package_has() -> None:
    """L-1: the tree listed ``optional.py`` and ``log.py``, which no longer exist."""
    named = _modules_the_tree_names()
    assert "__init__.py" in named, named  # the control: the block was parsed, so the comparison is not vacuous
    assert named == {path.name for path in PACKAGE.glob("*.py")}


def _tests_the_table_names() -> set[str]:
    """Every ``test_*.py`` name the page's test table lists."""
    page = PAGE.read_text(encoding="utf-8")
    block = page.split(_TESTS, 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"`tests/(test_\w+\.py)`", block))


def test_the_test_table_names_exactly_the_test_modules_the_suite_has() -> None:
    """The table listed ``test_schemas.py`` (renamed to ``test_configkit_schemas.py``) and two modules the tree
    does not have (``test_seams.py``, ``test_render.py``), so a reader was sent to files that are gone."""
    named = _tests_the_table_names()
    assert "test_roles.py" in named  # the control: the block was parsed, so the comparison is not vacuous
    assert named == {path.name for path in SUITE.glob("test_*.py")}
