"""Every check ``audit``, ``scan`` and ``pure`` ship, each with its own failing control.

The library's checks are the half of the contract a test can hold: a rule nobody calls is a rule nobody keeps.
Each check below gets a ``tmp_path`` tree holding **exactly one** injected defect, and the same tree with the
defect removed. The defect must produce exactly one finding, and the finding must name where the defect is --
``<path>:<line>`` for a source check, the field path for a model-graph check -- because a finding a reader cannot
locate is a finding a reader cannot fix. The control must produce none: a check whose control does not go red is
not shipped (the contract's §11).

Two kinds of fact, two entry points. :func:`~configkit.audit.audit` walks the model graph from a root class, so
its findings name a field path; :func:`~configkit.audit.scan` parses source with :mod:`ast`, so its findings name
``path:line`` -- and ``scan(roots=...)`` delegates to ``audit``, so there is one implementation of each rule.

The trees are never executed except where a model graph is the thing under test (an ``A`` check imports its
module, because a model class has to exist to be walked); every ``S`` tree is only parsed, which is what
:func:`~configkit.audit.scan` does.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from configkit.audit import audit, pure, scan

#: The package the library's own sources live in: criterion 5's self-scan target, and the ``local`` package an
#: underscore import inside it is not a seam across.
PACKAGE = Path(__file__).resolve().parents[2] / "src/configkit"

#: The marker a defect line carries, so the expected ``path:line`` is computed from the file rather than written
#: down: a source edit moves the line and the expectation with it.
MARKER = "# DEFECT"


@dataclass(frozen=True)
class Case:
    """One check: how to build its tree and run it, and where the finding must point.

    Attributes:
        check: The check's id (``"A1"``, ``"S5"``, ...), which every finding it produces is prefixed with.
        run: ``(root, defect) -> findings``: build the tree under *root* and run the check over it.
        anchor: ``(root) -> text``: the ``path:line`` or field path the defect's finding must contain.
    """

    check: str
    run: Callable[[Path, bool], list[str]]
    anchor: Callable[[Path], str]


def _write(root: Path, files: Mapping[str, str]) -> Path:
    """Write *files* under *root*, creating directories, and return *root*."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _marked(root: Path, name: str) -> str:
    """``<root>/<name>:<line>`` for the line carrying :data:`MARKER`."""
    path = root / name
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if MARKER in line:
            return f"{path}:{number}"
    raise AssertionError(f"{path} carries no {MARKER} marker")


@contextmanager
def _importable(directory: Path) -> Iterator[None]:
    """Put *directory* on ``sys.path`` for one import, so a tree's modules can import each other."""
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        sys.path.remove(str(directory))


def _imported(root: Path, source: str, *, name: str) -> ModuleType:
    """Write *source* to ``<root>/<name>.py`` and import it, registered in ``sys.modules`` as its own name.

    The registration is what A8 and A5 read: both look at the module a record is defined in, which is
    ``sys.modules[cls.__module__]``.
    """
    path = _write(root, {f"{name}.py": source}) / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with _importable(root):
        spec.loader.exec_module(module)
    return module


def _audited(root: Path, source: str, *, name: str) -> list[str]:
    """Import *source* and audit the roots it declares (``ROOTS``), which is how every A tree is checked."""
    module = _imported(root, source, name=name)
    return audit(*module.ROOTS)


# ---------------------------------------------------------------------------------------------------------------
# A1 -- a field with no role
# ---------------------------------------------------------------------------------------------------------------

_A1 = """\
from typing import ClassVar

from pydantic import BaseModel


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content"}
    kept: str
    flag: str = "x"


ROOTS = (Leaf,)
"""

_A1_CLEAN = _A1.replace('{"kept": "content"}', '{"kept": "content", "flag": "runtime"}')


def _a1(root: Path, defect: bool) -> list[str]:
    """A1: ``flag`` is a field and no declaration names its role."""
    return _audited(root, _A1 if defect else _A1_CLEAN, name="probe_a1")


# ---------------------------------------------------------------------------------------------------------------
# A2 -- a role on a field that does not exist
# ---------------------------------------------------------------------------------------------------------------

_A2 = """\
from typing import ClassVar

from pydantic import BaseModel


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content", "gone": "runtime"}
    kept: str


ROOTS = (Leaf,)
"""

_A2_CLEAN = _A2.replace('{"kept": "content", "gone": "runtime"}', '{"kept": "content"}')


def _a2(root: Path, defect: bool) -> list[str]:
    """A2: the declaration names ``gone``, and the model has no such field."""
    return _audited(root, _A2 if defect else _A2_CLEAN, name="probe_a2")


