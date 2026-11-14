"""One command spec, two front ends, the result included.

A command is declared once -- its name, its request model, its result model, the call and the lines it prints in
human mode -- and every surface is derived from that declaration. The flags come from the request model's fields
(:func:`option_specs`), so the MCP tool's input schema and the command line cannot disagree; the envelope carries
the result model itself, never a projected dict (rcp-45); and a subcommand reports its own name, not the group
that holds it.

The spec is front-end neutral: :func:`option_specs` returns a tuple of :class:`OptionSpec` records, and this
module ships the **argparse** adapter (:func:`build_parser`, standard library only). A consumer on another front
end renders the same specs -- rcp's click adapter does -- so the library never imports a third-party CLI.

Three things are declared at the root and nowhere else: ``--json`` and ``--traceback`` (rcp-2: rcp sniffs the flag
out of ``argv`` today because the declaration is not in one place), the envelope schema id, and the consumer's
exit-code table. A usage error *raises* (:class:`configkit.faults.Fault`) rather than calling ``sys.exit``
(emb-train 17), so a front end reports it in the same envelope and with the same code as any other refusal.
"""

from __future__ import annotations

import argparse
import enum
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, NoReturn, get_args, get_origin

from pydantic import BaseModel

from configkit.faults import ValueErrorFault
from configkit.ports import ExitCodeTable, Ports, Renderer
from configkit.spec import Brand

__all__ = [
    "Command",
    "Verb",
    "CommandTree",
    "DoctorFacts",
    "Step",
    "StepResults",
    "OptionSpec",
    "option_specs",
    "build_parser",
    "index",
    "describe",
    "tool",
    "envelope",
    "run_steps",
    "customise",
]

#: The flags the root parser declares once (rcp-2). A request field of the same name would declare one twice, so
#: the builder refuses it rather than letting argparse's last declaration win silently.
_RESERVED_FLAGS: Final[tuple[str, ...]] = ("--json", "--traceback", "--help", "-h")

#: The origins a repeatable option may have: one flag, appended, per value.
_REPEATABLE_ORIGINS: Final[tuple[Any, ...]] = (list, tuple, set, frozenset, Sequence)


@dataclass(frozen=True)
class OptionSpec:
    """One front-end-neutral option, derived from one field of a command's request model (rcp-1, rcp-24).

    Attributes:
        name: The flag (``"--runs-dir"``), the field's name with ``_`` written as ``-``.
        type: The value's kind: ``"str"``, ``"int"``, ``"float"``, ``"bool"``, ``"path"`` or ``"choice"``.
        default: The field's declared default, or ``None`` when the flag is required. A repeatable option's
            default is a tuple, and a ``bool``'s is a ``bool``.
        help: The field's description, or ``""`` when it declares none.
        repeatable: Whether the option may be given more than once (a list, tuple or set field).
        choices: The closed set of values of a ``"choice"`` option, in the declaration's order; empty otherwise.
        required: Whether the field has no default.
    """

    name: str
    type: str
    default: Any
    help: str
    repeatable: bool
    choices: tuple[str, ...]
    required: bool


def _unwrap(annotation: Any, *, name: str) -> tuple[Any, bool]:
    """``(inner type, repeatable)`` of *annotation*, with ``None`` removed from a union.

    Raises:
        TypeError: the annotation is a union of two or more real types, or a list of lists: neither has one flag
            form, and a silently dropped field is worse than a refusal.
    """
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        members = [argument for argument in get_args(annotation) if argument is not type(None)]
        if len(members) != 1:
            raise TypeError(f"{name}: cannot derive one flag from the union {annotation!r}; name one type")
        return _unwrap(members[0], name=name)
    if origin in _REPEATABLE_ORIGINS:
        arguments = [argument for argument in get_args(annotation) if argument is not Ellipsis]
        inner, nested = _unwrap(arguments[0] if arguments else str, name=name)
        if nested:
            raise TypeError(f"{name}: cannot derive one flag from the nested sequence {annotation!r}")
        return inner, True
    return annotation, False


