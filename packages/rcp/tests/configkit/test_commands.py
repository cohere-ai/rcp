"""One spec, two front ends, the result included: the flags, the MCP tool, the envelope and the steps.

The declaration is the only place a command is described. The argparse parser is built from ``option_specs`` (no
flag is hand-written beside it), the MCP tool's input schema is the same request model, and the envelope is the
result model plus the verb's own human lines -- so a new flag or a new result field is one edit, and a subcommand
reports its own name rather than its parent's.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pytest
from configkit.commands import (
    Command,
    CommandTree,
    DoctorFacts,
    OptionSpec,
    Step,
    StepResults,
    Verb,
    build_parser,
    customise,
    describe,
    envelope,
    index,
    option_specs,
    run_steps,
    tool,
)
from configkit.faults import Fault, ValueErrorFault, exit_codes
from configkit.spec import Brand
from pydantic import BaseModel, Field

#: ``packages/rcp/src/configkit``: the package whose source must hold no ``print`` call.
PACKAGE = Path(__file__).resolve().parents[2] / "src/configkit"


class _ListRequest(BaseModel):
    """A request model covering every kind of option the builder renders."""

    limit: int = Field(default=50, description="How many rows to list.")
    threshold: float = 0.5
    runs_dir: Path | None = None
    tags: list[str] = []
    mode: Literal["fast", "full"] = "fast"
    dry_run: bool = False
    name: str


class _Empty(BaseModel):
    """A request model with no options."""


class _ListResult(BaseModel):
    """A result model: what the envelope carries."""

    rows: list[str] = []


def _list(request: Any) -> _ListResult:
    """List the rows under a runs directory."""
    return _ListResult(rows=[str(request)])


def _command(name: str = "run list", *, request: type[BaseModel] = _ListRequest, **kwargs: Any) -> Command:
    """A command over the list request, with every optional field left to the caller."""
    return Command(name=name, request=request, result=_ListResult, run=_list, **kwargs)


def _brand() -> Brand:
    """The one brand parameter a parser and a tool take."""
    return Brand(name="p", prefix="P", schema_root="p.cli.v1", output_schema_prefix="p.run")


def _parser(verbs: Sequence[Command], **kwargs: Any) -> argparse.ArgumentParser:
    """A parser over *verbs*, with the brand, the envelope id and an empty exit-code table filled in."""
    return build_parser(
        "p", list(verbs), brand=_brand(), envelope_schema_id="p.cli.v1", exit_codes=exit_codes({}), **kwargs
    )


def _subparsers(parser: argparse.ArgumentParser) -> Any:
    """The subparsers action of *parser* (argparse keeps it in ``_actions``)."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("the parser declares no subcommands")


