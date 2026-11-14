"""The checks that fail a test: two kinds of fact, two entry points, and one purity gate.

A rule nobody calls is a rule nobody keeps, so the library's own invariants are functions a consumer's test suite
calls, and this module is where they live. **Two functions, because two kinds of fact exist**: a model walk cannot
see a path literal, a command group's name, an import edge, an ``__all__`` or a call site, and a source scan cannot
see a field's role or a default.

:func:`audit` walks the **model graph** from root classes, recursing through every ``BaseModel`` field, and reports
eight facts:

``A1``
    a field with no role declared in ``IDENTITY_ROLES``.
``A2``
    a role declared for a field the model does not have.
``A3``
    a ``CONTENT`` field with a default: an identity field is derived, never defaulted (rcp-28).
``A4``
    a ``PATH`` field the resolver misses: the role is declared on a model-valued field, which
    :func:`configkit.roles.path_fields` names as one leaf and the loader then walks as free-form data -- so the
    model's own ``PATH`` leaves are never resolved as paths, and its other strings are resolved as if they were.
``A5``
    a code with no fault: a module's ``FAULT_CODES`` table declares a code that no ``Fault`` subclass carries, so a
    front end would report an exit number for a refusal nothing raises.
``A6``
    a schema field with a default: a ``SchemaTagged`` record's ``schema_id`` is derived at the write site (rcp-26).
``A7``
    a warning channel with no declared code set: the record declares ``WARNING_CODES`` and it can emit nothing.
``A8``
    a record type defined in a service module: the record's module imports a module outside the standard library,
    the ``pydantic`` closure, the library and the record's own top-level package (rcp-50).

:func:`scan` parses **source** with :mod:`ast` -- so a path literal inside a string constant and an import inside
a function are both visible -- and reports ten facts: a repository path literal (``S1``), a command group named
after a repository (``S2``), a shipped data pin or benchmark name (``S3``), an import of a consumer (``S4``), an
underscore seam across a package boundary (``S5``), an upward import (``S6``), a module whose ``__all__`` omits a
public name (``S7``), a call site still writing a removable schema spelling (``S8``), a second data gate (``S9``),
and a ``**options: Any`` signature (``S10``). ``scan(roots=...)`` delegates to :func:`audit`, so there is one
implementation of each rule and two entry points.

:func:`pure` runs one validator under an audit hook (rcp-20): validation that opens a file, opens a socket or
starts a process is not pure, and the findings say which event happened and to what.

Every check is a **function, not a hook**: nothing runs at import (C3), nothing runs at run time unless a caller
calls it, and a finding is a string a test asserts on. ``test_audit.py`` carries one defect and one control per
check, so a check that stops firing reddens a test rather than a run.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, TypeGuard

from pydantic import BaseModel

from configkit.faults import WARNING_CODES, Fault
from configkit.roles import Role, _nested_model
from configkit.schemas import SchemaTagged
from configkit.spec import Brand

__all__ = ["audit", "pure", "scan"]

#: The class attribute a record declares its roles on, merged down the MRO exactly as ``roles_of`` merges it. The
#: name lives here so a finding can spell it; the walk is :mod:`configkit.roles`'s rule, read without raising.
_ROLES: Final[str] = "IDENTITY_ROLES"

#: The module-level name a fault table is declared under: the shape :func:`configkit.faults.register_fault_codes`
#: returns, so A5 reads the same table a front end reports.
_FAULT_TABLE: Final[str] = "FAULT_CODES"

#: A path prefix no repository checkout lacks: a home directory, or a directory a checkout lives under.
_HOME_PREFIXES: Final[tuple[str, ...]] = ("~/", "/root/", "/home/", "/Users/", "/data/")

#: A path segment that names a checkout rather than a file.
_REPOSITORY_MARKERS: Final[tuple[str, ...]] = ("/repos/", "/repositories/", "/src/repos/")

#: The URI schemes that name *data*: a shipped literal with one of these is a data pin, not an example (rcp-48).
_DATA_SCHEMES: Final[tuple[str, ...]] = ("beir:", "mteb:", "suite:", "hf://", "images:")

#: The call names that declare a command or a command group: a string argument equal to a repository's name is a
#: group named after the repository (rcp-9).
_COMMAND_CALLS: Final[frozenset[str]] = frozenset(
    {"Command", "CommandTree", "build_parser", "add_parser", "command", "group", "tool"}
)

#: The modules allowed to read a file, each for a declared reason: the one document reader, the one byte seam, and
#: this check, which reads the source it checks. Every other module's read is a second data gate (et-23).
_FILE_READERS: Final[frozenset[str]] = frozenset({"document.py", "storage.py", "audit.py"})

#: The audit events :func:`pure` refuses: a file, a socket, a process.
_PURE_EVENTS: Final[tuple[str, ...]] = ("open", "socket.", "subprocess.")


def audit(*roots: type[BaseModel], brand: Brand | None = None) -> list[str]:
    """The model-graph findings for *roots* and every model they reach.

    The walk recurses through every ``BaseModel`` field, because a nested model that no test listed is the one that
    escapes a flat list (the reason this takes roots and not a model list). Each finding names the check, the
    model and the field path -- ``"A3 Leaf.kept: ..."`` -- so a reader can go straight to the declaration.

    Args:
        *roots: The models to walk. A root's nested models are walked too, once each.
        brand: The program's :class:`~configkit.spec.Brand`, when the caller has one. A6's finding then names the
            schema root a derived id sits under, so the report says what to write instead.

    Returns:
        The findings, in walk order; an empty list is the proof. Each is one line, prefixed with its check's id.
    """
    findings: list[str] = []
    allowed = _types_module_roots()
    faults = _fault_codes()
    walked: set[type[BaseModel]] = set()
    modules: set[str] = set()
    for root in roots:
        _walk(root, findings=findings, allowed=allowed, faults=faults, walked=walked, modules=modules, brand=brand)
    return findings


def scan(
    *paths: Path | str,
    roots: Sequence[type[BaseModel]] = (),
    seam_prefix: str = "_",
    api_globs: Sequence[str] = ("**/*.py",),
    consumer_imports: Sequence[str] = (),
    repository_names: Sequence[str] = (),
    data_pins: Sequence[str] = (),
    layers: Sequence[str] = (),
    removable: Sequence[str] = (),
) -> list[str]:
    """The source findings under *paths*, plus the model-graph findings for *roots*.

    Each source finding is ``"<check> <path>:<line>: <what>"``, so it names the file and the line a reader has to
    open. The trees are parsed, never imported: a defect in a file that cannot run is still a defect.

    Args:
        *paths: The files and directories to read. A directory is walked with *api_globs*.
        roots: The models to audit as well; ``scan(roots=...)`` is :func:`audit`'s second entry point.
        seam_prefix: The prefix that marks a private name. An import of a prefixed name **across a package
            boundary** is an underscore seam (S5), and a module-level name that does not carry it is public (S7).
        api_globs: The globs that select the files to read, relative to each directory (default: every ``*.py``).
        consumer_imports: The top-level packages the scanned source must not import (S4).
        repository_names: The repository names a command group must not be named after (S2).
        data_pins: The benchmark or dataset names the source must not ship, matched case-insensitively (S3).
        layers: The layer names in dependency order, lowest first; an import from a lower layer to a higher one is
            upward (S6). Empty means the layer order is not checked.
        removable: The schema spellings a consumer may delete; a site still writing one is reported (S8).

    Returns:
        The findings, in file order then check order; an empty list is the proof.
    """
    findings: list[str] = list(audit(*roots)) if roots else []
    local = frozenset(Path(path).resolve().name for path in paths)
    consumers = frozenset(consumer_imports)
    names = frozenset(repository_names)
    pins = frozenset(pin.lower() for pin in data_pins)
    stale = frozenset(removable)
    order = tuple(layers)
    for path in _files(paths, api_globs):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstrings(tree)
        findings.extend(_s1(path, tree, docstrings))
        findings.extend(_s2(path, tree, names))
        findings.extend(_s3(path, tree, docstrings, pins))
        findings.extend(_s4(path, tree, consumers))
        findings.extend(_s5(path, tree, local, seam_prefix))
        findings.extend(_s6(path, tree, order))
        findings.extend(_s7(path, tree, seam_prefix))
        findings.extend(_s8(path, tree, docstrings, stale))
        findings.extend(_s9(path, tree))
        findings.extend(_s10(path, tree))
    return findings


def pure(validator: Callable[[], Any]) -> list[str]:
    """Run *validator* and report every file, socket or process it touched (rcp-20).

    Validation is pure when it reads its argument and nothing else: a validator that opens a document, resolves a
    host or starts a process makes a config read depend on the machine, which is the failure the rule names. The
    process installs **one** audit hook the first time this is called -- an audit hook cannot be removed -- and the
    hook records only while a :func:`pure` call is running, so the rest of the process is unaffected.

    Args:
        validator: The validation to run. It takes no argument (wrap one that does in a lambda).

    Returns:
        One finding per refused event, as ``"<event>: <target>"``; an empty list is the proof. The validator's own
        exception propagates unchanged, so a refusal is not mistaken for impurity.
    """
    _install_hook()
    findings: list[str] = []
    _ACTIVE.append(findings)
    try:
        validator()
    finally:
        _ACTIVE.pop()
    return findings


# ---------------------------------------------------------------------------------------------------------------
# the model graph: eight checks over the roots a caller names
# ---------------------------------------------------------------------------------------------------------------


def _walk(
    model: type[BaseModel],
    *,
    findings: list[str],
    allowed: frozenset[str],
    faults: frozenset[str],
    walked: set[type[BaseModel]],
    modules: set[str],
    brand: Brand | None,
) -> None:
    """Check *model* and recurse into every model its fields name, once each."""
    if model in walked:
        return
    walked.add(model)
    roles = _check_roles(model, findings)
    _check_content_defaults(model, roles, findings)
    _check_path_roles(model, roles, findings)
    _check_fault_table(model, faults, modules, findings)
    _check_schema_defaults(model, brand, findings)
    _check_warning_codes(model, findings)
    _check_types_module(model, allowed, findings)
    for field in model.model_fields.values():
        nested = _nested_model(field.annotation)
        if nested is not None:
            _walk(
                nested,
                findings=findings,
                allowed=allowed,
                faults=faults,
                walked=walked,
                modules=modules,
                brand=brand,
            )


def _check_roles(model: type[BaseModel], findings: list[str]) -> Mapping[str, Role]:
    """A1 and A2: every field has a role, and every declared role names a field. Returns the usable roles."""
    merged: dict[str, Any] = {}
    for klass in reversed(model.__mro__):
        declared = klass.__dict__.get(_ROLES)
        if declared is None:
            continue
        if not isinstance(declared, Mapping):
            findings.append(f"A2 {model.__name__}: {_ROLES} must be a mapping of field name to role")
            continue
        merged.update(declared)
    roles: dict[str, Role] = {}
    for name, value in merged.items():
        if name not in model.model_fields:
            findings.append(
                f"A2 {model.__name__}.{name}: declared in {_ROLES} and the model has no such field; "
                "delete the declaration"
            )
            continue
        try:
            roles[name] = Role(value)
        except ValueError:
            findings.append(
                f"A1 {model.__name__}.{name}: the declared role {value!r} is not one of "
                f"{', '.join(member.value for member in Role)}, so the field has none"
            )
    for name in model.model_fields:
        if name not in merged:
            findings.append(
                f"A1 {model.__name__}.{name}: no role is declared in {_ROLES}; "
                "declare CONTENT, RUNTIME, PATH, URI, SECRET, ENV or OUTPUT"
            )
    return roles


def _check_content_defaults(model: type[BaseModel], roles: Mapping[str, Role], findings: list[str]) -> None:
    """A3: a CONTENT field with a default would key two different documents alike."""
    for name, field in model.model_fields.items():
        if roles.get(name) is Role.CONTENT and not field.is_required():
            findings.append(
                f"A3 {model.__name__}.{name}: a CONTENT field carries a default; an identity field is derived, "
                "never defaulted"
            )


def _check_path_roles(model: type[BaseModel], roles: Mapping[str, Role], findings: list[str]) -> None:
    """A4: a PATH role on a model-valued field is a path the resolver cannot resolve as one."""
    for name, field in model.model_fields.items():
        if roles.get(name) is Role.PATH and _nested_model(field.annotation) is not None:
            findings.append(
                f"A4 {model.__name__}.{name}: PATH is declared on a model-valued field, so the enumeration names "
                "this field as one leaf and the loader resolves every string inside it; declare PATH on the leaves"
            )


def _check_fault_table(model: type[BaseModel], faults: frozenset[str], modules: set[str], findings: list[str]) -> None:
    """A5: a code in a module's fault table that no ``Fault`` subclass carries is a dead exit code."""
    name = model.__module__
    if name in modules:
        return
    modules.add(name)
    module = sys.modules.get(name)
    table = getattr(module, _FAULT_TABLE, None) if module is not None else None
    if not _is_fault_table(table):
        return
    declared = {code for _, code in table}
    for code in sorted(declared - faults):
        findings.append(
            f"A5 {name}.{code}: {_FAULT_TABLE} declares a code no Fault subclass carries, so nothing raises it"
        )


