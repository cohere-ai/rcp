"""One ``KEY=VALUE`` grammar, one value ladder, one error table, one precedence -- and the pipeline's order.

An override is a structured edit of a document: a dotted path to one leaf, the value already parsed, and the
source it came from. There is one value parser (:func:`parse_value`) and it is the grammar ``rcp`` ships today
(``support/config.py:69``), so no existing config changes meaning: an empty value is the empty string, every
other value is a YAML literal (``5`` an int, ``yes`` a boolean, ``[a, b]`` a list). There is one error table
(:data:`VALUE_ERRORS`) naming each refusal with its code and its message template, and one precedence
(``file < env < flag``) with the winning value's source on the record.

:func:`apply` returns a new model through ``model_copy``, because a frozen spec is the shape a config should
have, and it resolves a ``PATH`` override against the base its caller names -- the second of the library's two
bases, never the process's working directory and never the document's.

:func:`pipeline` is the one call a run uses, and its stage order is the contract's:
``read -> resolve -> apply -> validate -> identity``. ``apply`` runs **before** ``validate`` because an override
written after validation enters the model unvalidated, and **before** the identity because one written after
``identity_of`` reuses the cache digest computed without it -- the failure the order exists to prevent. The
dependency is this module above :mod:`configkit.document`, never the reverse: ``read`` never applies an override.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Final, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from configkit.document import (
    _YAML_HINT,
    Applied,
    _resolve_paths,
    deep_merge,
    leaf_paths,
    read,
    resolve,
    same_value,
    validate_or_fault,
)
from configkit.faults import Fault, require
from configkit.roles import Role, _model_of, _roles_of_or_none, identity_of

__all__ = [
    "Override",
    "parse",
    "parse_value",
    "from_env",
    "apply",
    "pipeline",
    "merge_layers",
    "unset",
    "VALUE_ERRORS",
]

#: One entry per refusal: the fault code, the message template the raise formats with its fields, and the next
#: step a caller can take. The table is data, so a consumer's test can read it and ``audit`` can check every code
#: against the fault table. Every refusal here is a value problem, so every code is the generic one: a consumer's
#: exit-code table needs no row per message. The hint is static: the message names the path, the variable and the
#: leaves, and the hint names the fix.
VALUE_ERRORS: Final[Mapping[str, tuple[str, str, str]]] = MappingProxyType(
    {
        "not_a_pair": (
            "value_error",
            "override {text!r} is not KEY=VALUE; the form is key.path=VALUE",
            "write key.path=VALUE, for example judge.concurrency=4",
        ),
        "repeated_path": (
            "value_error",
            "the override {path} is given twice with two values ({first!r} and {second!r}); one path has one value",
            "give one path one value; a different value belongs in a later layer",
        ),
        "value_not_parsed": (
            "value_error",
            "the override value {text!r} is not a YAML literal: {problem}",
            "write a YAML literal: 5, 1.5, true, null, [a, b] or {k: v}",
        ),
        "not_a_leaf": (
            "value_error",
            "{path} names no leaf of {model}; the leaves are {known}",
            "name a leaf the model declares; the message lists them",
        ),
        "not_a_mapping": (
            "value_error",
            "{path} runs through {head}, which is not a model; an override names a leaf",
            "an override names a leaf; a path may only run through a model",
        ),
        "no_default": (
            "value_error",
            "{path} is required and declares no default; unset cannot remove it",
            "give the field a default, or leave it set",
        ),
        "no_base": (
            "value_error",
            "an override resolves its PATH values against a base the caller names; pipeline got overrides and no base",
            "pass base= to pipeline: an override's PATH values resolve against the caller's base",
        ),
        "no_prefix": (
            "value_error",
            "an environment prefix is required; an empty prefix would read every variable",
            "pass the consumer's prefix (Brand.prefix); an empty one would read every variable",
        ),
        "unknown_env": (
            "value_error",
            "{name} is under the prefix {prefix}_ and names no leaf of {model}; the names are {known}",
            "rename or unset the variable, or declare the leaf the model reads",
        ),
        "content_conflict": (
            "value_error",
            "{path} is CONTENT and the two layers set it differently ({base!r} and {overlay!r}); "
            "one document has one content value",
            "make the two layers agree on the CONTENT value, or change the field's Role",
        ),
        "no_role": (
            "value_error",
            "{path} declares no Role, so the layers cannot say whether it may differ",
            "declare the field's Role in the model's IDENTITY_ROLES",
        ),
    }
)


def _refusal(key: str, **fields: Any) -> Fault:
    """The fault *key* names: its code, message template and hint come from :data:`VALUE_ERRORS`."""
    code, template, hint = VALUE_ERRORS[key]
    return Fault(template.format(**fields), code=code, details={"refusal": key, **fields}, hint=hint)


def _leaves(model: type[BaseModel], prefix: tuple[str, ...]) -> str:
    """The dotted leaves *model* declares, under *prefix*: the "known leaves" a refusal names."""
    return ", ".join(".".join((*prefix, leaf)) for leaf in leaf_paths(model))


def _role_of(model: type[BaseModel], name: str) -> Role | None:
    """The role *name* declares in *model*, or ``None`` when the model declares no ``IDENTITY_ROLES``.

    A *partial* declaration is not "no declaration": :func:`roles_of` refuses it and the refusal propagates,
    because reading it as ``None`` writes a ``PATH`` override unresolved and silently -- a wrong value in the
    identity rather than a refusal.
    """
    roles = _roles_of_or_none(model)
    return None if roles is None else roles.get(name)


@dataclass(frozen=True)
class Override:
    """One structured edit of a document: the leaf it writes, the parsed value, and where the value came from.

    Attributes:
        path: The dotted path of the model's own leaf, by field name (``judge.concurrency``).
        value: The value, already parsed by :func:`parse_value`; its type is the ladder's (``int``, ``bool``,
            ``list``, ``dict``, ``str``).
        source: Where the value came from: ``"--set"``, ``"env:<NAME>"`` or ``"file:<name>"``.
    """

    path: str
    value: Any
    source: str


def parse_value(text: str, *, yaml_hint: str = _YAML_HINT) -> Any:
    """Parse one override value with the one grammar: ``yaml.safe_load(text) if text.strip() else ""``.

    The YAML reader is imported on first use, so a caller that passes no non-empty override never imports it.

    Args:
        text: The value as the caller wrote it (the part after the ``=``).
        yaml_hint: The install line a missing YAML reader reports, verbatim.

    Returns:
        ``""`` for an empty or whitespace-only *text*; otherwise the YAML literal: ``None`` for ``null``/``~``,
        an ``int`` for ``5``, a ``float`` for ``5.0``, a ``bool`` for ``true``/``yes``/``no``/``on``/``off``, a
        ``list`` for ``[a, b]``, a ``dict`` for ``{k: v}``, and the text itself for any other word.

    Raises:
        Fault: the text is not a YAML literal (``value_error``), or the YAML reader is absent
            (``optional.missing``, with *yaml_hint*).
    """
    if not text.strip():
        return ""
    yaml = require("yaml", hint=yaml_hint)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _refusal("value_not_parsed", text=text, problem=str(exc)) from exc


def parse(pairs: Sequence[str]) -> tuple[Override, ...]:
    """Parse ``key.path=VALUE`` pairs into override records, all sourced ``"--set"``.

    Args:
        pairs: The ``--set`` values as written, each ``key.path=VALUE``.

    Returns:
        One record per distinct path, in the order given.

    Raises:
        Fault: a pair has no ``=`` or no key (``not_a_pair``); one path is given twice with two different values
            (``repeated_path``) -- a repeated path with the same value is one record; or a value does not parse
            (see :func:`parse_value`).
    """
    overrides: list[Override] = []
    seen: dict[str, Override] = {}
    for text in pairs:
        key, separator, raw = text.partition("=")
        path = key.strip()
        if not separator or not path:
            raise _refusal("not_a_pair", text=text)
        override = Override(path=path, value=parse_value(raw), source="--set")
        previous = seen.get(path)
        if previous is None:
            seen[path] = override
            overrides.append(override)
        elif not same_value(previous.value, override.value):
            raise _refusal("repeated_path", path=path, first=previous.value, second=override.value)
    return tuple(overrides)


def from_env(
    environ: Mapping[str, str],
    *,
    prefix: str,
    model: type[BaseModel],
) -> tuple[Override, ...]:
    """Read the environment names under one prefix into override records, all sourced ``"env:<NAME>"``.

    A name is the model's leaf path upper-cased with dots read as underscores, under ``<prefix>_``: the leaf
    ``judge.concurrency`` of the model ``_Run`` is read from ``P_JUDGE_CONCURRENCY`` when the prefix is ``P``.
    A name under the prefix that maps to no leaf is refused, because a typo in an environment variable is
    otherwise a value nobody sets.

    Args:
        environ: The environment mapping to read (``os.environ`` at a call site, a dict in a test).
        prefix: The namespace, upper case (``Brand.prefix``), with no trailing underscore.
        model: The model class whose leaves name the variables.

    Returns:
        One record per variable that is set, in the model's leaf order.

    Raises:
        Fault: *prefix* is empty (``no_prefix``); a name under the prefix maps to no leaf (``unknown_env``); or a
            value does not parse (see :func:`parse_value`).
    """
    if not prefix.strip():
        raise _refusal("no_prefix")
    namespace = f"{prefix}_"
    names = {f"{namespace}{leaf.upper().replace('.', '_')}": leaf for leaf in leaf_paths(model)}
    unknown = sorted(name for name in environ if name.startswith(namespace) and name not in names)
    if unknown:
        raise _refusal(
            "unknown_env",
            name=unknown[0],
            prefix=prefix,
            model=model.__name__,
            known=", ".join(sorted(names)),
        )
    return tuple(
        Override(path=names[name], value=parse_value(environ[name]), source=f"env:{name}")
        for name in names
        if name in environ
    )


def _descend(annotation: Any) -> tuple[str, type[BaseModel]] | None:
    """How a dotted path runs through a field: the model it reaches, and the container that model sits in.

    One classification for both ends of the grammar. :func:`configkit.document.leaf_paths` and :func:`from_env`
    enumerate the leaves of a nested model, a list of models and a mapping of models alike, so :func:`_write`
    must write all three: otherwise an environment name the library itself produced ("``judges.concurrency``")
    is refused by the writer as "``judges`` runs through ``judges``, which is not a model".

    Returns:
        ``("one", Model)`` for a nested model, ``("list", Model)`` for a sequence of models, ``("mapping",
        Model)`` for a mapping of models, or ``None`` when the field is a leaf.
    """
    direct = _model_of(annotation)
    if direct is not None:
        return ("one", direct)
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for argument in get_args(annotation):
            found = _descend(argument)
            if found is not None:
                return found
        return None
    if origin in (list, tuple, set, frozenset):
        inner = get_args(annotation)[0] if get_args(annotation) else Any
        nested = _model_of(inner)
        return ("list", nested) if nested is not None else None
    if origin in (dict, Mapping):
        inner = get_args(annotation)[-1] if get_args(annotation) else Any
        nested = _model_of(inner)
        return ("mapping", nested) if nested is not None else None
    return None


def _each(node: Any, kind: str, apply: Callable[[Any], Any]) -> Any:
    """*node* -- the model, list or mapping a field holds -- with *apply* run on each model it holds."""
    if kind == "list":
        return [apply(item if item is not None else {}) for item in (node or ())]
    if kind == "mapping":
        return {key: apply(item if item is not None else {}) for key, item in (node or {}).items()}
    return apply(node if node is not None else {})


def _write(
    node: Any,
    model: type[BaseModel],
    parts: Sequence[str],
    value: Any,
    *,
    base: Path,
    path: str,
    prefix: tuple[str, ...] = (),
    source: str | None = None,
) -> Any:
    """*node* -- a model, or the mapping a draft holds in its place -- with the dotted *parts* written to *value*.

    A leaf whose declared role is ``PATH`` resolves against *base*; every other leaf takes the value verbatim,
    because ``apply`` writes and re-validates nothing.
    """
    head, rest = parts[0], parts[1:]
    field = model.model_fields.get(head)
    if field is None:
        raise _refusal("not_a_leaf", path=path, model=model.__name__, known=_leaves(model, prefix), source=source)
    if not rest:
        written = _resolve_paths(value, base) if _role_of(model, head) is Role.PATH else value
    else:
        shape = _descend(field.annotation)
        if shape is None:
            raise _refusal("not_a_mapping", path=path, head=head, source=source)
        kind, nested = shape
        child = getattr(node, head, None) if isinstance(node, BaseModel) else node.get(head)
        written = _each(
            child,
            kind,
            lambda item: _write(
                item,
                nested,
                rest,
                value,
                base=base,
                path=path,
                prefix=(*prefix, head),
                source=source,
            ),
        )
    if isinstance(node, BaseModel):
        return node.model_copy(update={head: written})
    return {**node, head: written}


def apply[ModelT: BaseModel](
    draft: ModelT,
    overrides: Sequence[Override],
    *,
    base: Path,
) -> ModelT:
    """Write *overrides* into *draft* in order, returning a new model and validating nothing.

    The draft is not changed: each write returns a copy (``model_copy``), because a consumer's frozen spec is the
    shape a config should have. A later record naming a path a earlier one already set wins, which is what the
    ``file < env < flag`` precedence needs; :func:`parse` refuses two different values for one path inside one
    layer.

    Args:
        draft: The unvalidated draft :func:`configkit.document.resolve` built, or a validated model.
        overrides: The records to write, in precedence order (the last write to a path wins).
        base: The directory a ``PATH`` override resolves against -- the caller names it, and it is required:
            a default would be the ambiguity that produced two identities for one run.

    Returns:
        A new model with every override written; *draft* is unchanged.

    Raises:
        Fault: a path names no leaf of the model (``not_a_leaf``, with the path and the known leaves), runs
            through a field that is not a model (``not_a_mapping``), or the model's ``IDENTITY_ROLES`` declaration
            is malformed (see :func:`configkit.roles.roles_of`).
    """
    updated = draft
    for override in overrides:
        updated = _write(
            updated,
            type(draft),
            override.path.split("."),
            override.value,
            base=base,
            path=override.path,
            source=override.source,
        )
    return updated


def _clear(
    node: Any,
    model: type[BaseModel],
    parts: Sequence[str],
    *,
    path: str,
    prefix: tuple[str, ...] = (),
    source: str | None = None,
) -> Any:
    """*node* with the dotted *parts* removed: a key dropped from a mapping, a model field set to its default."""
    head, rest = parts[0], parts[1:]
    field = model.model_fields.get(head)
    if field is None:
        raise _refusal("not_a_leaf", path=path, model=model.__name__, known=_leaves(model, prefix), source=source)
    if not rest:
        if isinstance(node, Mapping):
            return {key: item for key, item in node.items() if key != head}
        if field.is_required():
            raise _refusal("no_default", path=path, source=source)
        return node.model_copy(update={head: field.get_default(call_default_factory=True)})
    shape = _descend(field.annotation)
    if shape is None:
        raise _refusal("not_a_mapping", path=path, head=head, source=source)
    kind, nested = shape
    child = getattr(node, head, None) if isinstance(node, BaseModel) else node.get(head)
    if child is None:
        raise _refusal("not_a_leaf", path=path, model=model.__name__, known=_leaves(model, prefix), source=source)

    def clear(item: Any) -> Any:
        return _clear(item, nested, rest, path=path, prefix=(*prefix, head), source=source)

    cleared = _each(child, kind, clear)
    if isinstance(node, BaseModel):
        return node.model_copy(update={head: cleared})
    return {**node, head: cleared}


def unset[ModelT: BaseModel](draft: ModelT, path: str) -> ModelT:
    """Remove the leaf *path* names, returning a new model and changing *draft* not at all.

    A removal is the one structured edit the ``key.path=VALUE`` grammar cannot express. A model field cannot be
    absent, so removing one sets it to its declared default; a required field with no default is refused, because
    a model that dropped it could not be validated. A draft whose nested block is still a mapping loses the key.

    Args:
        draft: The draft or model to remove the leaf from.
        path: The dotted path of the leaf, by field name.

    Returns:
        A new model without the value.

    Raises:
        Fault: the path names no leaf (``not_a_leaf``), runs through a field that is not a model
            (``not_a_mapping``), or names a required field with no default (``no_default``).
    """
    return _clear(draft, type(draft), path.split("."), path=path)


def merge_layers(
    base: Mapping[str, Any],
    overlay: Mapping[str, Any],
    *,
    roles: Mapping[str, Role],
) -> Mapping[str, Any]:
    """Merge two layers of one document, refusing a ``CONTENT`` field the layers set differently (rcp 23).

    A field's role decides whether two layers may disagree: a ``CONTENT`` value is part of what the document
    *is*, so two different values are two documents and the merge is refused; any other role may be overridden,
    because it does not enter the identity. A field with no declared role is refused too: a malformed declaration
    is an error, not "no declaration".

    Args:
        base: The lower layer.
        overlay: The upper layer.
        roles: The role each field declares, by field name (``roles.roles_of(model)``); a dotted path is judged by
            its first segment.

    Returns:
        A new mapping; neither layer is changed.

    Raises:
        Fault: two layers set one ``CONTENT`` field differently (``content_conflict``, with the path and both
            values), or a differing field declares no role (``no_role``).
    """

    def refuse(path: tuple[str, ...], inherited: Any, value: Any) -> None:
        """A differing leaf is refused when its role forbids the difference."""
        role = roles.get(path[0])
        if role is None:
            raise _refusal("no_role", path=".".join(path))
        if role is Role.CONTENT:
            raise _refusal("content_conflict", path=".".join(path), base=inherited, overlay=value)

    return deep_merge(base, overlay, on_diff=refuse)


def pipeline[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
    *,
    base: Path | None = None,
    overrides: Sequence[Override] = (),
    schema_id: str | None = None,
    format: Literal["yaml", "json"] | None = None,
    path_fields: Sequence[Sequence[str]] = (),
) -> Applied:
    """Run the contract's five stages in its order: ``read -> resolve -> apply -> validate -> identity``.

    This is the one call a run uses. ``read`` parses the document, ``resolve`` builds the draft and resolves the
    ``PATH`` leaves against the document's directory, ``apply`` writes the overrides against *base*, ``validate``
    is the only stage that runs pydantic, and ``identity`` is the CONTENT payload the run's key hashes.

    A caller with no overrides gets the same model :func:`configkit.document.load` returns, and its payload is
    ``roles.identity_of`` of it. A caller with overrides must name *base*: the document's directory is the
    document layer's base and is not the override layer's.

    Args:
        path: The document to read.
        model: The model class to build.
        base: The directory the overrides' ``PATH`` values resolve against; required as soon as *overrides* is
            not empty.
        overrides: The records to apply, in precedence order.
        schema_id: The id the document's ``schema`` key must equal, or ``None`` for no check.
        format: The parser to use for *path* and every file it extends, or ``None`` to dispatch on the suffix
            (see :func:`configkit.document.read`).
        path_fields: Extra dotted rows :func:`configkit.document.resolve` resolves, over the declared ``PATH``
            leaves.

    Returns:
        The applied record: the validated model, the overrides in the order applied, and the identity payload.

    Raises:
        Fault: *overrides* is not empty and *base* is ``None`` (``no_base``); reading refuses the document (see
            :func:`configkit.document.read`); an override names no leaf (see :func:`apply`); or validation failed
            (one :func:`configkit.document.config_error` fault naming the field).
    """
    if overrides and base is None:
        raise _refusal("no_base")
    document = read(path, schema_id=schema_id, format=format)
    draft = resolve(document, model, rows=path_fields)
    if overrides and base is not None:
        draft = apply(draft, overrides, base=base)
    validated = validate_or_fault(
        draft,
        model=model,
        source=str(path),
        overrides=[f"{override.path}={override.value!r}" for override in overrides],
    )
    return Applied(model=validated, overrides=tuple(overrides), payload=identity_of(validated))