# ---------------------------------------------------------------------------------------------------------------
# A3 -- a CONTENT field with a default
# ---------------------------------------------------------------------------------------------------------------

_A3 = """\
from typing import ClassVar

from pydantic import BaseModel


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content"}
    kept: str = "x"


ROOTS = (Leaf,)
"""

_A3_CLEAN = _A3.replace('kept: str = "x"', "kept: str")


def _a3(root: Path, defect: bool) -> list[str]:
    """A3: a CONTENT field is derived, never defaulted (rcp-28)."""
    return _audited(root, _A3 if defect else _A3_CLEAN, name="probe_a3")


# ---------------------------------------------------------------------------------------------------------------
# A4 -- a PATH field the resolver misses
# ---------------------------------------------------------------------------------------------------------------

_A4 = """\
from typing import ClassVar

from pydantic import BaseModel


class Inner(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"where": "path"}
    where: str


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"inner": "path"}
    inner: Inner


ROOTS = (Leaf,)
"""

_A4_CLEAN = _A4.replace('{"inner": "path"}', '{"inner": "runtime"}')


def _a4(root: Path, defect: bool) -> list[str]:
    """A4: PATH on a model-valued field -- the enumeration names the field, and the resolver then walks the model
    as free-form data, so its own leaves are never resolved as paths."""
    return _audited(root, _A4 if defect else _A4_CLEAN, name="probe_a4")


# ---------------------------------------------------------------------------------------------------------------
# A5 -- a code with no fault
# ---------------------------------------------------------------------------------------------------------------

_A5 = """\
from typing import ClassVar

from pydantic import BaseModel

from configkit.faults import Fault, register_fault_codes


class TreeFault(Fault):
    code = "tree_error"


FAULT_CODES = register_fault_codes(((TreeFault, "tree_error", "probe"), (KeyError, "orphan_code", "probe")))


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content"}
    kept: str


ROOTS = (Leaf,)
"""

_A5_CLEAN = _A5.replace(
    '((TreeFault, "tree_error", "probe"), (KeyError, "orphan_code", "probe"))', '((TreeFault, "tree_error", "probe"),)'
)


def _a5(root: Path, defect: bool) -> list[str]:
    """A5: the module's fault table declares a code no ``Fault`` subclass raises -- a dead exit code."""
    return _audited(root, _A5 if defect else _A5_CLEAN, name="probe_a5")


# ---------------------------------------------------------------------------------------------------------------
# A6 -- a schema field with a default
# ---------------------------------------------------------------------------------------------------------------

_A6 = """\
from typing import ClassVar

from configkit.schemas import SchemaTagged


class Record(SchemaTagged):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"schema_id": "runtime"}
    schema_id: str = "probe.record.v1"


ROOTS = (Record,)
"""

_A6_CLEAN = _A6.replace('schema_id: str = "probe.record.v1"', "schema_id: str")


def _a6(root: Path, defect: bool) -> list[str]:
    """A6: a schema id is derived at the write site, never defaulted."""
    return _audited(root, _A6 if defect else _A6_CLEAN, name="probe_a6")


# ---------------------------------------------------------------------------------------------------------------
# A7 -- a warning channel with no declared code set
# ---------------------------------------------------------------------------------------------------------------

_A7 = """\
from typing import ClassVar

from pydantic import BaseModel


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content"}
    WARNING_CODES: ClassVar[tuple[str, ...]] = ()
    kept: str


ROOTS = (Leaf,)
"""

_A7_CLEAN = _A7.replace(
    "WARNING_CODES: ClassVar[tuple[str, ...]] = ()",
    'WARNING_CODES: ClassVar[tuple[str, ...]] = ("probe_notice",)',
)


def _a7(root: Path, defect: bool) -> list[str]:
    """A7: the record declares the warning channel and a code set that can emit nothing (rcp-31)."""
    return _audited(root, _A7 if defect else _A7_CLEAN, name="probe_a7")


# ---------------------------------------------------------------------------------------------------------------
# A8 -- a record type defined in a service module
# ---------------------------------------------------------------------------------------------------------------

_A8 = """\
import probe_service

from typing import ClassVar

from pydantic import BaseModel


class Leaf(BaseModel):
    IDENTITY_ROLES: ClassVar[dict[str, str]] = {"kept": "content"}
    kept: str


ROOTS = (Leaf,)
"""

_A8_CLEAN = _A8.replace("import probe_service\n\n", "")


def _a8(root: Path, defect: bool) -> list[str]:
    """A8: a record type comes from a types module, not a service one (rcp-50)."""
    _write(root, {"probe_service.py": "VALUE = 1\n"})
    return _audited(root, _A8 if defect else _A8_CLEAN, name="probe_a8")