def _kind(inner: Any, *, name: str) -> tuple[str, tuple[str, ...]]:
    """``(type name, choices)`` of the inner annotation *inner*.

    Raises:
        TypeError: the annotation is not a scalar this module renders (see :class:`OptionSpec`).
    """
    if get_origin(inner) is Literal:
        return "choice", tuple(str(argument) for argument in get_args(inner))
    if isinstance(inner, type) and issubclass(inner, enum.Enum):
        return "choice", tuple(str(member.value) for member in inner)
    for kind, scalar in (("bool", bool), ("int", int), ("float", float), ("path", Path), ("str", str)):
        if inner is scalar:
            return kind, ()
    raise TypeError(
        f"{name}: cannot derive one flag from the type {inner!r}; give the field a scalar type (str, int, float, "
        "bool, Path, Literal or an Enum), or a list of one"
    )


def option_specs(request: type[BaseModel]) -> tuple[OptionSpec, ...]:
    """The options *request*'s fields declare, in declaration order: the one source of a command's flags.

    Args:
        request: The command's request model. Each field becomes one flag; a field's description is its help.

    Returns:
        One :class:`OptionSpec` per field, in the model's declaration order.

    Raises:
        TypeError: a field's annotation has no one-flag form (a union of two types, a nested sequence, a mapping,
            a nested model). Nothing is dropped silently.
    """
    specs: list[OptionSpec] = []
    for name, field in request.model_fields.items():
        inner, repeatable = _unwrap(field.annotation, name=name)
        kind, choices = _kind(inner, name=name)
        required = field.is_required()
        default = None if required else field.get_default(call_default_factory=True)
        if isinstance(default, enum.Enum):
            default = default.value
        if repeatable:
            default = tuple(default or ())
        specs.append(
            OptionSpec(
                name=f"--{name.replace('_', '-')}",
                type=kind,
                default=default,
                help=field.description or "",
                repeatable=repeatable,
                choices=choices,
                required=required,
            )
        )
    return tuple(specs)


@dataclass(frozen=True)
class Command:
    """One verb, declared once: what it takes, what it returns, and which call does the work (rcp-1, et-15).

    Attributes:
        name: The verb's path without the program name (``"run list"``). The last word is the subcommand; the
            earlier words are its groups.
        request: The pydantic model of its inputs: its fields are the flags and the MCP input schema.
        result: The pydantic model of its output, or ``None`` for a free-form document.
        run: ``run(request) -> result``: the library call, given the validated request model.
        human_lines: ``human_lines(result) -> lines``: the text the verb prints in human mode, so the verb owns its
            own rendering and the library owns none (et-18).
        services: ``services() -> Ports``: the host services this verb's call needs, when it needs any.
        add_arguments: A post-declaration hook that adds what no request field can express (a positional
            argument), or ``None``.
    """

    name: str
    request: type[BaseModel]
    result: type[BaseModel] | None
    run: Callable[[Any], BaseModel]
    human_lines: Callable[[BaseModel], Sequence[str]] | None = None
    services: Callable[[], Ports] | None = None
    add_arguments: Callable[[Any], None] | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed declaration: a blank name, a non-model request or result, a non-callable field.

        Raises:
            Fault: the name is blank (``code`` is ``value_error``).
            TypeError: ``request`` or ``result`` is not a pydantic model class, or a callable field is not callable.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueErrorFault(f"a Command needs a non-empty name, got {self.name!r}")
        if not (isinstance(self.request, type) and issubclass(self.request, BaseModel)):
            raise TypeError(f"{self.name}: request must be a pydantic model class, got {self.request!r}")
        if self.result is not None and not (isinstance(self.result, type) and issubclass(self.result, BaseModel)):
            raise TypeError(f"{self.name}: result must be a pydantic model class or None, got {self.result!r}")
        for attribute in ("run", "human_lines", "services", "add_arguments"):
            value = getattr(self, attribute)
            if value is not None and not callable(value):
                raise TypeError(f"{self.name}: {attribute} must be callable or None, got {value!r}")


#: et-15 publishes the command record as ``Verb``; it is the same class, not a second implementation.
Verb = Command