def _check_schema_defaults(model: type[BaseModel], brand: Brand | None, findings: list[str]) -> None:
    """A6: a schema id is derived at the write site, never defaulted."""
    if not issubclass(model, SchemaTagged):
        return
    field = model.model_fields.get("schema_id")
    if field is None or field.is_required():
        return
    root = brand.schema_root if brand is not None else "the consumer's schema root"
    findings.append(
        f"A6 {model.__name__}.schema_id: a schema field carries a default; the id is derived at the write site, "
        f"under {root}"
    )


def _check_warning_codes(model: type[BaseModel], findings: list[str]) -> None:
    """A7: a record that declares the warning channel must declare a code it can emit."""
    for klass in model.__mro__:
        declared = klass.__dict__.get(WARNING_CODES)
        if declared is None:
            continue
        codes = (declared,) if isinstance(declared, str) else tuple(str(code) for code in declared)
        if not codes or any(not code.strip() for code in codes):
            findings.append(
                f"A7 {model.__name__}: {WARNING_CODES} is declared with no usable code, so the channel can emit "
                "nothing; declare the codes the record may warn with"
            )
        return


def _check_types_module(model: type[BaseModel], allowed: frozenset[str], findings: list[str]) -> None:
    """A8: a record type belongs in a types module, not in a module that imports a service."""
    module = sys.modules.get(model.__module__)
    if module is None:
        return
    local = allowed | {_root(model.__module__)}
    foreign = sorted(
        {_root(value.__name__) for value in vars(module).values() if isinstance(value, ModuleType)} - local
    )
    if foreign:
        findings.append(
            f"A8 {model.__name__}: the record type is defined in the service module {model.__module__}, "
            f"which imports {', '.join(foreign)}; move the record to a types module"
        )