def _leaf(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    """The leaf parser of verb *name* (one word)."""
    return _subparsers(parser).choices[name]


def _action(parser: argparse.ArgumentParser, option: str) -> argparse.Action:
    """The action that declares *option*."""
    for action in parser._actions:
        if option in action.option_strings:
            return action
    raise AssertionError(f"{option} is not declared")


def test_flags_equal_specs() -> None:
    """E1: the parser's flags are exactly ``option_specs(request)``, with the same types, defaults and help."""
    specs = option_specs(_ListRequest)
    leaf = _leaf(_parser([_command(name="list")]), "list")
    declared = {
        option for action in leaf._actions for option in action.option_strings if option not in {"-h", "--help"}
    }
    expected = {spec.name for spec in specs} | {f"--no-{spec.name[2:]}" for spec in specs if spec.type == "bool"}
    assert declared == expected

    limit = _action(leaf, "--limit")
    assert (limit.type, limit.default, limit.help, limit.required) == (int, 50, "How many rows to list.", False)
    assert _action(leaf, "--threshold").type is float
    assert _action(leaf, "--runs-dir").type is Path
    assert _action(leaf, "--mode").choices == ["fast", "full"]
    assert _action(leaf, "--name").required is True
    assert _action(leaf, "--tags").__class__.__name__ == "_AppendAction"

    namespace = _parser([_command(name="list")]).parse_args(
        ["list", "--name", "x", "--limit", "3", "--tags", "a", "--tags", "b", "--mode", "full", "--dry-run"]
    )
    assert (namespace.name, namespace.limit, namespace.tags, namespace.mode) == ("x", 3, ["a", "b"], "full")
    assert namespace.dry_run is True
    assert namespace.command == "list"
    assert _parser([_command(name="list")]).parse_args(["list", "--name", "x"]).dry_run is False


def test_option_specs_unwraps_optional_and_repeats() -> None:
    """A ``| None`` is the inner type, a list is repeatable, and a Literal is a closed choice."""
    specs = {spec.name: spec for spec in option_specs(_ListRequest)}
    assert specs["--runs-dir"].type == "path" and specs["--runs-dir"].required is False
    assert specs["--tags"].repeatable is True and specs["--tags"].default == ()
    assert specs["--mode"].choices == ("fast", "full")
    assert specs["--name"].required is True and specs["--name"].default is None
    assert isinstance(specs["--limit"], OptionSpec)


@pytest.mark.parametrize("annotation", [dict[str, int], list[list[str]]])
def test_option_specs_refuses_a_type_it_cannot_render(annotation: Any) -> None:
    """A field with no flag form is refused naming the field and the type: nothing is silently dropped."""

    class _Unrenderable(BaseModel):
        payload: annotation  # type: ignore[valid-type]

    with pytest.raises(TypeError, match="payload"):
        option_specs(_Unrenderable)


def test_mcp_tool_from_the_request_model() -> None:
    """E2: the tool's input schema is the request model, its output schema the result model, and it names the verb."""
    declaration = tool(_command())
    request_schema = _ListRequest.model_json_schema()
    request_schema.pop("title")
    result_schema = _ListResult.model_json_schema()
    result_schema.pop("title")
    assert declaration["name"] == "run-list"
    assert declaration["description"] == "List the rows under a runs directory."
    assert declaration["inputSchema"] == request_schema
    assert declaration["outputSchema"] == result_schema
    assert declaration["_meta"]["x-cli-command"] == "run list"
    assert "--dry-run" in declaration["_meta"]["x-flags"]
    assert tool(Command(name="noop", request=_Empty, result=None, run=_list))["outputSchema"] is None


def test_a_flag_declared_twice_is_refused() -> None:
    """E2: the root declares ``--json`` once; a request field of the same name would declare it a second time."""

    class _Reserved(BaseModel):
        traceback: bool = False

    with pytest.raises(Fault, match="--traceback") as caught:
        _parser([_command(request=_Reserved)])
    assert caught.value.code == "value_error"
    assert caught.value.details == {"flag": "--traceback", "field": "traceback"}


def test_a_subcommand_result_carries_its_own_name() -> None:
    """E3: a leaf's envelope and parsed namespace name the leaf, not the group that holds it."""
    parent = Command(name="run", request=_Empty, result=_ListResult, run=_list, human_lines=lambda result: ["parent"])
    child = Command(
        name="run list", request=_ListRequest, result=_ListResult, run=_list, human_lines=lambda result: ["child"]
    )
    result = _ListResult(rows=["a"])
    assert envelope(child, result, schema_id="p.cli.v1")["command"] == "run list"
    assert envelope(parent, result, schema_id="p.cli.v1")["command"] == "run"
    assert envelope(child, result, schema_id="p.cli.v1")["lines"] == ["child"]
    assert envelope(parent, result, schema_id="p.cli.v1")["lines"] == ["parent"]
    namespace = _parser([parent, child]).parse_args(["run", "list", "--name", "x"])
    assert namespace.command == "run list"


def test_envelope_is_the_one_document() -> None:
    """The envelope: the parameter schema id, the verb's name, the result model's own JSON and the verb's lines."""
    command = _command(human_lines=lambda result: [f"{len(result.rows)} rows"])
    document = envelope(command, _ListResult(rows=["a", "b"]), schema_id="p.cli.v1")
    assert document == {
        "schema": "p.cli.v1",
        "command": "run list",
        "ok": True,
        "data": {"rows": ["a", "b"]},
        "lines": ["2 rows"],
    }
    assert envelope(_command(), _ListResult(), schema_id="p.cli.v1")["lines"] == []


def test_envelope_refuses_a_result_the_command_does_not_declare() -> None:
    """The payload is the declared model (C6): another model would make the envelope a second, unchecked shape."""

    class _Other(BaseModel):
        rows: list[str] = []

    with pytest.raises(TypeError, match="_Other"):
        envelope(_command(), _Other(), schema_id="p.cli.v1")


def test_build_parser_carries_the_declaration() -> None:
    """rcp-2: the program name, the brand, the envelope schema id, the exit-code table, the renderer and the verbs."""
    table = exit_codes({"value_error": 2})

    def render(result: Any) -> str:
        return str(result)

    parser = build_parser(
        "prog",
        [_command()],
        brand=_brand(),
        envelope_schema_id="p.cli.v1",
        exit_codes=table,
        render=render,
        exit_help=(("value_error", "the value does not fit"),),
    )
    assert parser.prog == "prog"
    assert parser.brand == _brand()
    assert parser.envelope_schema_id == "p.cli.v1"
    assert parser.exit_codes is table
    assert parser.render is render
    assert [verb.name for verb in parser.verbs] == ["run list"]
    assert "value_error" in parser.format_help()
    assert "the value does not fit" in parser.format_help()


def test_the_root_declares_json_and_traceback_once() -> None:
    """rcp-2: both switches are the root parser's, so a verb's flag list does not repeat them."""
    parser = _parser([_command(name="list")])
    namespace = parser.parse_args(["--json", "--traceback", "list", "--name", "x"])
    assert namespace.json is True and namespace.traceback is True
    leaf = _leaf(parser, "list")
    assert not [action for action in leaf._actions if {"--json", "--traceback"} & set(action.option_strings)]
    assert parser.parse_args(["list", "--name", "x"]).json is False


def test_a_usage_error_raises_and_is_caught() -> None:
    """et-17: an argparse fault is a :class:`Fault` the front end classifies, never a ``SystemExit(2)``."""
    parser = _parser([_command(name="list")])
    with pytest.raises(Fault) as caught:
        parser.parse_args(["list", "--limit", "not-an-int", "--name", "x"])
    assert caught.value.code == "value_error"
    # The parser that refused names itself: the verb's, whose usage is the one the caller got wrong.
    assert caught.value.details["prog"] == "p list"
    with pytest.raises(Fault):
        parser.parse_args(["--not-a-root-flag"])
        parser.parse_args(["list", "--not-a-flag", "x", "--name", "x"])
    with pytest.raises(Fault):
        parser.parse_args(["list"])


def test_customise_evaluates_the_help_when_it_prints() -> None:
    """rcp-4: a plugin registers after the command is built, so one option's help is asked for at print time."""
    text = ["first"]
    customised = customise(_command(name="list"), "--mode", lambda: f"mode: {text[0]}")
    parser = _parser([customised])
    assert "mode: first" in _leaf(parser, "list").format_help()
    text[0] = "second"
    assert "mode: second" in _leaf(parser, "list").format_help()
    assert "mode: second" not in _parser([_command(name="list")]).format_help()


def test_customise_refuses_an_option_the_request_does_not_declare() -> None:
    """A post-declaration hook on a flag nobody declared is a typo, and it names the flags that exist."""
    with pytest.raises(ValueErrorFault, match="--mod"):
        customise(_command(), "--mod", lambda: "x")


def test_index_and_describe() -> None:
    """rcp-1: the names the dispatcher builds, and the table as data -- flags, result model and children."""
    parent = Command(name="run", request=_Empty, result=None, run=_list)
    child = _command()
    verbs = [parent, child]
    assert index(verbs) == ("run", "run list")
    tree = describe(verbs)
    assert isinstance(tree, CommandTree)
    assert tree.paths == ("run", "run list")
    assert tree.options["run"] == ()
    assert tree.options["run list"] == option_specs(_ListRequest)
    assert tree.results == {"run": None, "run list": "_ListResult"}
    assert tree.children == {"run": ("run list",), "run list": ()}


def test_index_refuses_a_duplicate_verb() -> None:
    """A verb name names one command: a second declaration of it would be the one that wins, silently."""
    with pytest.raises(ValueErrorFault, match="run list"):
        index([_command(), _command()])


def test_run_steps_stops_on_the_first_failure_and_skips_the_rest() -> None:
    """et-16: the values by field, the failure, and the steps that never ran because of it."""
    seen: list[str] = []

    def _first(values: Any) -> int:
        seen.append("first")
        return 1

    def _boom(values: Any) -> int:
        seen.append("boom")
        raise ValueError("nope")

    def _third(values: Any) -> int:
        seen.append("third")
        return 3

    results = run_steps([Step("load", "first", _first), Step("fit", "second", _boom), Step("report", "third", _third)])
    assert isinstance(results, StepResults)
    assert results.values == {"first": 1}
    assert results.failed == "fit"
    assert isinstance(results.error, ValueError)
    assert results.skipped == ("report",)
    assert results.ok is False
    assert seen == ["first", "boom"]


def test_run_steps_continues_when_asked_and_skips_a_none_step() -> None:
    """``stop_on_first=False`` runs the rest, and a step with nothing to record is skipped rather than stored."""
    results = run_steps(
        [
            Step("a", "a", lambda values: 1),
            Step("b", "b", lambda values: None),
            Step("c", "c", lambda values: values["a"] + 1),
        ],
        stop_on_first=False,
    )
    assert results.values == {"a": 1, "c": 2}
    assert results.skipped == ("b",)
    assert results.ok is True


def test_run_steps_refuses_a_malformed_step() -> None:
    """A step with no name or no field cannot be reported on, so it is refused at declaration."""
    with pytest.raises(ValueErrorFault):
        Step("", "field", lambda values: 1)
    with pytest.raises(ValueErrorFault):
        Step("name", "", lambda values: 1)
    with pytest.raises(TypeError):
        Step("name", "field", "not callable")  # type: ignore[arg-type]


def test_doctor_facts_is_the_declared_record() -> None:
    """rcp-7: ``doctor`` takes its facts as data, and the record names all six of them."""
    facts = DoctorFacts(
        distributions={"p": "1.0"},
        pinned_sibling=("p-core", "1.0"),
        extras={"yaml": "p[yaml]"},
        credential_variables=("P_TOKEN",),
        cache_dir=Path("/cache"),
        runs_dir=Path("/runs"),
    )
    assert set(DoctorFacts.__dataclass_fields__) == {
        "distributions",
        "pinned_sibling",
        "extras",
        "credential_variables",
        "cache_dir",
        "runs_dir",
    }
    # L19: the record is exercised, so a field that carries nothing reddens this test.
    assert facts.distributions == {"p": "1.0"}
    assert facts.pinned_sibling == ("p-core", "1.0")
    assert facts.extras == {"yaml": "p[yaml]"}
    assert facts.credential_variables == ("P_TOKEN",)
    assert facts.cache_dir == Path("/cache")
    assert facts.runs_dir == Path("/runs")


def test_verb_is_command() -> None:
    """et-15 publishes the record as ``Verb``; it is the same class, not a second implementation."""
    assert Verb is Command


def test_command_refuses_a_malformed_declaration() -> None:
    """A command with no name, a non-model request or a non-callable run is a declaration error."""
    with pytest.raises(ValueErrorFault):
        Command(name=" ", request=_Empty, result=None, run=_list)
    with pytest.raises(TypeError):
        Command(name="x", request=dict, result=None, run=_list)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Command(name="x", request=_Empty, result=None, run="not callable")  # type: ignore[arg-type]


def test_no_print_call_in_the_package() -> None:
    """E4: the library returns typed results and prints nothing; a front end writes to the consumer's sink."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