# ---------------------------------------------------------------------------------------------------------------
# S1 -- a repository path literal
# ---------------------------------------------------------------------------------------------------------------

_S1 = """\
from pathlib import Path

_DATA = Path("/root/work/repos/acme/data")  # DEFECT
"""

_S1_CLEAN = """\
from pathlib import Path

_DATA = Path("data")
"""


def _s1(root: Path, defect: bool) -> list[str]:
    """S1: a repository path in the source is a path no other host has (rcp-8)."""
    return scan(_write(root, {"leaf.py": _S1 if defect else _S1_CLEAN}))


# ---------------------------------------------------------------------------------------------------------------
# S2 -- a command group named after a repository
# ---------------------------------------------------------------------------------------------------------------

_S2 = """\
from configkit.commands import Command

_COMMANDS = (Command(name="acme"),)  # DEFECT
"""

_S2_CLEAN = """\
from configkit.commands import Command

_COMMANDS = (Command(name="run"),)
"""


def _s2(root: Path, defect: bool) -> list[str]:
    """S2: the library ships no command group, and never one named after a repository (rcp-9)."""
    return scan(_write(root, {"leaf.py": _S2 if defect else _S2_CLEAN}), repository_names=("acme",))


# ---------------------------------------------------------------------------------------------------------------
# S3 -- a shipped data pin or benchmark name
# ---------------------------------------------------------------------------------------------------------------

_S3 = """\
_DATASET = "beir:scifact"  # DEFECT
"""

_S3_CLEAN = """\
_DATASET = "scifact"
"""


def _s3(root: Path, defect: bool) -> list[str]:
    """S3: no benchmark name and no data pin is shipped in the library (rcp-48)."""
    return scan(_write(root, {"leaf.py": _S3 if defect else _S3_CLEAN}))


# ---------------------------------------------------------------------------------------------------------------
# S4 -- an import of a consumer
# ---------------------------------------------------------------------------------------------------------------

_S4 = """\
import acme_consumer  # DEFECT
"""

_S4_CLEAN = """\
import pathlib
"""


def _s4(root: Path, defect: bool) -> list[str]:
    """S4: the library imports no consumer, at any scope (C2, a second time at the text level)."""
    return scan(_write(root, {"leaf.py": _S4 if defect else _S4_CLEAN}), consumer_imports=("acme_consumer",))


# ---------------------------------------------------------------------------------------------------------------
# S5 -- an underscore seam
# ---------------------------------------------------------------------------------------------------------------

_S5 = """\
from configkit.document import _YAML_HINT  # DEFECT
"""

_S5_CLEAN = """\
from configkit.document import Document
"""


def _s5(root: Path, defect: bool) -> list[str]:
    """S5: a seam between two packages is a public name, never an underscore path (C5, rcp-44)."""
    return scan(_write(root, {"leaf.py": _S5 if defect else _S5_CLEAN}), seam_prefix="_")


# ---------------------------------------------------------------------------------------------------------------
# S6 -- an upward import
# ---------------------------------------------------------------------------------------------------------------

_S6 = """\
_LOW = 1

from high.other import VALUE  # DEFECT
"""

_S6_CLEAN = """\
_LOW = 1
"""


def _s6(root: Path, defect: bool) -> list[str]:
    """S6: an import that points up the layer order is a cycle the layering rule forbids (ee-S25/X2)."""
    other = '__all__ = ["VALUE"]\n\nVALUE = 1\n'
    _write(root, {"low/leaf.py": _S6 if defect else _S6_CLEAN, "high/other.py": other})
    return scan(root, layers=("low", "high"))


# ---------------------------------------------------------------------------------------------------------------
# S7 -- a module whose __all__ misses a public name
# ---------------------------------------------------------------------------------------------------------------

_S7 = """\
__all__ = ["kept"]


def kept() -> str:
    return "kept"


def leaked() -> str:  # DEFECT
    return "leaked"
"""

_S7_CLEAN = _S7.replace('__all__ = ["kept"]', '__all__ = ["kept", "leaked"]')


def _s7(root: Path, defect: bool) -> list[str]:
    """S7: ``__all__`` is the module's published surface, and a public name it omits is not published (et-32)."""
    return scan(_write(root, {"leaf.py": _S7 if defect else _S7_CLEAN}))


# ---------------------------------------------------------------------------------------------------------------
# S8 -- a call site that still writes a removable schema spelling
# ---------------------------------------------------------------------------------------------------------------

