"""The shared types: the frozen spec bases, the container freezers and the one ``Brand`` parameter.

``FrozenSpec`` is the shape a config block should have (an unknown key refused, no container mutable), the two
option bases are where a consumer declares its own options instead of a ``**options: Any`` mapping (rcp-24), and
``Brand`` is the one parameter that fills schema ids, output prefixes and environment names (rcp-6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import Any

import pytest
from configkit.spec import (
    Brand,
    FrozenSpec,
    OperationOptions,
    OptionSchema,
    declared_along_mro,
    frozen_mapping,
    frozen_sequence,
)
from pydantic import ValidationError


class _Block(FrozenSpec):
    """A spec block a consumer would declare: one field and one free-form mapping."""

    name: str
    options: Mapping[str, Any] = {}


def test_frozen_mapping_freezes_at_every_depth() -> None:
    """A mapping becomes a read-only proxy and every nested container becomes immutable."""
    frozen = frozen_mapping({"a": {"b": [1, {"c": 2}]}})
    assert isinstance(frozen, MappingProxyType)
    with pytest.raises(TypeError):
        frozen["a"] = {}
    inner = frozen["a"]
    assert isinstance(inner, MappingProxyType)
    assert inner["b"] == (1, MappingProxyType({"c": 2}))


def test_frozen_sequence_freezes_at_every_depth() -> None:
    """A sequence becomes a tuple and every nested container becomes immutable."""
    assert frozen_sequence([1, [2], {"a": 3}]) == (1, (2,), MappingProxyType({"a": 3}))


def test_frozen_spec_refuses_an_unknown_key_and_assignment() -> None:
    """The block's shape: an unknown key is refused, and a validated block cannot be edited."""
    block = _Block(name="x")
    assert block.name == "x"
    with pytest.raises(ValidationError):
        _Block(name="x", unknown=1)
    with pytest.raises(ValidationError):
        block.name = "y"


def test_operation_options_and_option_schema_are_frozen_specs() -> None:
    """rcp-15 and rcp-24: both bases are frozen blocks, and an undeclared option is refused."""
    assert issubclass(OperationOptions, FrozenSpec)
    assert issubclass(OptionSchema, FrozenSpec)

    class _Options(OptionSchema):
        uri: str

    assert _Options(uri="a").uri == "a"
    with pytest.raises(ValidationError):
        _Options(uri="a", surprise=1)


def test_brand_is_frozen_and_names_its_three_uses() -> None:
    """rcp-6: one parameter, and assignment to it fails."""
    brand = Brand(name="p", prefix="P", schema_root="p.cli.v1", output_schema_prefix="p.out")
    assert brand.prefix == "P"
    assert brand.schema_root == "p.cli.v1"
    assert brand.output_schema_prefix == "p.out"
    with pytest.raises(FrozenInstanceError):
        brand.name = "q"


def test_a_model_inside_a_frozen_container_is_frozen_too() -> None:
    """L2: ``identity.freeze`` and ``spec._frozen`` were two walkers for one "immutable at every depth" rule."""
    from configkit.identity import freeze

    block = _Block(name="x", options={"a": [1]})
    frozen = frozen_mapping({"block": block})["block"]
    assert isinstance(frozen, MappingProxyType)
    assert frozen == freeze(block)
    assert frozen["name"] == "x"


def test_the_declaration_walk_reads_each_class_base_first() -> None:
    """M7: four independent walks of "the declared attribute merges down the MRO" became one reader."""
    from typing import ClassVar

    from configkit.roles import Role, roles_of

    class _BaseDeclared(FrozenSpec):
        IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"a": Role.CONTENT}

        a: str

    class _ChildDeclared(_BaseDeclared):
        IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"a": Role.RUNTIME, "b": Role.CONTENT}

        b: str

    pairs = declared_along_mro(_ChildDeclared, "IDENTITY_ROLES")
    assert [klass.__name__ for klass, _ in pairs] == ["_BaseDeclared", "_ChildDeclared"]
    assert [declared for _, declared in pairs] == [
        {"a": Role.CONTENT},
        {"a": Role.RUNTIME, "b": Role.CONTENT},
    ]
    assert roles_of(_ChildDeclared) == {"a": Role.RUNTIME, "b": Role.CONTENT}
