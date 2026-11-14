"""Rendering a command's declared options as click parameters: one flag derivation, one front end.

A command's flags are derived once, by :func:`configkit.commands.option_specs`, so the command line, the MCP
tool's input schema and the command tree read one declaration. This module is the click renderer: each
:class:`~configkit.commands.OptionSpec` becomes the click parameter the front end runs, and the one ``--json``
flag is declared here. What the renderer adds is click's own vocabulary -- the ``--x/--no-x`` form of a switch, a
positional argument's spelling, an env-var fallback and a hidden debug flag -- which is front-end policy, not
derivation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import click
from configkit.commands import OptionSpec, option_specs
from pydantic import BaseModel

#: The flag every leaf command declares, and no group does: ``--json`` prints one ``rcp.cli.v1`` document.
JSON_FLAG = "--json"

#: The click type of each scalar kind :func:`option_specs` names.
_CLICK_TYPES: Mapping[str, click.ParamType] = {
    "str": click.STRING,
    "int": click.INT,
    "float": click.FLOAT,
    "bool": click.BOOL,
}


def _click_type(spec: OptionSpec) -> click.ParamType:
    """The click type of *spec*: a closed set is a ``Choice``, a path a ``Path``, every other kind a scalar."""
    if spec.type == "choice":
        return click.Choice(list(spec.choices))
    if spec.type == "path":
        return click.Path(path_type=str)
    return _CLICK_TYPES[spec.type]


def option_params(
    request: type[BaseModel],
    *,
    positional: Sequence[str] = (),
    envvars: Mapping[str, str] | None = None,
    hidden: Sequence[str] = (),
    classes: Mapping[str, type[click.Option]] | None = None,
) -> list[click.Parameter]:
    """The click parameters of *request*'s fields, in declaration order.

    Args:
        request: The command's request model; :func:`configkit.commands.option_specs` derives one flag per field.
        positional: Field names taken as positional arguments instead of options.
        envvars: Field name -> the environment variable read when the flag is absent (shown in ``--help``).
        hidden: Field names whose options are hidden from ``--help`` (debug switches).
        classes: Flag -> the click option class to render it with, for a flag whose help is not static
            (``--metric`` reads the metric registry when the help is printed).

    Returns:
        One parameter per field: a ``click.Argument`` for a positional field, else a ``click.Option``. A switch
        renders ``--x/--no-x`` when its default is true, and ``--x`` otherwise.

    Raises:
        TypeError: a field's annotation has no one-flag form (see :func:`configkit.commands.option_specs`).
    """
    envvars = envvars or {}
    classes = classes or {}
    params: list[click.Parameter] = []
    for spec in option_specs(request):
        field = spec.name.removeprefix("--").replace("-", "_")
        click_type = _click_type(spec)
        if field in positional:
            nargs = -1 if spec.repeatable else 1
            params.append(click.Argument([field], type=click_type, required=spec.required, nargs=nargs))
            continue
        common: dict[str, Any] = {
            "help": spec.help or None,
            "hidden": field in hidden,
            "envvar": envvars.get(field),
            "show_envvar": field in envvars,
        }
        if spec.type == "bool" and not spec.repeatable:
            decls = [f"{spec.name}/--no-{spec.name[2:]}" if spec.default else spec.name, field]
            params.append(click.Option(decls, is_flag=True, default=bool(spec.default), show_default=True, **common))
            continue
        option: dict[str, Any] = {"type": click_type, "required": spec.required, "multiple": spec.repeatable, **common}
        if not spec.required:  # click treats an explicit default (even None) as a value, which defeats `required`
            option.update(default=spec.default, show_default=spec.default not in (None, ()))
        params.append(classes.get(spec.name, click.Option)([spec.name, field], **option))
    return params


def json_option() -> click.Option:
    """The ``--json`` flag: one ``rcp.cli.v1`` JSON document on stdout, and nothing else on it."""
    return click.Option([JSON_FLAG, "as_json"], is_flag=True, help="Print one rcp.cli.v1 JSON document on stdout.")


__all__ = ["JSON_FLAG", "json_option", "option_params"]