_S8 = """\
_SPELLING = "rcp-ndcg.result.v1"  # DEFECT
"""

_S8_CLEAN = """\
_SPELLING = "rcp.result.v1"
"""


def _s8(root: Path, defect: bool) -> list[str]:
    """S8: the alias list says which spelling is removable, and a site still writing it is the cleanup (rcp-30)."""
    return scan(_write(root, {"leaf.py": _S8 if defect else _S8_CLEAN}), removable=("rcp-ndcg.result.v1",))


# ---------------------------------------------------------------------------------------------------------------
# S9 -- a second data gate
# ---------------------------------------------------------------------------------------------------------------

_S9 = """\
from pathlib import Path

_TEXT = Path("config.yaml").read_text(encoding="utf-8")  # DEFECT
"""

_S9_CLEAN = """\
_TEXT = "config: {}"
"""


def test_a_file_reader_is_allowed_by_its_path_not_its_name(tmp_path: Path) -> None:
    """L12: the allow-list matched ``path.name``, so any future ``document.py`` became a silent second data gate."""
    elsewhere = _write(tmp_path / "other", {"document.py": _S9})
    found = scan(elsewhere)
    assert len(found) == 1, found
    assert found[0].startswith("S9 "), found[0]
    allowed = _write(tmp_path / "lib", {"configkit/document.py": _S9})
    assert scan(allowed) == []


def _s9(root: Path, defect: bool) -> list[str]:
    """S9: a file read outside the one document reader and the one byte seam is a second data gate (et-23)."""
    return scan(_write(root, {"loader.py": _S9 if defect else _S9_CLEAN}))


# ---------------------------------------------------------------------------------------------------------------
# S10 -- a call site that declares **options: Any
# ---------------------------------------------------------------------------------------------------------------

_S10 = """\
from typing import Any

__all__ = ["load_dataset"]


def load_dataset(path: str, **options: Any) -> None:  # DEFECT
    del path, options
"""

_S10_CLEAN = """\
from typing import Any

__all__ = ["load_dataset"]


def load_dataset(path: str, options: dict[str, Any] | None = None) -> None:
    del path, options
"""


def _s10(root: Path, defect: bool) -> list[str]:
    """S10: a reader's or writer's options are a declared model, never ``**options: Any`` (rcp-24)."""
    return scan(_write(root, {"loader.py": _S10 if defect else _S10_CLEAN}))


#: The eighteen cases: the eight model-graph checks, the ten source checks. Two of the source checks share a tree
#: shape (a single ``leaf.py``), which is what makes the card's "seventeen cases, sixteen trees" a counting
#: statement rather than a rule: each case owns its own tree here.
CASES: tuple[Case, ...] = (
    Case("A1", _a1, lambda root: "A1 Leaf.flag"),
    Case("A2", _a2, lambda root: "A2 Leaf.gone"),
    Case("A3", _a3, lambda root: "A3 Leaf.kept"),
    Case("A4", _a4, lambda root: "A4 Leaf.inner"),
    Case("A5", _a5, lambda root: "A5 probe_a5.orphan_code"),
    Case("A6", _a6, lambda root: "A6 Record.schema_id"),
    Case("A7", _a7, lambda root: "A7 Leaf"),
    Case("A8", _a8, lambda root: "A8 Leaf"),
    Case("S1", _s1, lambda root: _marked(root, "leaf.py")),
    Case("S2", _s2, lambda root: _marked(root, "leaf.py")),
    Case("S3", _s3, lambda root: _marked(root, "leaf.py")),
    Case("S4", _s4, lambda root: _marked(root, "leaf.py")),
    Case("S5", _s5, lambda root: _marked(root, "leaf.py")),
    Case("S6", _s6, lambda root: _marked(root, "low/leaf.py")),
    Case("S7", _s7, lambda root: _marked(root, "leaf.py")),
    Case("S8", _s8, lambda root: _marked(root, "leaf.py")),
    Case("S9", _s9, lambda root: _marked(root, "loader.py")),
    Case("S10", _s10, lambda root: _marked(root, "loader.py")),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.check)
def test_a_check_fires_on_its_one_defect_and_names_where_it_is(case: Case, tmp_path: Path) -> None:
    """The defect: exactly one finding, and it names the field path or the ``path:line`` the defect sits on."""
    root = tmp_path / "defect"
    found = case.run(root, True)
    assert len(found) == 1, found
    assert case.anchor(root) in found[0], found[0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.check)
def test_the_same_tree_without_the_defect_is_clean(case: Case, tmp_path: Path) -> None:
    """The control: the same tree with the defect removed produces no finding at all."""
    assert case.run(tmp_path / "control", False) == []