@dataclass(frozen=True)
class DoctorFacts:
    """What a ``doctor`` command reports, taken as data so the command holds no directory of its own (rcp-7).

    Attributes:
        distributions: The installed distributions' versions, by project name.
        pinned_sibling: The sibling distribution and the version this one pins, or ``None``.
        extras: The install line of each optional extra, by extra name.
        credential_variables: The environment variables a credential is read from, by name.
        cache_dir: The cache directory the consumer resolved.
        runs_dir: The runs directory the consumer resolved.
    """

    distributions: Mapping[str, str]
    pinned_sibling: tuple[str, str] | None
    extras: Mapping[str, str]
    credential_variables: tuple[str, ...]
    cache_dir: Path
    runs_dir: Path


@dataclass(frozen=True)
class Step:
    """One step of a multi-step command: a named call whose value lands under one field (et-16).

    Attributes:
        name: The step's name, reported when it fails or is skipped.
        field: The key its value is stored under in :attr:`StepResults.values`.
        run: ``run(values) -> value``: the call, given the values the earlier steps produced (read-only). A step
            that returns ``None`` has nothing to record and is reported as skipped.
    """

    name: str
    field: str
    run: Callable[[Mapping[str, Any]], Any]

    def __post_init__(self) -> None:
        """Refuse a step that cannot be reported on or called.

        Raises:
            Fault: ``name`` or ``field`` is blank (``code`` is ``value_error``).
            TypeError: ``run`` is not callable.
        """
        if not self.name.strip():
            raise ValueErrorFault("a Step needs a non-empty name")
        if not self.field.strip():
            raise ValueErrorFault(f"step {self.name!r} needs a non-empty field")
        if not callable(self.run):
            raise TypeError(f"step {self.name!r}: run must be callable, got {self.run!r}")


@dataclass(frozen=True)
class StepResults:
    """What a step sequence produced: the values by field, the steps skipped, and the first failure.

    Attributes:
        values: The values the steps returned, by :attr:`Step.field`, in the order they were produced.
        skipped: The names of the steps that did not run: a ``None`` return, or a step after a failure that
            stopped the sequence.
        failed: The name of the first step that raised, or ``None``.
        error: The exception that step raised, or ``None``.
    """

    values: Mapping[str, Any]
    skipped: tuple[str, ...]
    failed: str | None = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        """Whether no step failed."""
        return self.failed is None


def run_steps(steps: Sequence[Step], *, stop_on_first: bool = True) -> StepResults:
    """Run *steps* in order, recording each value and the first failure (et-16).

    A step reads the values produced so far, through a read-only view, so a step cannot rewrite an earlier one's
    result. A step that raises is recorded and, when *stop_on_first* holds, every later step is skipped; otherwise
    the sequence continues and the first failure is still the one reported.

    Args:
        steps: The steps, in the order they run.
        stop_on_first: Whether a failure ends the sequence (``True``) or the remaining steps still run.

    Returns:
        The :class:`StepResults`: the values, the skipped names, the failing step and its exception.
    """
    values: dict[str, Any] = {}
    skipped: list[str] = []
    failed: str | None = None
    error: BaseException | None = None
    for step in steps:
        if failed is not None and stop_on_first:
            skipped.append(step.name)
            continue
        try:
            value = step.run(MappingProxyType(dict(values)))
        except Exception as exc:  # a step's failure is a result, not the end of the caller's process
            if failed is None:
                failed, error = step.name, exc
            continue
        if value is None:
            skipped.append(step.name)
            continue
        values[step.field] = value
    return StepResults(values=MappingProxyType(values), skipped=tuple(skipped), failed=failed, error=error)


def index(verbs: Sequence[Command]) -> tuple[str, ...]:
    """The verbs' names, in declaration order: what a dispatcher offers (rcp-1).

    Args:
        verbs: The declared commands.

    Returns:
        The names, unchanged and in order.

    Raises:
        Fault: a name is declared twice (``code`` is ``value_error``). A second declaration would
            silently be the one that runs.
    """
    names = tuple(verb.name for verb in verbs)
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        raise ValueErrorFault(f"verb(s) declared twice: {', '.join(duplicates)}; a name names one command")
    return names


