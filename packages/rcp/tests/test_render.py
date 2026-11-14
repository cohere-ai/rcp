"""P6 (C6): the front end renders the library's result model; it does not project its own dict.

A command's declaration is :class:`configkit.commands.Command`, whose ``run`` returns the *result model* and whose
``human_lines`` renderer takes that model. :func:`rcp.cli.command.command` wraps the declared ``text`` renderer as
the declaration's ``human_lines``, and the click callback hands the renderer the model the handler returned -- never
the ``data`` projection the ``--json`` envelope carries. That projection exists (``Outcome.data``, one
``model_dump``), and it is a second consumer of the same model, not the source the human renderer reads.
"""

from __future__ import annotations

from typing import Any

from click.testing import CliRunner
from pydantic import BaseModel

from rcp.cli.command import command, execute


class Ask(BaseModel):
    """A command's inputs: one field, one ``--word`` flag."""

    word: str


class Echo(BaseModel):
    """A command's result: the one payload the front end prints."""

    message: str


def _echo(seen: list[Any]):
    """A declared command whose ``text`` records what it is handed and returns the model's own field."""

    def render(result: Echo) -> str:
        seen.append(result)
        return result.message

    @command("echo", request=Ask, result=Echo, text=render)
    def echo(request: Ask) -> Echo:
        """Print the word back."""
        return Echo(message=request.word)

    return echo


def test_the_human_renderer_receives_the_result_model() -> None:
    """The click callback passes ``Outcome.result`` (the model), not ``Outcome.data`` (the projection)."""
    seen: list[Any] = []
    result = CliRunner().invoke(_echo(seen), ["--word", "hi"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "hi"
    assert len(seen) == 1
    assert isinstance(seen[0], Echo), f"the renderer got {type(seen[0]).__name__}, not the result model"
    assert seen[0].message == "hi"


def test_the_declaration_carries_that_renderer() -> None:
    """``text`` becomes the library ``Command``'s ``human_lines``: one renderer, declared once, on the record."""
    seen: list[Any] = []
    spec = _echo(seen).spec

    assert spec.command.human_lines is not None
    assert spec.command.human_lines(Echo(message="from the record")) == ["from the record"]
    assert isinstance(seen[0], Echo)


def test_the_json_projection_is_the_models_dump_not_a_second_shape() -> None:
    """The ``--json`` envelope's ``data`` is one ``model_dump`` of the same model plus its schema tag, so the two
    consumers of a result (text and JSON) cannot disagree about its fields."""
    spec = _echo([]).spec
    outcome = execute(spec, {"word": "hi"})

    assert isinstance(outcome.result, Echo)
    assert outcome.data == {"schema": "rcp.echo.v1", "message": "hi"}