def _is_fault_table(table: Any) -> TypeGuard[Sequence[tuple[Any, str]]]:
    """Whether *table* is a fault table: a sequence of ``(exception class, code)`` rows."""
    if isinstance(table, (str, bytes)) or not isinstance(table, Sequence):
        return False
    for row in table:
        if not (isinstance(row, tuple) and len(row) == 2 and isinstance(row[1], str)):
            return False
    return True


def _fault_codes() -> frozenset[str]:
    """Every code a :class:`~configkit.faults.Fault` subclass carries, the library's own and a consumer's."""
    codes: set[str] = set()
    pending: list[type[Fault]] = [Fault]
    while pending:
        klass = pending.pop()
        code = getattr(klass, "code", None)
        if isinstance(code, str) and code:
            codes.add(code)
        pending.extend(klass.__subclasses__())
    return frozenset(codes)


def _types_module_roots() -> frozenset[str]:
    """The roots a record's module may import: the standard library, the ``pydantic`` closure and the library."""
    return frozenset(set(sys.stdlib_module_names) | {_root(__name__)} | _pydantic_closure())


def _pydantic_closure() -> frozenset[str]:
    """The import names ``pydantic``'s metadata declares, plus ``pydantic`` itself: the A8 allow-list."""
    names = {"pydantic"}
    for requirement in importlib.metadata.requires("pydantic") or ():
        if "extra ==" in requirement:
            continue
        names.add(re.split(r"[ <>=!~;\[]", requirement, maxsplit=1)[0].replace("-", "_").lower())
    return frozenset(names)


