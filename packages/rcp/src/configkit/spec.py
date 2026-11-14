"""The shared types: the frozen spec bases, the container freezers and the one ``Brand`` parameter.

``FrozenSpec`` is the shape a config block should have -- an unknown key is refused and no container is mutable
-- and ``OperationOptions``/``OptionSchema`` are the two bases a consumer subclasses so that a command's bounds
(rcp-15) and a reader's or writer's options (rcp-24) live in a declared model instead of a ``**options: Any``
mapping. ``frozen_mapping`` and ``frozen_sequence`` are the container freezers a consumer wires with pydantic's
``AfterValidator`` (no base here uses them). ``Brand`` is the one parameter that fills schema ids, generated
output-schema prefixes and environment-variable names (rcp-6), so no consumer's name is baked into the library.

A record type comes from a types module, not a service (rcp-50): this module reads the standard library and
``pydantic`` only, and imports no consumer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "Brand",
    "FrozenSpec",
    "OperationOptions",
    "OptionSchema",
    "declared_along_mro",
    "frozen_mapping",
    "frozen_sequence",
]


def _frozen(value: Any) -> Any:
    """*value* with every container frozen, at every depth: the one freeze walk (a model's fields included).

    A model becomes a read-only mapping of its fields (its extras too), a mapping a
    :class:`types.MappingProxyType`, and a list or tuple a tuple. :func:`configkit.identity.freeze` is this
    walk's public name; ``frozen_mapping``/``frozen_sequence`` are its two typed doors.
    """
    if isinstance(value, BaseModel):
        fields = {name: _frozen(getattr(value, name)) for name in type(value).model_fields}
        fields.update({name: _frozen(item) for name, item in (value.__pydantic_extra__ or {}).items()})
        return MappingProxyType(fields)
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def declared_along_mro(model: type[BaseModel], attribute: str) -> tuple[tuple[type[BaseModel], Any], ...]:
    """The declarations *model*'s classes make for *attribute*, base class first: the one walk of the MRO rule.

    A declaration is read from the class's own ``__dict__``, so an inherited value is not re-declared and a
    caller that merges gives the most derived declaration the last word. Four readers shared this rule -- the
    role walk (:func:`configkit.roles.roles_of`), the audit's role and warning checks, and the warning channel
    (:func:`configkit.faults.warn`) -- and each carried its own copy, so a change to one silently diverged from
    its twin. The walk is here; the policy (raise, report, merge) stays with each caller.

    Args:
        model: The model class whose MRO is walked.
        attribute: The class attribute's name (``IDENTITY_ROLES``, ``WARNING_CODES``).

    Returns:
        One ``(class, declared value)`` pair per class that declares *attribute*, in MRO order (base first).
    """
    return tuple((klass, klass.__dict__[attribute]) for klass in reversed(model.__mro__) if attribute in klass.__dict__)


def frozen_mapping(value: Mapping[str, Any]) -> MappingProxyType[str, Any]:
    """*value* with every container replaced by an immutable one, recursively.

    A mapping becomes a :class:`types.MappingProxyType` and a list or a tuple becomes a tuple, at every depth, so
    a spec a caller mutates after validation cannot hold a value the model refuses. Use it as pydantic's
    ``AfterValidator`` for a free-form mapping field.

    Args:
        value: The mapping to freeze.

    Returns:
        A read-only mapping whose nested containers are read-only mappings or tuples.
    """
    return MappingProxyType({key: _frozen(item) for key, item in value.items()})


def frozen_sequence(value: Sequence[Any]) -> tuple[Any, ...]:
    """*value* as a tuple with every container replaced by an immutable one, recursively.

    Args:
        value: The sequence to freeze.

    Returns:
        A tuple whose nested containers are read-only mappings or tuples.
    """
    return tuple(_frozen(item) for item in value)


class FrozenSpec(BaseModel):
    """A spec block: an unknown key is refused and no container is mutable.

    ``validate_default`` runs the freeze over a default too, so a block a caller never filled in still holds a
    frozen container, and ``validate_by_name`` lets a consumer name a field by its own name when it also declares
    an alias for the document's spelling.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_by_name=True, validate_default=True)


class OperationOptions(FrozenSpec):
    """A command's own option model: the defaults and bounds of one operation, declared beside it (rcp-15)."""


class OptionSchema(FrozenSpec):
    """A reader's or writer's declared option schema: an unknown option is refused, never silently ignored (rcp-24)."""


@dataclass(frozen=True)
class Brand:
    """The one parameter that fills schema ids, output-schema prefixes and environment-variable names.

    Attributes:
        name: The program name (``"rcp"``).
        prefix: The environment-variable prefix, upper case (``"RCP"``).
        schema_root: The schema-id root (``"rcp.cli.v1"``).
        output_schema_prefix: The generated output-schema prefix (``"rcp.run"``).
    """

    name: str
    prefix: str
    schema_root: str
    output_schema_prefix: str
