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

#: The heading the tree block sits under.
_PLACEMENT = "## The placement, and why it is one directory"


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