@dataclass(frozen=True)
class CommandTree:
    """The declared verbs as data, so a front end can describe itself without importing the callables (rcp-1).

    Every mapping is keyed by a verb's path (``"run list"``) and holds one entry per entry of :attr:`paths`.

    Attributes:
        paths: The verbs' paths, in declaration order.
        options: Each verb's flags, from :func:`option_specs`.
        results: Each verb's result model's class name, or ``None`` when it declares no result model.
        children: Each path's child paths, in declaration order; a leaf's entry is empty.
    """

    paths: tuple[str, ...]
    options: Mapping[str, tuple[OptionSpec, ...]]
    results: Mapping[str, str | None]
    children: Mapping[str, tuple[str, ...]]


def describe(verbs: Sequence[Command]) -> CommandTree:
    """The command table as a :class:`CommandTree`: the paths, the flags, the result models and the groups.

    Args:
        verbs: The declared commands.

    Returns:
        The tree. A verb's earlier words are its groups, so ``"run list"`` makes ``"run"`` a parent of it.

    Raises:
        Fault: a name is declared twice (``code`` is ``value_error``).
    """
    paths = index(verbs)
    options: dict[str, tuple[OptionSpec, ...]] = {}
    results: dict[str, str | None] = {}
    children: dict[str, list[str]] = {path: [] for path in paths}
    for verb in verbs:
        options[verb.name] = option_specs(verb.request)
        results[verb.name] = verb.result.__name__ if verb.result is not None else None
        words = verb.name.split()
        for depth in range(1, len(words)):
            children.setdefault(" ".join(words[:depth]), []).append(verb.name)
    return CommandTree(
        paths=paths,
        options=MappingProxyType(options),
        results=MappingProxyType(results),
        children=MappingProxyType({path: tuple(names) for path, names in children.items()}),
    )


def _summary(run: Callable[..., Any]) -> str:
    """The first paragraph of *run*'s docstring on one line: the tool's and the subcommand's description."""
    docstring = (getattr(run, "__doc__", "") or "").strip()
    return " ".join(docstring.split("\n\n", 1)[0].split()) if docstring else ""


def tool(command: Command) -> dict[str, Any]:
    """The MCP tool declaration of *command*, generated from the same request and result models (rcp-45, et-18).

    Args:
        command: The declared command.

    Returns:
        A mapping with the tool's ``name`` (the verb's path, ``-``-joined), its ``description`` (the call's
        docstring's first paragraph), its ``inputSchema`` (the request model's JSON Schema), its ``outputSchema``
        (the result model's, or ``None``), and ``_meta`` naming the command path and its flags.

    Raises:
        TypeError: a request field has no one-flag form (see :func:`option_specs`). The tool and the command line
            are one declaration, so a model one of them cannot render is refused here too.
    """
    flags = option_specs(command.request)
    input_schema = command.request.model_json_schema()
    input_schema.pop("title", None)
    output_schema = None
    if command.result is not None:
        output_schema = command.result.model_json_schema()
        output_schema.pop("title", None)
    return {
        "name": command.name.replace(" ", "-"),
        "description": _summary(command.run),
        "inputSchema": input_schema,
        "outputSchema": output_schema,
        "_meta": {"x-cli-command": command.name, "x-flags": [spec.name for spec in flags]},
    }


def envelope(command: Command, result: BaseModel, *, schema_id: str) -> dict[str, Any]:
    """The one document a command's output is: its schema id, the verb's name, the result and the verb's lines.

    The payload is the declared result model's own JSON (never a projected dict, rcp-45), so ``--json``, the human
    render (``lines``) and the MCP tool all read one declaration (et-18).

    Args:
        command: The declared command that produced *result*.
        result: The validated result model instance.
        schema_id: The envelope's schema id, the consumer's (``"rcp.cli.v1"``).

    Returns:
        ``{"schema": schema_id, "command": command.name, "ok": True, "data": result's JSON, "lines": [...]}``,
        where ``lines`` is the verb's human rendering (empty when it declares none).

    Raises:
        TypeError: *result* is not an instance of ``command.result``: the envelope would carry a shape the
            declaration does not describe.
    """
    if command.result is not None and not isinstance(result, command.result):
        raise TypeError(f"{command.name}: the envelope carries {command.result.__name__}, got {type(result).__name__}")
    lines = list(command.human_lines(result)) if command.human_lines is not None else []
    return {
        "schema": schema_id,
        "command": command.name,
        "ok": True,
        "data": result.model_dump(mode="json", by_alias=True),
        "lines": lines,
    }