# ---------------------------------------------------------------------------------------------------------------
# the source: ten checks over the files a caller names
# ---------------------------------------------------------------------------------------------------------------


def _s1(path: Path, tree: ast.Module, docstrings: frozenset[int]) -> Iterator[str]:
    """S1: a repository path literal (rcp-8)."""
    for node, value in _strings(tree, docstrings):
        if _is_repository_path(value):
            yield _finding(path, node, "S1", f"a repository path literal {value!r}")


def _is_repository_path(value: str) -> bool:
    """Whether *value* is a path under a home directory or a checkout, rather than a bare prefix.

    A check that refuses a repository path has to spell the prefixes it refuses, so a literal that *is* one of them
    (``"~/"``, ``"/repos/"``) is a pattern and not a path: the rule needs something after the prefix.
    """
    for prefix in _HOME_PREFIXES:
        if value.startswith(prefix) and value[len(prefix) :].strip():
            return True
    for marker in _REPOSITORY_MARKERS:
        if marker in value and value.split(marker, 1)[1].strip():
            return True
    return False


def _s2(path: Path, tree: ast.Module, names: frozenset[str]) -> Iterator[str]:
    """S2: a command group named after a repository (rcp-9)."""
    if not names:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node.func) not in _COMMAND_CALLS:
            continue
        for value in _call_strings(node):
            if value in names:
                yield _finding(path, node, "S2", f"a command group named after the repository {value!r}")


