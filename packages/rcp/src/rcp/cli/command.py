"""One decorator turns a library call into a CLI command, and the same declaration serves MCP.

A command is declared once::

    @command("run list", request=RunListRequest, result=RunList)
    def run_list(request: RunListRequest) -> RunList:
        '''List the runs under a runs directory, newest first.'''
        return RunList(...)

The declaration is the library's :class:`configkit.commands.Command` -- the name, the request and result models,
the call and the human renderer -- and the decorator adds what this front end needs: a :class:`CommandSpec`
(the declaration, its output-schema id and its one-line summary) kept on the click command (``.spec``), the click
parameters rendered from the request's fields by :mod:`rcp.cli.click_adapter` (a flag is a field, so the MCP
input schema is the same model), and ``--json``. A call renders the result as text or as the ``rcp.cli.v1``
envelope (:mod:`rcp.cli.output`), and every exception is mapped through :func:`rcp.errors.classify` onto a typed
error and its exit code. :func:`execute` runs a declaration for the CLI and for the MCP server alike, and
:func:`rcp.cli.introspect.command_specs` collects them all.
"""

from __future__ import annotations

import re
import sys
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import click
from configkit.commands import Command
from pydantic import BaseModel, ValidationError

from rcp.cli.click_adapter import json_option, option_params
from rcp.cli.output import (
    error_document,
    print_error,
    print_json,
    print_warnings,
    success_document,
)
from rcp.errors import RcpNdcgError, RcpNdcgWarning, UsageError, classify
from rcp.support.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CommandSpec:
    """A declared command: the library's declaration and the two facts this front end adds to it.

    Attributes:
        command: The declaration (:class:`configkit.commands.Command`): the name, the request model of its inputs
            (its fields are the flags and the MCP input schema), the result model of its output, the call and the
            human renderer.
        output_schema: The id of the JSON Schema that ``data`` conforms to (``"rcp.run-list.v1"``).
        read_only: Whether the call leaves no artifacts behind.
        summary: The one-line description (help text, MCP tool description).
    """

    command: Command
    output_schema: str
    read_only: bool = True
    summary: str = ""

    @property
    def name(self) -> str:
        """The command path without the program name (``"data inspect"``)."""
        return self.command.name

    @property
    def request(self) -> type[BaseModel]:
        """The pydantic model of the inputs: its fields are the flags and the MCP input schema."""
        return self.command.request

    @property
    def result(self) -> type[BaseModel] | None:
        """The pydantic model of the output, or ``None`` for a free-form JSON document."""
        return self.command.result

    @property
    def handler(self) -> Callable[[Any], Any]:
        """``handler(request) -> result``: the library call."""
        return self.command.run


@dataclass
class Outcome:
    """What one execution produced: the result as JSON-ready ``data``, or the typed ``error``."""

    command: str
    result: Any = None
    data: Any = None
    error: RcpNdcgError | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    elapsed_s: float = 0.0

    def document(self) -> dict[str, Any]:
        """The ``rcp.cli.v1`` envelope of this outcome."""
        if self.error is not None:
            return error_document(self.command, self.error, warnings=self.warnings, elapsed_s=self.elapsed_s)
        run_dir = self.data.get("run_dir") if isinstance(self.data, dict) else None
        return success_document(
            self.command,
            self.data,
            warnings=self.warnings,
            elapsed_s=self.elapsed_s,
            run_dir=str(run_dir) if run_dir is not None else None,
        )


def schema_name(model: type[BaseModel]) -> str:
    """The schema name of a result model: its class name in kebab case (``RunList`` -> ``run-list``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", model.__name__).lower()


def _validation_error(exc: ValidationError, model: type[BaseModel]) -> UsageError:
    """The request model's refusal, in the one shape of ``details.errors`` (see
    :func:`rcp.support.config.validation_problems`)."""
    from rcp.support.config import validation_problems

    problems = validation_problems(exc, model=model, source="the arguments")
    listed = "; ".join(f"{problem['field']}: {problem['problem']}" for problem in problems)
    return UsageError(
        f"invalid arguments: {listed}", hint="see --help for the expected values", details={"errors": problems}
    )


def _data(spec: CommandSpec, result: Any) -> Any:
    """The result as JSON-ready data, tagged with its schema id (a model that carries its own ``schema`` keeps it)."""
    if isinstance(result, BaseModel):
        payload = result.model_dump(mode="json", by_alias=True)
        return {"schema": spec.output_schema, **payload} if spec.result is not None else payload
    return result


def execute(spec: CommandSpec, arguments: Mapping[str, Any]) -> Outcome:
    """Validate *arguments* against the request model, run the handler and capture its typed warnings.

    Every exception becomes a typed error on the outcome (malformed arguments are :class:`UsageError`); only
    ``KeyboardInterrupt`` and ``SystemExit`` propagate.

    Args:
        spec: The command.
        arguments: Field values of ``spec.request``.

    Returns:
        The :class:`Outcome`, with ``elapsed_s`` in seconds.
    """
    started = time.perf_counter()
    outcome = Outcome(command=spec.name)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            try:
                request = spec.request.model_validate(dict(arguments))
            except ValidationError as exc:
                raise _validation_error(exc, spec.request) from exc
            outcome.result = spec.handler(request)
            outcome.data = _data(spec, outcome.result)
        except Exception as exc:  # noqa: BLE001 -- every failure is reported as a typed error
            outcome.error = classify(exc).for_cli()
            logger.debug(f"{spec.name} failed: {type(exc).__name__}: {exc}", exc_info=exc)
    for record in seen:
        if isinstance(record.message, RcpNdcgWarning):
            outcome.warnings.append(record.message.to_dict())
        else:  # not ours: hand it back to the normal warning filters
            warnings.warn_explicit(record.message, record.category, record.filename, record.lineno)
    outcome.elapsed_s = time.perf_counter() - started
    return outcome


def _summary(handler: Callable[..., Any]) -> str:
    """The docstring's first paragraph on one line (its continuation indentation collapsed to one space)."""
    doc = (handler.__doc__ or "").strip()
    return " ".join(doc.split("\n\n", 1)[0].split()) if doc else ""


