"""One loader: read a document, resolve its paths, validate a model, and map a validation error to one fault.

The loader holds the five rules ``emb-train``'s v0 config module fixed (``emb_train_cli/config.py:5-18``), and it
keeps them:

1. the document is UTF-8 text, and a file that is not is refused naming the file;
2. a duplicate key is refused: a duplicate can hide a value, and a hidden value breaks reproducibility;
3. a required ``schema`` key must equal the id the caller passes (:func:`read`'s ``schema_id`` parameter), and
   the key is popped into :attr:`Document.schema` so no model ever receives it;
4. a relative ``PATH`` value resolves against the document's own directory, and an absolute one is kept;
5. the resolution happens before anything hashes the payload, so a moved local file changes the identity.

Two of the five changed, and the module states them: it reads YAML as well as JSON (YAML is imported lazily, so a
JSON document never imports it and the library's import path stays ``pydantic``-only), and it honours a top-level
``extends``. :func:`deep_merge` is that rule's only implementation: the base mapping is merged *under* the child,
key by key and recursively, and a file that reaches itself is refused naming the chain.

The stages are separate functions because a run's pipeline needs them separated (``read -> resolve -> apply ->
validate -> identity``, the contract's order): :func:`read` returns an unvalidated :class:`Document`,
:func:`resolve` builds a draft with ``model_construct`` and resolves the ``PATH`` leaves, :func:`validate` is the
one stage that runs pydantic, and :class:`Applied` is the record a run's manifest reads back. ``apply`` lives in
:mod:`configkit.overrides`, above this module: it needs :func:`read` and :func:`resolve`, never the reverse.

Nothing here reads the environment, opens a file at import time, or imports a consumer.
"""

from __future__ import annotations

import copy
import difflib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Any, Final, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from configkit.faults import Fault, OSErrorFault, require
from configkit.roles import _declares_roles, _model_of, _nested_model, path_fields, redact, roles_of

__all__ = [
    "Applied",
    "Document",
    "FieldTypes",
    "config_error",
    "deep_merge",
    "leaf_paths",
    "load",
    "read",
    "register_field_type",
    "resolve",
    "resolve_reference",
    "validate",
]

#: The key a document uses to name the schema it was written as. It is popped before validation, so no model
#: declares it: the model's own id field is ``schema_id``, and the loader never passes one to the other.
_SCHEMA: Final[str] = "schema"

#: The key a document uses to name the file it extends. It is popped before validation, so no model declares it.
_EXTENDS: Final[str] = "extends"

#: The hint a missing YAML reader reports when the caller named none. It names no distribution, because the
#: library ships in more than one and an install line that is wrong after the move is worse than a general one.
_YAML_HINT: Final[str] = "install the YAML reader of the distribution that ships this library"


def _refusal(message: str, *, code: str = "value_error", **details: Any) -> Fault:
    """A deliberate refusal: *message* for a person, *code* from the fault table, *details* for a program."""
    return Fault(message, code=code, details=details)


def _copy(value: Any) -> Any:
    """*value* with every mapping and sequence copied, at every depth, so a merge shares no mutable value."""
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(item) for item in value]
    return copy.deepcopy(value)