def _s3(path: Path, tree: ast.Module, docstrings: frozenset[int], pins: frozenset[str]) -> Iterator[str]:
    """S3: a shipped data pin or benchmark name (rcp-48)."""
    for node, value in _strings(tree, docstrings):
        lowered = value.lower()
        if lowered in pins or _names_data(lowered):
            yield _finding(path, node, "S3", f"a shipped data pin or benchmark name {value!r}")


def _names_data(value: str) -> bool:
    """Whether *value* names data through a data-URI scheme; a bare scheme is the check's own pattern, not a pin."""
    return any(value.startswith(scheme) and value[len(scheme) :].strip() for scheme in _DATA_SCHEMES)


def _s4(path: Path, tree: ast.Module, consumers: frozenset[str]) -> Iterator[str]:
    """S4: an import of a consumer (C2, at the text level)."""
    if not consumers:
        return
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if _root(module) in consumers:
                yield _finding(path, node, "S4", f"an import of the consumer {module!r}")


def _s5(path: Path, tree: ast.Module, local: frozenset[str], seam_prefix: str) -> Iterator[str]:
    """S5: an underscore seam across a package boundary (C5, rcp-44)."""
    if not seam_prefix:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module is None or _root(node.module) in local:
                continue
            for alias in node.names:
                if alias.name.startswith(seam_prefix):
                    yield _finding(
                        path, node, "S5", f"an underscore seam: {node.module}.{alias.name} crosses a package boundary"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) in local:
                    continue
                if any(part.startswith(seam_prefix) for part in alias.name.split(".")):
                    yield _finding(path, node, "S5", f"an underscore seam: {alias.name} crosses a package boundary")


def _s6(path: Path, tree: ast.Module, layers: Sequence[str]) -> Iterator[str]:
    """S6: an import that points up the layer order (ee-S25/X2)."""
    if not layers:
        return
    source = _layer(path, layers)
    if source is None:
        return
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            target = _layer_of_name(module, layers)
            if target is None or layers.index(target) <= layers.index(source):
                continue
            yield _finding(path, node, "S6", f"an upward import: {source} imports {module!r} from {target}")


def _s7(path: Path, tree: ast.Module, seam_prefix: str) -> Iterator[str]:
    """S7: a module whose ``__all__`` omits a public name (et-32, ee-S18)."""
    declared = _all_names(tree)
    for name, node in _public_names(tree, seam_prefix):
        if name not in declared:
            yield _finding(path, node, "S7", f"the module's __all__ omits the public name {name!r}")


def _s8(path: Path, tree: ast.Module, docstrings: frozenset[int], removable: frozenset[str]) -> Iterator[str]:
    """S8: a call site that still writes a removable schema spelling (rcp-30)."""
    if not removable:
        return
    for node, value in _strings(tree, docstrings):
        if value in removable:
            yield _finding(path, node, "S8", f"a removable schema spelling is still written: {value!r}")


def _s9(path: Path, tree: ast.Module) -> Iterator[str]:
    """S9: a second data gate -- a file read outside the one reader and the one byte seam (et-23)."""
    if path.name in _FILE_READERS:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node.func)
        if name in {"read_text", "read_bytes"} and isinstance(node.func, ast.Attribute):
            yield _finding(path, node, "S9", f"a file read outside {sorted(_FILE_READERS)}: .{name}()")
        elif name == "open" and not isinstance(node.func, ast.Attribute):
            yield _finding(path, node, "S9", f"a file read outside {sorted(_FILE_READERS)}: open()")
        elif name == "open" and _owner(node.func) in {"io", "codecs"}:
            yield _finding(path, node, "S9", f"a file read outside {sorted(_FILE_READERS)}: {_owner(node.func)}.open()")