# ---------------------------------------------------------------------------------------------------------------
# pure: one validator, under an audit hook that refuses open, socket.* and subprocess.* (rcp-20)
# ---------------------------------------------------------------------------------------------------------------


def test_pure_records_an_open(tmp_path: Path) -> None:
    """Validation that opens a file is not pure, and the finding names the event and the path."""
    target = tmp_path / "document.yaml"

    def validator() -> str:
        with target.open("w", encoding="utf-8") as handle:
            handle.write("a: 1")
        return "read"

    findings = pure(validator)
    assert len(findings) >= 1, findings
    assert findings[0].startswith("open"), findings[0]
    assert str(target) in findings[0]


def test_pure_records_a_socket_and_a_subprocess() -> None:
    """A validator that reaches the network or starts a process is not pure either."""
    import socket

    network = pure(socket.socket)
    assert network and network[0].startswith("socket."), network
    process = pure(lambda: subprocess.run([sys.executable, "-c", "pass"], check=False))
    assert process and process[0].startswith("subprocess."), process


def test_pure_is_clean_for_a_validator_that_touches_nothing() -> None:
    """The control: a validator over its argument reads no file, no socket and no process."""
    assert pure(lambda: 1 + 1) == []


def test_pure_leaves_the_process_alone_afterwards(tmp_path: Path) -> None:
    """The hook is installed once and records only while a ``pure`` call is running, so a later open is fine."""
    assert pure(lambda: 1) == []
    (tmp_path / "later.txt").write_text("x", encoding="utf-8")
    assert (tmp_path / "later.txt").read_text(encoding="utf-8") == "x"


# ---------------------------------------------------------------------------------------------------------------
# the entry points themselves
# ---------------------------------------------------------------------------------------------------------------


def test_scan_with_roots_runs_the_model_checks_too(tmp_path: Path) -> None:
    """``scan(roots=...)`` delegates to ``audit``: one implementation, two entry points."""
    found = _a3(tmp_path / "tree", True)
    assert len(found) == 1
    root = tmp_path / "tree"
    module = sys.modules["probe_a3"]
    scanned = scan(root, roots=module.ROOTS)
    assert found[0] in scanned
    assert any(finding.startswith("A3") for finding in scanned)


def test_scan_finds_nothing_in_a_clean_tree(tmp_path: Path) -> None:
    """A tree with no defect produces no finding, so the checks are not vacuous."""
    root = _write(tmp_path / "clean", {"leaf.py": '__all__ = ["value"]\n\nvalue = 1\n'})
    assert scan(root) == []


def test_scan_is_deterministic(tmp_path: Path) -> None:
    """Two runs over one tree give the same list, so a caller can compare findings across runs."""
    root = _write(tmp_path / "tree", {"b.py": _S1, "a.py": _S1})
    assert scan(root) == scan(root)


#: The rule set the library's own source is held to: the three consumer roots the isolation rule forbids, the
#: repository names no command group may take, a benchmark name and a removable schema spelling it must not ship,
#: and the package's one layer (a flat package has no upward import to point).
_LIBRARY_RULES: dict[str, Any] = {
    "consumer_imports": ("rcp", "rcp_core", "rcp_vllm"),
    "repository_names": ("rcp", "rcp-ndcg", "rcp-core", "emb-train", "emb-eval"),
    "data_pins": ("scifact", "msmarco"),
    "layers": ("configkit",),
    "removable": ("rcp-ndcg.result-record.v1",),
}


def test_the_library_scans_itself_clean() -> None:
    """Criterion 5: every check runs over the library's own source, and none fires.

    L11: the five rule sets used to be the defaults -- empty -- so S2, S3, S4, S6 and S8 returned before reading a
    line and half the checks were never run by the test that claims they all are.
    """
    assert scan(PACKAGE, seam_prefix="_", **_LIBRARY_RULES) == []


def test_the_library_rule_set_is_live(tmp_path: Path) -> None:
    """The control for the scan above: the same parameters find a planted defect, so the clean result is real."""
    defect = 'from configkit.commands import Command\n\n_COMMANDS = (Command(name="rcp"),)  # DEFECT\n'
    root = _write(tmp_path / "tree", {"leaf.py": defect})
    found = scan(root, seam_prefix="_", **_LIBRARY_RULES)
    assert found == [f"S2 {_marked(root, 'leaf.py')}: a command group named after the repository 'rcp'"]


def test_audit_of_no_root_is_empty() -> None:
    """No root, no model-graph fact: ``audit()`` is a walk from the roots a caller names."""
    assert audit() == []