def _human_lines(text: Callable[[Any], str]) -> Callable[[Any], Sequence[str]]:
    """A command's ``text`` renderer as the declaration's human lines: its one string, as one line."""
    return lambda result: [text(result)]


def _render_text(data: Any) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120).rstrip()


def _run_cli(spec: CommandSpec, arguments: dict[str, Any], *, as_json: bool, text: Callable[[Any], str] | None) -> None:
    try:
        outcome = execute(spec, arguments)
    except KeyboardInterrupt:
        outcome = Outcome(command=spec.name, error=classify(KeyboardInterrupt()))
    if outcome.error is not None:
        if as_json:
            print_json(outcome.document())
        else:
            print_warnings(outcome.warnings)
            print_error(outcome.error)
        sys.exit(int(outcome.error.exit_code))
    if as_json:
        print_json(outcome.document())
        return
    print_warnings(outcome.warnings)
    click.echo(text(outcome.result) if text is not None else _render_text(outcome.data))


def command(
    name: str,
    *,
    request: type[BaseModel],
    result: type[BaseModel] | None,
    output_schema: str | None = None,
    text: Callable[[Any], str] | None = None,
    positional: Sequence[str] = (),
    envvars: Mapping[str, str] | None = None,
    hidden_fields: Sequence[str] = (),
    option_classes: Mapping[str, type[click.Option]] | None = None,
    read_only: bool = True,
) -> Callable[[Callable[[Any], Any]], click.Command]:
    """Declare a command over a library call.

    Args:
        name: The command path without the program name; the last word is the click command's name.
        request: The pydantic model of the inputs. Each field becomes ``--field-name`` (``True`` defaults become
            ``--x/--no-x``, lists become repeatable options); fields named in *positional* become arguments.
        result: The pydantic model of the output; ``None`` for a free-form JSON document, which then needs an
            explicit *output_schema*.
        output_schema: The schema id of ``data``; default ``rcp.<kebab name of result>.v1``.
        text: Renders the result for a person; default a YAML dump of ``data``.
        positional: Fields taken as positional arguments.
        envvars: Field name -> environment variable read when the flag is absent (shown in ``--help``).
        hidden_fields: Fields whose options are hidden from ``--help`` (debug switches).
        option_classes: Flag -> the click option class that renders it, for a flag whose help is not static
            (``--metric`` reads the metric registry when the help is printed).
        read_only: Whether the call leaves no artifacts behind (MCP ``readOnlyHint``).

    Returns:
        A decorator turning ``handler(request) -> result`` into a :class:`click.Command` that carries the
        :class:`CommandSpec` as ``.spec`` and its schema id as ``.output_schema``.
    """
    if output_schema is None:
        if result is None:
            raise ValueError(f"{name}: a command without a result model needs an explicit output_schema")
        output_schema = f"rcp.{schema_name(result)}.v1"
    resolved_schema = output_schema

    def decorator(handler: Callable[[Any], Any]) -> click.Command:
        declaration = Command(
            name=name,
            request=request,
            result=result,
            run=handler,
            human_lines=None if text is None else _human_lines(text),
        )
        spec = CommandSpec(
            command=declaration,
            output_schema=resolved_schema,
            read_only=read_only,
            summary=_summary(handler),
        )
        params = option_params(
            request,
            positional=positional,
            envvars=envvars or {},
            hidden=hidden_fields,
            classes=option_classes,
        )
        params.append(json_option())

        def callback(as_json: bool, **values: Any) -> None:
            arguments = {key: list(value) if isinstance(value, tuple) else value for key, value in values.items()}
            _run_cli(spec, arguments, as_json=as_json, text=text)

        cmd = click.Command(
            name=name.split()[-1],
            callback=callback,
            params=params,
            help=handler.__doc__,
            short_help=spec.summary,
        )
        cmd.spec = spec  # type: ignore[attr-defined]
        cmd.output_schema = resolved_schema  # type: ignore[attr-defined]
        return cmd

    return decorator


__all__ = ["CommandSpec", "Outcome", "command", "execute", "schema_name"]
