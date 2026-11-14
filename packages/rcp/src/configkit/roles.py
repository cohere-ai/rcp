"""The one role vocabulary, and the four behaviours that derive from it.

A config field's role is declared next to the field, as ``IDENTITY_ROLES`` on the model, because the failure this
prevents is *a field added without a decision*: a separate list of paths, secrets or local inputs drifts from the
models it describes. The seven roles are fixed -- a consumer declares one per field and does not choose the
vocabulary, because "identity" and "redaction" are only meaningful if every consumer means the same thing by
them.

``CONTENT``
    part of the identity; a change makes a different document.
``RUNTIME``
    affects how it runs, not what it is.
``PATH``
    resolved against the document's directory.
``URI``
    redacted in a report.
``SECRET``
    redacted, and never written to a log.
``ENV``
    a name, not a value.
``OUTPUT``
    written, never read.

Everything the library derives from the vocabulary is here: :func:`roles_of` (the declarations, merged down the
MRO), :func:`identity_of` (the CONTENT payload), :func:`path_fields` (the PATH leaves the loader resolves),
:func:`redact` (SECRET and URI values, for the recorded form) and :func:`local_inputs` (what a job on another
host cannot read). The module knows no field name: it reads what the model declares.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import UnionType
from typing import Any, Final, Union, get_args, get_origin

from pydantic import BaseModel

from configkit.faults import ValueErrorFault
from configkit.spec import declared_along_mro

__all__ = ["Role", "identity_of", "local_inputs", "path_fields", "redact", "roles_of", "safe_url"]

#: The class attribute a model declares its roles on, merged down the MRO.
_ROLES: Final[str] = "IDENTITY_ROLES"

#: The recorded form a ``SECRET`` value is replaced with: the marker ``rcp`` writes today, kept so a recorded
#: config reads the same before and after the move.
_REDACTED = "<redacted>"


class Role(StrEnum):
    """What a config field is for. The seven members are fixed; a consumer declares one per field."""

    CONTENT = "content"
    RUNTIME = "runtime"
    PATH = "path"
    URI = "uri"
    SECRET = "secret"
    ENV = "env"
    OUTPUT = "output"


def _declares_roles(model: type[BaseModel]) -> bool:
    """Whether *model* or one of its bases declares ``IDENTITY_ROLES`` at all.

    The one case a caller may read as "no roles": a model with no declaration declares no ``PATH`` leaf and no
    role, while a model with a *partial* declaration is refused by :func:`roles_of` and must not be read as
    neither -- that silence is how a ``PATH`` leaf stops resolving without a word.
    """
    return any(_ROLES in klass.__dict__ for klass in model.__mro__)


def roles_of(model: type[BaseModel]) -> Mapping[str, Role]:
    """The roles *model*'s fields declare, merged down its MRO.

    A subclass may add fields and may re-declare an inherited one; the most derived declaration wins, and a value
    written as a plain string is coerced to a :class:`Role` (the spelling the product's ``data/templates`` uses).

    Both directions are refused, because both are a decision nobody made: a field with no role, and a role naming
    a field the model no longer has.

    Args:
        model: The model class whose fields are being walked.

    Returns:
        The declared roles by field name.

    Raises:
        Fault: a declaration is not a mapping or names an unknown role, the model declares no roles at all, a
            field has no declared role, or a declaration names a field the model does not have. ``code`` is
            ``"value_error"``.
    """
    merged: dict[str, Role] = {}
    for klass, declared in declared_along_mro(model, _ROLES):
        if not isinstance(declared, Mapping):
            raise ValueErrorFault(f"{klass.__name__}.IDENTITY_ROLES must be a mapping of field name to Role")
        for name, role in declared.items():
            try:
                merged[name] = Role(role)
            except ValueError:
                raise ValueErrorFault(
                    f"{klass.__name__}.IDENTITY_ROLES[{name!r}] is not a Role: {role!r}; "
                    f"the roles are {', '.join(member.value for member in Role)}"
                ) from None
    problems: list[str] = []
    if not merged:
        problems.append("declares no IDENTITY_ROLES")
    fields = set(model.model_fields)
    undeclared = sorted(fields - set(merged))
    stale = sorted(set(merged) - fields)
    if undeclared:
        problems.append(f"undeclared field(s) {undeclared}: add each to {model.__name__}.IDENTITY_ROLES with its Role")
    if stale:
        problems.append(f"declared but absent field(s) {stale}: delete them from {model.__name__}.IDENTITY_ROLES")
    if problems:
        raise ValueErrorFault(f"{model.__name__}: " + "; ".join(problems))
    return merged


def identity_of(model: BaseModel) -> Mapping[str, Any]:
    """The CONTENT payload of *model*: what a key hashes, and the one definition of identity for three consumers.

    Four rules, and each one is load-bearing:

    1. a nested model recurses through the same rule, so a runtime knob cannot reach a key by being nested;
    2. a field whose value is ``None`` is omitted, so a new optional CONTENT field does not re-key every run that
       does not set it;
    3. the keys an ``extra="allow"`` model received are CONTENT (a needless re-key costs compute, a missed one
       costs numbers);
    4. :func:`roles_of` runs at every level before the walk, so an undeclared field inside a nested model is
       refused too.

    Args:
        model: The validated model to walk.

    Returns:
        The CONTENT values by field name, with nested models recursed and ``None`` omitted.

    Raises:
        Fault: a model in the graph has an incomplete ``IDENTITY_ROLES`` declaration (see :func:`roles_of`).
    """
    roles = roles_of(type(model))
    payload: dict[str, Any] = {}
    for name in type(model).model_fields:
        if roles[name] is not Role.CONTENT:
            continue
        value = getattr(model, name, None)
        if value is None:
            continue
        payload[name] = _content(value)
    for name, value in (model.__pydantic_extra__ or {}).items():
        if value is not None:
            payload[name] = _content(value)
    return payload


def _content(value: Any) -> Any:
    """*value* with nested models walked by :func:`identity_of` and mappings and sequences recursed."""
    if isinstance(value, BaseModel):
        return identity_of(value)
    if isinstance(value, Mapping):
        return {key: _content(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    return value


def path_fields(model: type[BaseModel]) -> tuple[tuple[str, ...], ...]:
    """The dotted paths of every ``Role.PATH`` leaf in *model*, a nested model's leaves included.

    The loader resolves a path against the document's directory, so it needs the leaves before it validates;
    this is the one enumeration of them (a hand-kept tuple misses a field, which is how ``emb-train``'s
    ``model.checkpoint`` never resolved). A path through a list or mapping of models applies to every element.

    Args:
        model: The model class whose PATH leaves are wanted.

    Returns:
        One tuple per PATH leaf, in declaration order, each the field names from *model* down to the leaf.
    """
    roles = roles_of(model)
    paths: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        if roles[name] is Role.PATH:
            paths.append((name,))
            continue
        nested = _nested_model(field.annotation)
        if nested is not None:
            paths.extend((name, *tail) for tail in path_fields(nested))
    return tuple(paths)


def redact(payload: Any, *, model: type[BaseModel]) -> Any:
    """*payload* with every SECRET and URI value redacted, walked by *model*'s declared roles.

    *payload* is a dump of *model* (``model.model_dump(...)``, whatever options the caller chose), and each
    field's key is found by the spelling a dump writes (its serialisation alias). A ``SECRET`` value becomes the
    recorded marker ``"<redacted>"``; a ``URI`` value loses its userinfo, query and fragment while the scheme,
    authority and path stay, so a redacted record still names its store. A nested model is walked by its own
    declarations, and a key the model does not declare is redacted too: an ``extra="allow"`` key is CONTENT to
    :func:`identity_of`, so it may hold a secret, and "no declared role" is not "no secret".

    Args:
        payload: A dump of *model*, or a sub-tree of one (a nested model's dump, a list of dumps).
        model: The model class *payload* was dumped from; it supplies the roles and the aliases.

    Returns:
        A new payload; *payload* is not mutated.
    """
    if not isinstance(payload, Mapping):
        return payload
    roles = roles_of(model)
    redacted = dict(payload)
    declared: set[str] = set()
    for name, field in model.model_fields.items():
        key = field.serialization_alias or field.alias or name
        declared.add(key)
        if key not in redacted or redacted[key] is None:
            continue
        value = redacted[key]
        role = roles[name]
        if role is Role.SECRET:
            redacted[key] = _REDACTED
        elif role is Role.URI:
            redacted[key] = safe_url(value)
        else:
            nested = _model_of(field.annotation)
            if nested is not None:
                redacted[key] = redact(value, model=nested)
            elif _nested_model(field.annotation) is not None:
                redacted[key] = _redact_nested(value, field.annotation)
    for key, value in redacted.items():
        if key not in declared and value is not None:
            redacted[key] = _REDACTED
    return redacted


def _redact_nested(value: Any, annotation: Any) -> Any:
    """*value* (a list or mapping of a nested model's dumps) with each dump redacted by the model it holds."""
    if isinstance(value, Mapping):
        inner = get_args(annotation)[-1] if get_args(annotation) else Any
        return {key: _redact_nested(item, inner) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        inner = get_args(annotation)[0] if get_args(annotation) else Any
        return [_redact_nested(item, inner) for item in value]
    nested = _model_of(annotation)
    return redact(value, model=nested) if nested is not None else value


def local_inputs(model: BaseModel) -> tuple[str, ...]:
    """``"<field path>: <location>"`` of every input *model* needs from this host.

    A job whose runner does not share this host's files (a remote cluster) cannot read them, so this listing is
    what a submitter checks before sending the job. A ``PATH`` field is a local input by definition; a ``URI``
    field is one when it names no remote scheme (a bare path, or ``file://``); an ``ENV`` field is listed as the
    variable name it declares (``$NAME``) -- the job host must set it, and its value is never read here. A list
    of locations is one row per element, indexed.

    Args:
        model: The validated config to list.

    Returns:
        The rows in declaration order, each ``"<field path>: <location>"``; a nested model's rows are prefixed
        with its field path.
    """
    rows: list[str] = []
    _collect_inputs(model, prefix="", rows=rows)
    return tuple(rows)


def _collect_inputs(model: BaseModel, *, prefix: str, rows: list[str]) -> None:
    """Append *model*'s local-input rows to *rows*, recursing into a nested model under *prefix*."""
    roles = roles_of(type(model))
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        value = getattr(model, name, None)
        if value is None:
            continue
        role = roles[name]
        if role is Role.PATH:
            _append_locations(rows, path, value, local=True)
        elif role is Role.URI:
            _append_locations(rows, path, value, local=False)
        elif role is Role.ENV and isinstance(value, str):
            rows.append(f"{path}: ${value}")
        elif role is not Role.SECRET and isinstance(value, BaseModel):
            _collect_inputs(value, prefix=f"{path}.", rows=rows)


def _append_locations(rows: list[str], path: str, value: Any, *, local: bool) -> None:
    """Append one row per location in *value*: a PATH is local, a URI only when it names no remote scheme."""
    if isinstance(value, str):
        if local or not _is_remote(value):
            rows.append(f"{path}: {value}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _append_locations(rows, f"{path}[{index}]", item, local=local)


def _is_remote(location: str) -> bool:
    """Whether *location* names a scheme other than ``file`` (a bare path is local)."""
    scheme, separator, _ = location.partition("://")
    return bool(separator) and scheme.lower() != "file"


def safe_url(value: Any) -> Any:
    """*value* with each URL's userinfo, query and fragment stripped; a list is stripped elementwise.

    The one URL-redaction rule (rcp's ``redact_urls`` is the other half of it): a URL that reaches a log or a
    recorded payload names its store without carrying a credential. A value that is not an authority-bearing URL
    (a bare path) is returned unchanged, and a list is stripped elementwise.

    Args:
        value: A URL string, a list of them, or any other value (returned unchanged).

    Returns:
        ``<scheme>://<host[:port]>[/<path>]`` for a URL, elementwise for a list, *value* itself otherwise.
    """
    if isinstance(value, str):
        return _safe_url_string(value)
    if isinstance(value, (list, tuple)):
        return [safe_url(item) for item in value]
    return value


def _safe_url_string(url: str) -> str:
    """``<scheme>://<host[:port]>[/<path>]``: the form of a URL that may reach a log or a record."""
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url  # not an authority-bearing URL: nothing to strip
    rest = rest.partition("#")[0].partition("?")[0]
    authority, _, path = rest.partition("/")
    authority = authority[authority.rfind("@") + 1 :]
    return f"{scheme}://{authority}/{path}" if path else f"{scheme}://{authority}"


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The :class:`BaseModel` *annotation* names directly or through ``X | None``; ``None`` for any other shape."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, UnionType):
        for argument in get_args(annotation):
            found = _model_of(argument)
            if found is not None:
                return found
    return None


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """The first :class:`BaseModel` *annotation* names at any depth (a list or mapping of models included)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in get_args(annotation):
        found = _nested_model(argument)
        if found is not None:
            return found
    return None