def deep_merge(base: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """*base* with *child* merged over it: mappings merge key by key, any other value the child replaces.

    This is the one implementation of the ``extends`` rule: a base document is merged *under* the child that
    extends it, recursively, so a key the child does not set is inherited and a key it does set wins. The result
    shares no mutable value with *base* or *child*, and neither argument is changed.

    Args:
        base: The mapping merged under *child* (the extended document).
        child: The mapping merged over *base* (the extending document).

    Returns:
        A new mapping.
    """
    merged: dict[str, Any] = _copy(base)
    for key, value in child.items():
        inherited = merged.get(key)
        if isinstance(value, Mapping) and isinstance(inherited, Mapping):
            merged[key] = deep_merge(inherited, value)
        else:
            merged[key] = _copy(value)
    return merged


@dataclass(frozen=True)
class Document:
    """A document as it was read, before any model sees it.

    Attributes:
        path: The file as the caller named it.
        base: The directory a relative ``PATH`` value resolves against: the document's own directory, never the
            process's working directory.
        payload: The mapping: every ``extends`` chain merged under the child, the ``extends`` and ``schema`` keys
            popped, and no value validated. A reader must not mutate it; :func:`resolve` copies what it changes.
        schema: The schema id the document declared in its ``schema`` key, or ``None`` when it declared none. The
            key is not in :attr:`payload`: a model declares ``schema_id`` and never receives the document's tag.
    """

    path: Path
    base: Path
    payload: Mapping[str, Any]
    schema: str | None = None


def _check_keys(node: Any, path: Path, *, seen: set[int]) -> None:
    """Refuse a key a YAML mapping holds twice, at every level; *seen* stops a recursive anchor."""
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    if node.id == "mapping":
        keys: list[Any] = []
        for key_node, value_node in node.value:
            if key_node.value in keys:
                raise _refusal(
                    f"config {path} holds the key {key_node.value!r} twice; a duplicate key can hide a value, "
                    "and a hidden value breaks reproducibility",
                    path=str(path),
                    key=str(key_node.value),
                )
            keys.append(key_node.value)
            _check_keys(value_node, path, seen=seen)
    elif node.id == "sequence":
        for item in node.value:
            _check_keys(item, path, seen=seen)


def _yaml_module(hint: str) -> Any:
    """The YAML reader, imported on first use; ``Any`` because the library must not import it at module level."""
    return require("yaml", hint=hint)


def _yaml_document(text: str, path: Path, hint: str) -> Any:
    """The mapping *text* holds, read as YAML, with a duplicate key refused at every level."""
    yaml = _yaml_module(hint)
    try:
        _check_keys(yaml.compose(text), path, seen=set())
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _refusal(f"config {path} is not YAML: {exc}", path=str(path)) from exc


def _json_document(text: str, path: Path) -> Any:
    """The mapping *text* holds, read as JSON, with a duplicate key refused at every level."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for key, value in pairs:
            if key in mapping:
                raise _refusal(
                    f"config {path} holds the key {key!r} twice; a duplicate key can hide a value, "
                    "and a hidden value breaks reproducibility",
                    path=str(path),
                    key=key,
                )
            mapping[key] = value
        return mapping

    try:
        return json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise _refusal(f"config {path} is not JSON: {exc}", path=str(path)) from exc


def _mapping(path: Path, yaml_hint: str) -> dict[str, Any]:
    """The mapping *path* holds: UTF-8 text, parsed as JSON for a ``.json`` file and as YAML otherwise."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _refusal(f"config {path} is not UTF-8 text: {exc}", path=str(path)) from exc
    payload = _json_document(text, path) if path.suffix.lower() == ".json" else _yaml_document(text, path, yaml_hint)
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise _refusal(f"config {path} holds {type(payload).__name__}, not a mapping", path=str(path))
    return dict(payload)


def _merged(path: Path, chain: tuple[Path, ...], *, extends: bool, yaml_hint: str) -> dict[str, Any]:
    """*path*'s mapping with its ``extends`` chain merged under it, and *chain* the files already read."""
    resolved = path.resolve()
    if resolved in chain:
        links = " -> ".join(str(link) for link in (*chain, resolved))
        raise _refusal(
            f"config {path} extends itself through {links}; an extends chain must end",
            chain=[str(link) for link in (*chain, resolved)],
        )
    payload = _mapping(path, yaml_hint)
    base_ref = payload.pop(_EXTENDS, None)
    if base_ref is None:
        return payload
    if not extends:
        raise _refusal(
            f"config {path} declares extends: {base_ref!r}, but the reader was called with extends=False",
            path=str(path),
        )
    if not isinstance(base_ref, str):
        raise _refusal(f"config {path}: `extends` names one path, got {base_ref!r}", path=str(path))
    base_path = path.parent / base_ref
    try:
        base = _merged(base_path, (*chain, resolved), extends=extends, yaml_hint=yaml_hint)
    except OSError as exc:
        raise OSErrorFault(
            f"config {path} extends {base_ref!r}, which cannot be read: {exc}",
            details={"path": str(path), "base": base_ref, "resolved": str(base_path)},
            hint=f"create {base_path} or fix the `extends` line in {path}",
        ) from exc
    return deep_merge(base, payload)


def read(
    path: Path,
    *,
    schema_id: str | None = None,
    extends: bool = True,
    yaml_hint: str = _YAML_HINT,
) -> Document:
    """Read *path* as a document: UTF-8, unique keys, ``extends`` merged under it, the ``schema`` id checked.

    The id is a parameter because each consumer's document declares its own, and the model must not default it: a
    caller that names no id checks none, and the model's own ``Literal`` field then owns the check.

    Args:
        path: The document to read.
        schema_id: The id the document's ``schema`` key must equal, or ``None`` for no check.
        extends: Whether a top-level ``extends`` is honoured; ``False`` refuses a document that declares one.
        yaml_hint: The install line a missing YAML reader reports, verbatim.

    Returns:
        The document, with no value validated and the ``schema`` key recorded on :attr:`Document.schema` rather
        than left in the payload: ``resolve`` would otherwise refuse it as a key the model does not declare.

    Raises:
        Fault: the file is not UTF-8 (``value_error``); a key appears twice (``value_error``); a base it extends
            is missing or forms a cycle (``value_error``); the document is not a mapping; the ``schema`` id is
            absent or different from *schema_id*; or the YAML reader is absent (``optional.missing``, with
            *yaml_hint*). An absent file is an ``OSError``, which the fault table's generic row classifies.
    """
    resolved = Path(path).resolve()
    payload = _merged(resolved, (), extends=extends, yaml_hint=yaml_hint)
    declared = payload.pop(_SCHEMA, None)
    if schema_id is not None and declared != schema_id:
        raise _refusal(
            f"config {path} declares schema {declared!r}, not {schema_id!r}; every document must declare {schema_id!r}",
            path=str(path),
            schema=declared,
            expected=schema_id,
        )
    return Document(path=Path(path), base=resolved.parent, payload=payload, schema=declared)


def _path_rows(model: type[BaseModel], extra: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """The dotted rows :func:`resolve` walks: the model's declared ``PATH`` leaves, then the caller's own rows.

    A model that declares no ``IDENTITY_ROLES`` declares no ``PATH`` leaf, so only the caller's rows apply;
    ``audit``'s A4 check is what refuses a ``PATH`` field this enumeration misses. A *partial* declaration is not
    that case: :func:`path_fields` refuses it and the refusal propagates, because reading it as "no PATH leaf"
    would leave every ``PATH`` value unresolved and say nothing. A row written as one dotted string is refused
    too: a string is a sequence of characters, so it would walk ``s``, ``o``, ``u``... and resolve nothing, which
    is the silent miss this library exists to prevent.
    """
    try:
        declared = path_fields(model)
    except Fault:
        if _declares_roles(model):
            raise
        declared = ()
    rows: list[tuple[str, ...]] = []
    for row in extra:
        if isinstance(row, str):
            raise _refusal(
                f"a path_fields row is a sequence of field names, not the string {row!r}; "
                f"write {(row,)!r} or {row!r}.split('.') for a dotted row",
                row=row,
            )
        rows.append(tuple(row))
    return tuple(declared) + tuple(rows)


def _resolve_paths(value: Any, base: Path) -> Any:
    """*value* with every relative path resolved against *base*, an absolute one and a URI kept unchanged."""
    if isinstance(value, str):
        if not value.strip() or "://" in value:
            return value
        path = Path(value)
        return value if path.is_absolute() else str((base / path).resolve())
    if isinstance(value, Mapping):
        return {key: _resolve_paths(item, base) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve_paths(item, base) for item in value]
    return value


def _resolved(value: Any, parts: Sequence[str], base: Path) -> Any:
    """*value* with the leaf *parts* names resolved against *base*; a list of models applies the row to each."""
    if not parts:
        return _resolve_paths(value, base)
    head, rest = parts[0], parts[1:]
    if isinstance(value, Mapping):
        if head not in value:
            return value
        return {**value, head: _resolved(value[head], rest, base)}
    if isinstance(value, (list, tuple)):
        return [_resolved(item, parts, base) for item in value]
    return value


def _unknown_keys(model: type[BaseModel], payload: Mapping[str, Any], *, source: str) -> None:
    """Refuse a key the model does not declare, when the model forbids extras.

    ``model_construct`` drops an unknown top-level key instead of refusing it, so a typo in a document would
    vanish between :func:`resolve` and :func:`validate`; this is the one place that keeps the model's
    ``extra="forbid"`` a refusal. The refusal names *source*, the way :func:`config_error` names its own, because
    a reader has to know which file to open. A nested model's keys need no check here: the nested value is still a
    mapping when :func:`validate` runs, so pydantic refuses them itself.
    """
    if model.model_config.get("extra") != "forbid":
        return
    unknown = sorted(set(payload) - set(model.model_fields))
    if unknown:
        known = ", ".join(sorted(model.model_fields))
        raise _refusal(
            f"{source}: {model.__name__} does not declare {', '.join(repr(key) for key in unknown)}; "
            f"known keys: {known}",
            model=model.__name__,
            unknown=unknown,
            known=sorted(model.model_fields),
            source=source,
        )


def resolve[ModelT: BaseModel](
    document: Document,
    model: type[ModelT],
    *,
    path_fields: Sequence[Sequence[str]] = (),
) -> ModelT:
    """Build *model* from *document* with ``model_construct`` and resolve every ``PATH`` leaf: an unvalidated draft.

    The paths resolve against the document's own directory (:attr:`Document.base`), which is the first of the
    library's two bases: an override resolves against the base its caller names (:func:`configkit.overrides.apply`),
    and neither is ever the process's working directory. A relative value resolves, an absolute one is kept, and a
    value that carries a scheme is not a local path and stays verbatim.

    Args:
        document: The document to build the draft from.
        model: The model class to construct; no value is validated.
        path_fields: Extra dotted rows to resolve, over the ``Role.PATH`` leaves the model declares. A row through
            a list or mapping of models applies to every element.

    Returns:
        The draft: a model built without validation, whose ``PATH`` leaves hold absolute paths.

    Raises:
        Fault: the document holds a key *model* does not declare and the model forbids extras (``value_error``);
            ``model_construct`` would drop it, and a dropped key is a typo nobody sees. A *path_fields* row
            written as one dotted string is refused too (``value_error``), and so is a malformed
            ``IDENTITY_ROLES`` declaration (see :func:`configkit.roles.roles_of`).
    """
    payload = document.payload
    _unknown_keys(model, payload, source=str(document.path))
    for parts in _path_rows(model, path_fields):
        payload = _resolved(payload, tuple(parts), document.base)
    return model.model_construct(**payload)


def _payload_of(value: Any) -> Any:
    """*value* as the plain mapping pydantic can validate again: a constructed model becomes its own values.

    ``model_construct`` fills a ``default_factory`` model as a real instance, and pydantic does not re-validate an
    instance it is handed; the draft's values are read back as data so that :func:`validate` is the one stage that
    checks them, a nested override included.
    """
    if isinstance(value, BaseModel):
        return {name: _payload_of(item) for name, item in dict(value).items()}
    if isinstance(value, Mapping):
        return {name: _payload_of(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload_of(item) for item in value]
    return value


def _validated[ModelT: BaseModel](draft: ModelT, field_types: FieldTypes | None) -> ModelT:
    """*draft* validated into a real model: the one stage that runs pydantic."""
    payload: dict[str, Any] = _payload_of(draft)
    if field_types is not None:
        payload = field_types.apply(payload, type(draft))
    return type(draft).model_validate(payload)


def validate[ModelT: BaseModel](draft: ModelT, *, field_types: FieldTypes | None = None) -> ModelT:
    """Validate *draft* into a model, so one invalid document produces one ``pydantic.ValidationError``.

    The draft comes from :func:`resolve` (``model_construct`` never validates), so this is the only stage that
    runs a validator: a value an override wrote is validated here, and no other stage may validate instead.

    Args:
        draft: The draft :func:`resolve` built.
        field_types: A registry of validators for the draft's own field types, or ``None`` for pydantic alone.

    Returns:
        The validated model.

    Raises:
        pydantic.ValidationError: a value does not fit. :func:`load` and :func:`configkit.overrides.pipeline` map
            it to one fault with :func:`config_error`.
    """
    return _validated(draft, field_types)


def load[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
    *,
    schema_id: str | None = None,
    path_fields: Sequence[Sequence[str]] = (),
    validate: bool = True,
    extends: bool = True,
    yaml_hint: str = _YAML_HINT,
    field_types: FieldTypes | None = None,
) -> ModelT:
    """``read + resolve + validate``: the whole loader for a caller with no overrides.

    A run uses :func:`configkit.overrides.pipeline` instead, because an override must be applied between
    :func:`resolve` and :func:`validate`; this function is the no-override path, and its result is the same model
    the pipeline returns for a caller that passes no override.

    Args:
        path: The document to read.
        model: The model class to build.
        schema_id: The id the document's ``schema`` key must equal, or ``None`` for no check.
        path_fields: Extra dotted rows to resolve, over the ``Role.PATH`` leaves the model declares.
        validate: Whether to validate; ``False`` returns the draft :func:`resolve` built.
        extends: Whether a top-level ``extends`` is honoured.
        yaml_hint: The install line a missing YAML reader reports, verbatim.
        field_types: A registry of validators for the model's own field types.

    Returns:
        The model, or the draft when *validate* is ``False``.

    Raises:
        Fault: reading refused the document (see :func:`read`), or validation failed (one
            :func:`config_error` fault naming the file and the field).
    """
    document = read(path, schema_id=schema_id, extends=extends, yaml_hint=yaml_hint)
    draft = resolve(document, model, path_fields=path_fields)
    if not validate:
        return draft
    try:
        return _validated(draft, field_types)
    except ValidationError as exc:
        raise config_error(exc, model=model, source=str(path)) from exc


def _type_name(annotation: Any) -> str | None:
    """The written form of *annotation*: a reader's word for what the field accepts (``int``, ``str | None``)."""
    if annotation is None:
        return None
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return " | ".join(filter(None, (_type_name(argument) for argument in get_args(annotation))))
    if origin is Literal:
        return "one of " + ", ".join(repr(argument) for argument in get_args(annotation))
    if origin in (list, tuple, set, frozenset, dict):
        name = getattr(origin, "__name__", str(origin))
        arguments = ", ".join(filter(None, (_type_name(argument) for argument in get_args(annotation))))
        return f"{name}[{arguments}]" if arguments else name
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _locate(model: type[BaseModel], loc: Sequence[str]) -> tuple[type[BaseModel] | None, Any]:
    """The model *loc* lives in, and the annotation of the value *loc* names (``None`` when it is unknown)."""
    container: type[BaseModel] | None = model
    annotation: Any = None
    for index, part in enumerate(loc):
        last = index == len(loc) - 1
        if container is not None and part in container.model_fields:
            annotation = container.model_fields[part].annotation
            if not last:
                container = _model_of(annotation)
        elif container is not None:
            annotation = None  # an unknown key of this model: the key itself is the leaf
        else:
            container = _nested_model(annotation)  # an index or key inside a list or mapping of models
            annotation = None
    return container, annotation


def _plain(value: Any) -> Any:
    """*value* as JSON-serialisable facts: a scalar kept, a container walked, anything else its ``repr``."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return repr(value)


def _redacted_input(value: Any, container: type[BaseModel] | None, loc: Sequence[str]) -> Any:
    """*value* as a problem's ``input``: every ``SECRET`` and ``URI`` field the model declares is redacted."""
    if container is None:
        return _plain(value)
    try:
        roles_of(container)
    except Fault:
        # No declared role means no field the library can name as a secret; audit's A1 check refuses such a model.
        return _plain(value)
    if loc:
        leaf = loc[-1]
        for name, field in container.model_fields.items():
            if (field.serialization_alias or field.alias or name) == leaf:
                return _plain(redact({leaf: value}, model=container)[leaf])
    if isinstance(value, Mapping):
        return _plain(redact(value, model=container))
    return _plain(value)


def config_error(
    exc: ValidationError,
    *,
    model: type[BaseModel],
    source: str,
    redact: bool = True,
) -> Fault:
    """A ``ValidationError`` of a document as one :class:`~configkit.faults.Fault` a caller can act on.

    ``details.errors`` holds one entry per problem, each with the field path the reader wrote, the problem in
    pydantic's words, the value that was given (``SECRET`` and ``URI`` fields redacted), the expected type, the
    keys the mapping accepts, and the source. The message names the first problem and where it came from, so a
    caller sees one line and a program sees every problem.

    Args:
        exc: The ``pydantic.ValidationError`` a document's validation raised.
        model: The model class that was validated; it supplies the expected types, the known keys and the roles
            the redaction walks.
        source: What was validated (a file path, ``"run config"``), named in the message and on each problem.
        redact: Whether to redact; ``False`` keeps the given values verbatim, for a caller whose model declares
            no roles and whose values are known to hold no secret.

    Returns:
        The fault: ``code`` is ``value_error``, ``details["errors"]`` the problems, ``hint`` and ``cli_hint`` the
        next step for a Python caller and for the command line.
    """
    problems: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False):
        loc = [str(part) for part in error["loc"]]
        container, annotation = _locate(model, loc)
        field = ".".join(loc) or "<config>"
        known = sorted(container.model_fields) if container is not None else []
        problem: dict[str, Any] = {
            "field": field,
            "problem": str(error["msg"]).removeprefix("Value error, "),
            "input": _redacted_input(error.get("input"), container, loc) if redact else _plain(error.get("input")),
            "known": known,
            "source": source,
        }
        expected = _type_name(annotation)
        if expected is not None:
            problem["expected"] = expected
        if error["type"] == "extra_forbidden":
            problem["problem"] = "unknown key"
            close = difflib.get_close_matches(loc[-1], known, n=1) if loc else []
            if close:
                problem["did_you_mean"] = ".".join([*field.split(".")[:-1], close[0]])
        problems.append(problem)
    first = problems[0]
    expected_text = f" (expected {first['expected']})" if first.get("expected") else ""
    guess = f"; did you mean {first['did_you_mean']!r}?" if "did_you_mean" in first else ""
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    if first["field"] == "<config>":
        hint, cli_hint = f"fix {source}", f"fix {source}"
    else:
        hint = f"fix {first['field']} in {source}, or override it (overrides=['{first['field']}=...'])"
        cli_hint = f"fix {first['field']} in {source}, or override it with --set {first['field']}=..."
    return Fault(
        f"{source}: {first['field']}: {first['problem']}{expected_text}{guess}{more}",
        code="value_error",
        details={"errors": problems},
        hint=hint,
        cli_hint=cli_hint,
    )


def leaf_paths(model: type[BaseModel]) -> tuple[str, ...]:
    """The dotted path of every leaf *model* declares, a nested model's leaves included.

    One derived enumeration, so a new contract field is expressible with no edit: the five hand-typed leaf sets
    ``emb-eval`` keeps are what this replaces. A field whose annotation names a model at any depth (``Inner``,
    ``list[Inner]``, ``dict[str, Inner]``) is a branch and contributes its model's leaves under its own path;
    every other field is a leaf.

    Args:
        model: The model class to enumerate.

    Returns:
        The dotted paths in declaration order, each field name from *model* down to the leaf.
    """
    paths: list[str] = []
    for name, field in model.model_fields.items():
        nested = _nested_model(field.annotation)
        if nested is not None:
            paths.extend(f"{name}.{tail}" for tail in leaf_paths(nested))
        else:
            paths.append(name)
    return tuple(paths)


def _default_schema_hook(core_schema: Any, handler: Any) -> Any:
    """pydantic's own JSON schema for a core schema: what a field type with no registered hook contributes."""
    return handler(core_schema)


class FieldTypes:
    """A caller-owned registry of field types: one validator per type, and the JSON-schema hook it composes.

    The registry is an object, never module state, so two callers in one process cannot see each other's
    registrations and a test builds its own. A registered validator runs in :func:`validate`, before pydantic
    sees the value, and contributes its own JSON-schema hook when it declares one.
    """

    def __init__(self) -> None:
        self._validators: dict[type[BaseModel], Callable[[Any], Any]] = {}

    def register(self, model_type: type[BaseModel], validator: Callable[[Any], Any]) -> None:
        """Register *validator* for *model_type*.

        Args:
            model_type: The type a field is annotated with.
            validator: The callable that turns a document value into that type's value.

        Raises:
            Fault: *model_type* already has a validator; one type has one rule, and a silent second registration
                would make the result depend on import order.
        """
        if model_type in self._validators:
            raise _refusal(
                f"a field validator is already registered for {model_type.__name__}; "
                "one field type has one rule, so replace the registry instead",
                model_type=model_type.__name__,
            )
        self._validators[model_type] = validator

    def validator_for(self, model_type: type[BaseModel]) -> Callable[[Any], Any] | None:
        """The validator registered for *model_type*, or ``None`` when the type is not registered."""
        return self._validators.get(model_type)

    def schema_hook(self, model_type: type[BaseModel]) -> Callable[..., Any]:
        """The JSON-schema hook to compose for *model_type*: its validator's own, or pydantic's default."""
        validator = self._validators.get(model_type)
        hook = getattr(validator, "__get_pydantic_json_schema__", None)
        return hook if hook is not None else _default_schema_hook

    def apply(self, payload: Mapping[str, Any], model: type[BaseModel]) -> dict[str, Any]:
        """*payload* with every registered type's validator applied to the field that declares it.

        Args:
            payload: The values a draft holds, by field name.
            model: The model class the values belong to.

        Returns:
            A new mapping; *payload* is not changed. A field whose annotation names no registered type is kept
            as it is, and a nested model's own fields are pydantic's to validate.
        """
        values = dict(payload)
        for name, field in model.model_fields.items():
            annotation = field.annotation
            validator = self.validator_for(annotation) if annotation is not None else None
            if validator is not None and name in values:
                values[name] = validator(values[name])
        return values


def register_field_type(
    registry: FieldTypes,
    model_type: type[BaseModel],
    validator: Callable[[Any], Any],
) -> None:
    """Register *validator* for *model_type* in *registry*: the declaration door rcp 19 names.

    Args:
        registry: The caller's registry; there is no module-level one, so a test cannot leak into another test.
        model_type: The type a field is annotated with.
        validator: The callable that turns a document value into that type's value.

    Raises:
        Fault: *model_type* already has a validator in *registry* (see :meth:`FieldTypes.register`).
    """
    registry.register(model_type, validator)


def resolve_reference(text: str, *, attempts: list[str]) -> Mapping[str, Any]:
    """Resolve one reference to a document mapping: an existing file, a ``kind:value`` shorthand, or a bare name.

    The library has no registry of names, so the three forms are what a reference can mean, tried in that order:
    a path that names an existing document is read; a ``kind:value`` reference becomes ``{kind: value}``; any
    other word becomes ``{"name": word}``, the one-key mapping a model with a ``name`` field declares. The caller
    passes the list the attempts are recorded in, so a refusal or a log line can name what was tried.

    Args:
        text: The reference as the caller wrote it.
        attempts: The list to append one line per attempt to, in the order tried, the matching one last.

    Returns:
        The document mapping the reference names.

    Raises:
        Fault: the reference is empty or whitespace. Reading the matched path refuses as :func:`read` does.
    """
    reference = text.strip()
    if not reference:
        raise _refusal("a reference names nothing", reference=text)
    attempts.append(f"path {reference!r}")
    path = Path(reference)
    if path.is_file():
        return dict(read(path).payload)
    attempts.append(f"shorthand {reference!r}")
    kind, separator, value = reference.partition(":")
    if separator and kind.strip() and value.strip():
        return {kind.strip(): value.strip()}
    attempts.append(f"name {reference!r}")
    return {"name": reference}


@dataclass(frozen=True)
class Applied:
    """The document a run actually used: the validated model, the overrides applied, and its identity payload.

    A run's manifest records this record, so the manifest names the document that produced the numbers rather
    than the one that was read before an override changed it.

    Attributes:
        model: The validated model, after every override.
        overrides: The ``configkit.overrides.Override`` records applied, in order. Typed loosely because
            ``document`` is below ``overrides`` in the dependency order and imports nothing from it.
        payload: ``roles.identity_of(model)``: the CONTENT fields a key hashes.
    """

    model: BaseModel
    overrides: tuple[Any, ...]
    payload: Mapping[str, Any]
