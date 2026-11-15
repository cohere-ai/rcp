"""The isolation proof (A1): the library imports nothing of the product it ships beside.

``configkit`` is its own distribution (``cohere-configkit``), so this rule is what keeps it adoptable by a
second consumer: the library imports no module of ``rcp``, ``rcp_core`` or ``rcp_vllm``, uses no relative
import (a relative import is a seam that would not survive the move to another distribution), and reaches
no product module through a string-literal import or a name built from one (``f"rcp.{name}"``,
``"rcp." + name``) either. The first half of this module parses every file of the package with
:mod:`ast`, at every scope, and fails on a finding; the second half proves the same rule at run time, in a
subprocess, and from an extracted copy of the package.

A check whose control cannot fail is not shipped (the contract's §11), so each leg carries a control: a copy
of the package in ``tmp_path`` with one bad line appended must produce exactly one finding.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

#: ``packages/rcp/src/configkit``: the package under test, and the directory a finding is relative to.
PACKAGE = Path(__file__).resolve().parents[2] / "src/configkit"

#: The top-level packages the library must never import: the product it ships in, its core and the serving half.
FORBIDDEN_ROOTS = frozenset({"rcp", "rcp_core", "rcp_vllm"})

#: ``packages/configkit/tests``: the suite that travels with the library, and must run where the product is absent.
SUITE = Path(__file__).resolve().parents[1]


def _consumer_imports(path: Path) -> list[str]:
    """Every module-scope import of a consumer in *path*, as ``file:line: imports <root>`` (function scope is fine).

    A module-scope import is collected before a test can skip: ``pytest packages/configkit/tests`` outside the
    workspace failed at collection while the guarded, function-scope rows skipped as designed.
    """
    findings: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        roots: set[str] = set()
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = {node.module.split(".")[0]}
        findings.extend(f"{path.name}:{node.lineno}: imports {root}" for root in sorted(roots & FORBIDDEN_ROOTS))
    return findings


def test_no_test_module_imports_a_consumer_at_module_scope(tmp_path: Path) -> None:
    """The suite runs in the target repository too: a consumer import is a guarded, function-scope one.

    ``docs/concepts/configkit.md``: "No library test imports a consumer ... so the directory move still carries
    a suite that runs in the target repository". The library's suite imports no consumer at module scope, and the
    control proves the scan can fail: a module-scope import is one finding, a function-scope one none.
    """
    findings = [finding for path in sorted(SUITE.rglob("*.py")) for finding in _consumer_imports(path)]
    assert findings == []

    control = tmp_path / "test_control.py"
    control.write_text(
        "from rcp.calibration._projection import QUERY_ID_SEP\n"
        "import rcp_core\n"
        "def test_guarded():\n"
        "    from rcp.support import urls\n",
        encoding="utf-8",
    )
    assert _consumer_imports(control) == ["test_control.py:1: imports rcp", "test_control.py:2: imports rcp_core"]


def _pydantic_closure() -> frozenset[str]:
    """The import names ``pydantic``'s metadata declares, plus ``pydantic`` itself: the C1 allow-list.

    Computed at test time rather than written down: a fixed list false-fails on a lock that does not resolve a
    dependency (``annotated_types`` arrives with ``from pydantic import BaseModel``, not with a bare
    ``import pydantic``, whose exports are lazy) and false-passes on one that adds a name. A requirement that
    belongs to an extra is skipped: it is not on the import path.
    """
    names = {"pydantic"}
    for requirement in importlib.metadata.requires("pydantic") or ():
        if "extra ==" in requirement:
            continue
        names.add(re.split(r"[ <>=!~;\[]", requirement, maxsplit=1)[0].replace("-", "_").lower())
    return frozenset(names)


#: The functions whose first string argument names a module to load.
_IMPORT_CALLS = frozenset({"__import__", "import_module", "find_spec"})

#: The ``pkgutil`` calls that walk a directory of modules; a string argument naming a product path is a finding.
_WALK_CALLS = frozenset({"walk_packages", "iter_modules", "get_importer"})


def _callee(node: ast.expr) -> str:
    """The name an import call is made by: ``import_module`` for ``importlib.import_module`` and for the bare name."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _owner(node: ast.expr) -> str:
    """The name the receiver of a call is bound to (``pkgutil`` for ``pkgutil.walk_packages``), or ``""``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id
    return ""


def _root(dotted: str) -> str:
    """The top-level part of a dotted module name."""
    return dotted.split(".", 1)[0]


def _literal_prefix(node: ast.expr) -> str | None:
    """The literal start of a module name built at run time: an f-string's first part, a concatenation's left.

    ``import_module(f"rcp.{name}")`` and ``import_module("rcp." + name)`` name the product with no
    ``ast.Constant`` holding the whole module, so the scan reads the part that is literal. A name with no literal
    start (``f"{package}.errors"``) names nothing this scan can decide.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_prefix(node.left)
    return None


def _dynamic_target(node: ast.Call) -> str | None:
    """The product module *node* loads through a string literal, if it does.

    The three forms the package could use are read by their call name: ``__import__("rcp…")``,
    ``importlib.import_module("rcp…")``/``find_spec("rcp…")``, and a ``pkgutil`` walk whose path names a
    product directory. A module name built at run time is read by its literal prefix. A docstring that mentions
    ``rcp`` is not a call and is not a finding.
    """
    name = _callee(node.func)
    if name in _IMPORT_CALLS:
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and _root(first.value) in FORBIDDEN_ROOTS:
            return first.value
        prefix = _literal_prefix(first) if first is not None else None
        if prefix is not None and _root(prefix) in FORBIDDEN_ROOTS:
            return prefix
    if name in _WALK_CALLS and _owner(node.func) == "pkgutil":
        for argument in node.args:
            for literal in _string_constants(argument):
                if any(part in FORBIDDEN_ROOTS for part in Path(literal).parts):
                    return literal
    return None


