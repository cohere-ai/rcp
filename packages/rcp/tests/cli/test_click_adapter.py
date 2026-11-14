"""The click adapter: the library's option specs rendered as click parameters, and the one ``--json`` flag.

The flag derivation is :func:`configkit.commands.option_specs`'s (one source for the CLI, the MCP tool and the
command tree); this module's tests pin what the click front end adds -- the ``--x/--no-x`` form of a switch, the
positional spelling, the env-var and hidden declarations, and ``--json`` as a per-leaf flag.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import click
import pytest
from pydantic import BaseModel, Field

from rcp.cli.click_adapter import JSON_FLAG, json_option, option_params
from rcp.cli.introspect import walk


class _Kind(Enum):
    """An enum field renders as a closed set, like a ``Literal``."""

    A = "a"
    B = "b"


class _Request(BaseModel):
    """One field of every kind the front end renders."""

    name: str = Field(description="The name.")
    count: int = Field(default=3, description="How many.")
    ratio: float = 0.5
    switch: bool = False
    enabled: bool = True
    kinds: list[str] = Field(default_factory=list)
    kind: Literal["a", "b"] = "a"
    flavor: _Kind = _Kind.A
    where: Path = Path("here")
    positional: str | None = None


def _by_name(params: list[click.Parameter]) -> dict[str, click.Parameter]:
    return {param.name: param for param in params if param.name is not None}


def test_one_option_per_request_field_in_declaration_order() -> None:
    params = option_params(_Request)
    assert [param.name for param in params] == list(_Request.model_fields)


def test_a_scalar_field_renders_its_click_type_default_and_help() -> None:
    params = _by_name(option_params(_Request))
    assert isinstance(params["name"], click.Option)
    assert params["name"].opts == ["--name"] and params["name"].required
    assert params["count"].type is click.INT and params["count"].default == 3
    assert params["ratio"].type is click.FLOAT and params["ratio"].default == 0.5
    assert params["where"].type.name == "path"
    assert params["name"].help == "The name." and params["count"].help == "How many."


def test_a_switch_renders_the_negative_form_only_when_its_default_is_true() -> None:
    params = _by_name(option_params(_Request))
    assert params["switch"].opts == ["--switch"] and params["switch"].secondary_opts == []
    assert params["switch"].is_flag and params["switch"].default is False
    assert params["enabled"].opts == ["--enabled"] and params["enabled"].secondary_opts == ["--no-enabled"]
    assert params["enabled"].default is True


def test_a_list_a_literal_and_an_enum_are_repeatable_and_closed() -> None:
    params = _by_name(option_params(_Request))
    assert params["kinds"].multiple and params["kinds"].default == ()
    assert isinstance(params["kind"].type, click.Choice) and list(params["kind"].type.choices) == ["a", "b"]
    assert isinstance(params["flavor"].type, click.Choice) and list(params["flavor"].type.choices) == ["a", "b"]


def test_a_positional_envvar_and_hidden_field_are_declared_where_the_caller_names_them() -> None:
    params = _by_name(
        option_params(_Request, positional=("positional",), envvars={"count": "RCP_NDCG_COUNT"}, hidden=("ratio",))
    )
    assert isinstance(params["positional"], click.Argument)
    assert params["positional"].opts == ["positional"]
    assert params["count"].envvar == "RCP_NDCG_COUNT" and params["count"].show_envvar
    assert params["ratio"].hidden


def test_a_field_the_library_cannot_render_is_refused_naming_the_field() -> None:
    class _Mapping(BaseModel):
        options: dict[str, str] = Field(default_factory=dict)

    with pytest.raises(TypeError, match="options"):
        option_params(_Mapping)


def test_json_is_a_per_leaf_flag_and_never_the_roots() -> None:
    """36 of the 37 leaves declare it: `mcp serve` speaks stdio and prints no envelope, and no group has it."""
    tree = list(walk())
    leaves = [path for path, cmd in tree if not isinstance(cmd, click.Group)]
    declaring = [path for path, cmd in tree if any(getattr(param, "name", None) == "as_json" for param in cmd.params)]
    assert len(leaves) == 37 and len(declaring) == 36, declaring
    assert set(leaves) - set(declaring) == {"rcp mcp serve"}
    for path, cmd in tree:
        if isinstance(cmd, click.Group):
            assert not [param for param in cmd.params if getattr(param, "name", None) == "as_json"], path
        elif path in declaring:
            (param,) = [param for param in cmd.params if getattr(param, "name", None) == "as_json"]
            assert param.opts == [JSON_FLAG] and param.is_flag, path
    assert json_option().opts == [JSON_FLAG]