def _s10(path: Path, tree: ast.Module) -> Iterator[str]:
    """S10: a signature that declares ``**options: Any`` instead of a model (rcp-24)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        extra = node.args.kwarg
        if extra is not None and extra.arg == "options" and _names_any(extra.annotation):
            yield _finding(
                path, node, "S10", "a **options: Any signature; declare the options as an OptionSchema model"
            )


def _files(paths: Iterable[Path | str], api_globs: Sequence[str]) -> list[Path]:
    """Every file under *paths* the globs select, in a stable order and each once."""
    found: dict[Path, None] = {}
    for path in paths:
        target = Path(path)
        if target.is_dir():
            for pattern in api_globs:
                for file in sorted(target.glob(pattern)):
                    if file.is_file() and "__pycache__" not in file.parts:
                        found.setdefault(file, None)
        elif target.is_file():
            found.setdefault(target, None)
    return list(found)


def _docstrings(tree: ast.Module) -> frozenset[int]:
    """The ``id()`` of every docstring node: a docstring describes what a rule refuses, it does not ship data."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return frozenset(found)


def _strings(tree: ast.Module, docstrings: frozenset[int]) -> Iterator[tuple[ast.Constant, str]]:
    """Every string constant in *tree* that is not a docstring."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node, node.value


def _call_strings(node: ast.Call) -> Iterator[str]:
    """Every string constant a call is given, positionally or by keyword."""
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            yield argument.value
    for keyword in node.keywords:
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            yield keyword.value.value


def _imported_modules(node: ast.AST) -> Iterator[str]:
    """The modules an import node names: its aliases, or its ``from`` module."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom) and not node.level and node.module is not None:
        yield node.module


def _all_names(tree: ast.Module) -> frozenset[str]:
    """The names a module's ``__all__`` declares; empty when it declares none."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(getattr(target, "id", None) == "__all__" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.List | ast.Tuple):
            return frozenset(
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return frozenset()


def _public_names(tree: ast.Module, seam_prefix: str) -> Iterator[tuple[str, ast.AST]]:
    """The module-level names a module publishes: a ``def``, a ``class``, or an ``UPPER_CASE`` constant."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith(seam_prefix):
                yield node.name, node
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            name = getattr(target, "id", None)
            if isinstance(name, str) and not name.startswith(seam_prefix) and re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                yield name, node


def _layer(path: Path, layers: Sequence[str]) -> str | None:
    """The layer *path* sits in: the first component of its path that names one."""
    for part in path.resolve().parts:
        if part in layers:
            return part
    return None


def _layer_of_name(module: str, layers: Sequence[str]) -> str | None:
    """The layer an imported *module* sits in: the first component of its dotted name that names one."""
    for part in module.split("."):
        if part in layers:
            return part
    return None


def _names_any(annotation: ast.expr | None) -> bool:
    """Whether an annotation is ``Any`` (or a qualified spelling of it)."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id == "Any"
    return isinstance(annotation, ast.Attribute) and annotation.attr == "Any"


def _callee(node: ast.expr) -> str:
    """The name a call is made by: ``import_module`` for ``importlib.import_module`` and for the bare name."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _owner(node: ast.expr) -> str:
    """The name the receiver of a call is bound to (``io`` for ``io.open``), or ``""``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id
    return ""


def _root(dotted: str) -> str:
    """The top-level part of a dotted module name."""
    return dotted.split(".", 1)[0]


def _finding(path: Path, node: ast.AST, check: str, what: str) -> str:
    """One source finding: ``"<check> <path>:<line>: <what>"``."""
    return f"{check} {path}:{getattr(node, 'lineno', 0)}: {what}"


# ---------------------------------------------------------------------------------------------------------------
# purity: one audit hook, installed once, recording only while a `pure` call runs (rcp-20)
# ---------------------------------------------------------------------------------------------------------------

#: The stacks of findings an active ``pure`` call is recording into. A list, because a nested call is legal.
_ACTIVE: list[list[str]] = []

#: Whether the hook is installed. An audit hook cannot be removed, so this is once per process.
_INSTALLED = False


def _install_hook() -> None:
    """Install the audit hook, once per process; a second call is a no-op."""
    global _INSTALLED
    if _INSTALLED:
        return
    sys.addaudithook(_audit_hook)
    _INSTALLED = True


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    """Record *event* when it is a file, socket or process event and a ``pure`` call is running."""
    if not _ACTIVE:
        return
    if not any(event == name or event.startswith(name) for name in _PURE_EVENTS):
        return
    target = repr(args[0]) if args else ""
    _ACTIVE[-1].append(f"{event}: {target}" if target else event)