class _Parser(argparse.ArgumentParser):
    """The parser :func:`build_parser` returns: it carries the declaration and raises instead of exiting."""

    #: The brand the consumer passed: schema ids, output-schema prefixes and environment names come from it.
    brand: Brand
    #: The envelope's schema id, the consumer's.
    envelope_schema_id: str
    #: The consumer's fault code -> exit number table.
    exit_codes: ExitCodeTable
    #: The consumer's result renderer, or ``None``.
    render: Renderer | None
    #: The declared commands, in declaration order.
    verbs: tuple[Command, ...]

    def error(self, message: str) -> NoReturn:
        """Refuse the invocation as a fault, so a front end reports it like any other refusal (emb-train 17).

        Raises:
            Fault: the usage error, code ``"value_error"``, naming the program in ``details``.
        """
        raise ValueErrorFault(
            f"{self.prog}: {message}", details={"prog": self.prog}, hint=f"see {self.prog} --help for the usage"
        )


def _exit_epilog(exit_help: Sequence[tuple[str, str]]) -> str | None:
    """The parser's epilog: the consumer's exit-code rows, one per line, or ``None`` when it declares none."""
    if not exit_help:
        return None
    rows = "\n".join(f"  {code}: {text}" for code, text in exit_help)
    return f"exit codes:\n{rows}"


def _add_option(parser: argparse.ArgumentParser, spec: OptionSpec) -> None:
    """Declare one argparse option from *spec*, refusing a flag the root parser already declares.

    Raises:
        Fault: *spec*'s flag is one the root declares (``--json``, ``--traceback``, ``--help``): the request field
            would declare it twice, and argparse would silently keep the last one.
    """
    if spec.name in _RESERVED_FLAGS:
        field = spec.name[2:].replace("-", "_")
        raise ValueErrorFault(
            f"the request field {field!r} declares {spec.name}, which the root parser already declares",
            details={"flag": spec.name, "field": field},
            hint=f"rename the field, or read {spec.name} from the parsed namespace",
        )
    kwargs: dict[str, Any] = {"dest": spec.name[2:].replace("-", "_"), "required": spec.required}
    if spec.help:
        kwargs["help"] = spec.help
    if spec.type == "bool":
        kwargs["action"] = argparse.BooleanOptionalAction
        kwargs["default"] = bool(spec.default)
    else:
        if spec.type == "choice":
            kwargs["choices"] = list(spec.choices)
        elif spec.type in ("int", "float", "path"):
            kwargs["type"] = {"int": int, "float": float, "path": Path}[spec.type]
        if spec.repeatable:
            kwargs["action"] = "append"
            kwargs["default"] = list(spec.default)  # argparse copies it before appending, so no parse shares it
        else:
            kwargs["default"] = spec.default
    parser.add_argument(spec.name, **kwargs)


def _add_verbs(parser: argparse.ArgumentParser, verbs: Sequence[Command]) -> None:
    """Build the subparser tree: one level per word, a verb's parser shared with the group of the same name."""
    paths = index(verbs)
    declared = {verb.name: verb for verb in verbs}
    groups: list[str] = []
    for path in paths:
        words = path.split()
        for depth in range(1, len(words)):
            group = " ".join(words[:depth])
            if group not in groups:
                groups.append(group)
    parsers: dict[str, argparse.ArgumentParser] = {"": parser}
    subparsers: dict[str, Any] = {}

    def children(path: str) -> Any:
        if path not in subparsers:
            subparsers[path] = parsers[path].add_subparsers(metavar="COMMAND")
        return subparsers[path]

    for path in sorted({*paths, *groups}, key=lambda name: (len(name.split()), name)):
        parent, _, word = path.rpartition(" ")
        verb = declared.get(path)
        summary = _summary(verb.run) if verb is not None else f"{word} commands"
        parsers[path] = children(parent).add_parser(word, help=summary or None)
        if verb is None:
            continue
        parsers[path].description = summary or None
        parsers[path].set_defaults(command=verb.name)
        for spec in option_specs(verb.request):
            _add_option(parsers[path], spec)
        if verb.add_arguments is not None:
            verb.add_arguments(parsers[path])