def _string_constants(node: ast.expr) -> Iterator[str]:
    """Every string constant *node* is, or contains in a list, tuple or set."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        for element in node.elts:
            yield from _string_constants(element)


def _findings(relative: Path, node: ast.AST) -> Iterator[str]:
    """The isolation findings of one syntax-tree node, as ``"<file>:<line>: <target>"``."""
    target: str | None = None
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _root(alias.name) in FORBIDDEN_ROOTS:
                target = alias.name
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            target = "." * node.level + (node.module or "")
        elif node.module is not None and _root(node.module) in FORBIDDEN_ROOTS:
            target = node.module
    elif isinstance(node, ast.Call):
        target = _dynamic_target(node)
    if target is not None:
        yield f"{relative}:{node.lineno}: {target}"


def scan_package(root: Path) -> list[str]:
    """Every isolation finding under *root*, in file order, as ``"<file>:<line>: <target>"``.

    The scan walks the whole syntax tree, so a module-, class-, function- or ``if TYPE_CHECKING``-scoped
    import is found alike. ``<file>`` is relative to *root*, which keeps a control's findings readable.

    Args:
        root: The package directory to scan (the real package, or a copy of it).

    Returns:
        The findings; an empty list is the proof.
    """
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            findings.extend(_findings(path.relative_to(root), node))
    return findings


def _run(code: str, *, cwd: Path, pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run *code* in the project interpreter with *cwd* outside the checkout.

    The ambient ``PYTHONPATH`` is dropped (it could put the checkout's ``src`` first and hide a product import),
    and the interpreter is ``sys.executable``: the venv ``uv run --no-sync`` selected, whose site-packages
    carries the editable install this test's package is imported from.
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=cwd, env=env, check=False)


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The JSON line a probe printed last, after asserting the probe itself succeeded."""
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


#: P2: what an ``import configkit`` put in ``sys.modules``, and the file it imported.
_RUNTIME_PROBE = """
import json, sys
import configkit
product = sorted({name.split(".")[0] for name in sys.modules} & {"rcp", "rcp_core", "rcp_vllm"})
print(json.dumps({"file": configkit.__file__, "product": product}))
"""

#: P4: the same import from an extracted copy, plus the non-stdlib roots the import closed over.
_EXTRACTION_PROBE = """
import json, pathlib, sys
import configkit
allowed = set(sys.stdlib_module_names) | {"configkit", "__main__"}
roots = sorted({name.split(".")[0] for name in sys.modules
                if name.split(".")[0] not in allowed and not name.startswith("_")})
print(json.dumps({"file": str(pathlib.Path(configkit.__file__ or "")), "roots": roots}))
"""


def test_the_package_is_where_the_scan_looks() -> None:
    """A scan of a missing directory finds nothing, so the directory is asserted before it is trusted."""
    assert PACKAGE.is_dir(), PACKAGE
    assert (PACKAGE / "__init__.py").is_file(), PACKAGE


def test_no_product_import_at_any_scope() -> None:
    """A1: every file of the package imports the standard library and ``pydantic`` only."""
    assert scan_package(PACKAGE) == []


@pytest.mark.parametrize(
    ("injected", "target"),
    [
        ("import rcp", "rcp"),
        ("from rcp.errors import Fault", "rcp.errors"),
        ("from rcp_core.schemas import accepted_schema_ids", "rcp_core.schemas"),
        ("from . import faults", "."),
        ('import importlib\nimportlib.import_module("rcp.errors")', "rcp.errors"),
        ('import importlib\nname = "errors"\nimportlib.import_module(f"rcp.{name}")', "rcp."),
        ('import importlib\nname = "errors"\nimportlib.import_module("rcp." + name)', "rcp."),
        ('import pkgutil\npkgutil.walk_packages(["vendor/rcp"])', "vendor/rcp"),
    ],
)
def test_the_scan_finds_an_injected_import(tmp_path: Path, injected: str, target: str) -> None:
    """The control: one bad line appended to a copy of the package produces exactly one finding."""
    copy = tmp_path / "configkit"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    init = copy / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + f"\n{injected}\n", encoding="utf-8")
    findings = scan_package(copy)
    assert len(findings) == 1, findings
    assert findings[0].endswith(f": {target}"), findings[0]


def test_importing_the_library_loads_no_product_module(tmp_path: Path) -> None:
    """P2: ``import configkit`` loads no ``rcp``, ``rcp_core`` or ``rcp_vllm`` module, from outside the checkout."""
    payload = _payload(_run(_RUNTIME_PROBE, cwd=tmp_path))
    assert payload["product"] == []
    assert Path(str(payload["file"])).resolve() == (PACKAGE / "__init__.py").resolve()


def test_a_copy_of_the_package_imports_with_the_pydantic_closure(tmp_path: Path) -> None:
    """P4: the package extracted to another directory imports with no edit, and imports nothing else.

    The copy is ``src/configkit``, not the distribution directory: a namespace package would import with
    ``__file__ is None`` and prove nothing, so the path is asserted to be the copy's own ``__init__.py``.
    """
    copy = tmp_path / "configkit"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    payload = _payload(_run(_EXTRACTION_PROBE, cwd=tmp_path, pythonpath=tmp_path))
    extracted = Path(str(payload["file"]))
    assert extracted.name == "__init__.py", payload["file"]
    assert extracted.parent == copy, payload["file"]
    assert set(payload["roots"]) <= _pydantic_closure(), payload["roots"]