def build_parser(
    prog: str,
    verbs: Sequence[Command],
    *,
    brand: Brand,
    envelope_schema_id: str,
    exit_codes: ExitCodeTable,
    render: Renderer | None = None,
    exit_help: Sequence[tuple[str, str]] = (),
) -> argparse.ArgumentParser:
    """The argparse front end of *verbs*: one subparser per verb, built from the request models (rcp-2, et-15).

    The root parser declares ``--json`` and ``--traceback`` once, and carries the rest of the declaration
    (``brand``, ``envelope_schema_id``, ``exit_codes``, ``render``, ``verbs``) as attributes, so a front end that
    parses ``argv`` needs nothing else to build the envelope or report a fault. A verb's path is one subparser
    level per word: ``"run list"`` parses as ``prog run list``.

    Args:
        prog: The program name, as ``usage:`` prints it.
        verbs: The declared commands.
        brand: The one brand parameter: schema ids, output-schema prefixes and environment names.
        envelope_schema_id: The schema id of the document the front end prints.
        exit_codes: The consumer's fault code -> exit number table.
        render: The consumer's renderer for a result model, or ``None``.
        exit_help: The consumer's ``(code, text)`` rows, printed in the parser's epilog.

    Returns:
        The root :class:`argparse.ArgumentParser`; its ``error`` raises a :class:`configkit.faults.Fault` rather
        than exiting, so a usage error is reported like any other refusal.

    Raises:
        Fault: a verb is declared twice (``code`` is ``value_error``).
        Fault: a request field's flag is one the root parser already declares.
        TypeError: a request field has no one-flag form.
    """
    parser = _Parser(
        prog=prog,
        description=None,
        epilog=_exit_epilog(exit_help),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.brand = brand
    parser.envelope_schema_id = envelope_schema_id
    parser.exit_codes = exit_codes
    parser.render = render
    parser.verbs = tuple(verbs)
    parser.add_argument("--json", action="store_true", help="Print one JSON document (the envelope) on stdout.")
    parser.add_argument("--traceback", action="store_true", help="Print a failure's Python traceback.")
    if verbs:
        _add_verbs(parser, verbs)
    return parser


def customise(command: Command, option: str, help_at_print_time: Callable[[], str]) -> Command:
    """A copy of *command* whose *option*'s help is asked for when the help is printed (rcp-4).

    A plugin registers after the command is built, so its help text is not known at declaration time: the returned
    command's post-declaration hook replaces the formatter, and the callable is evaluated on every
    ``format_help()``.

    Args:
        command: The declared command.
        option: The flag whose help is deferred (``"--metric"``); it must be one of the request's options.
        help_at_print_time: ``help_at_print_time() -> text``: the help, evaluated when the help is printed.

    Returns:
        A new :class:`Command`; *command* is unchanged.

    Raises:
        Fault: *option* is not a flag the request declares (``code`` is ``value_error``). The message names
            the declared flags.
    """
    declared = [spec.name for spec in option_specs(command.request)]
    if option not in declared:
        raise ValueErrorFault(f"{command.name}: {option} is not a declared option; declared: {', '.join(declared)}")
    previous = command.add_arguments

    def add_arguments(parser: Any) -> None:
        if previous is not None:
            previous(parser)
        parser.formatter_class = _deferred_formatter({option: help_at_print_time})

    return replace(command, add_arguments=add_arguments)


def _deferred_formatter(deferred: Mapping[str, Callable[[], str]]) -> type[argparse.HelpFormatter]:
    """A formatter class whose help for each option in *deferred* is that callable's value, at print time.

    The class is created per call, so two customised commands in one process never share a table. ``argparse``
    instantiates the formatter with no arguments of its own, which is why the table is closed over rather than
    passed in.
    """

    class _DeferredHelpFormatter(argparse.RawDescriptionHelpFormatter):
        def _format_action(self, action: argparse.Action) -> str:
            for name, text in deferred.items():
                if name in action.option_strings:
                    action.help = text()
            return super()._format_action(action)

    return _DeferredHelpFormatter
