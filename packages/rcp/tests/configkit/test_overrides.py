"""One grammar, one value ladder, one error table, one precedence — and the pipeline's stage order.

The grammar is ``rcp``'s (``support/config.py:69``): an override value is a YAML literal, so ``5`` is an int,
``true`` and ``yes`` are booleans, ``[a, b]`` a list and an empty value the empty string. The precedence is
``file < env < flag``, and the source of the value that won is on the :class:`Override` record.

``pipeline`` runs the contract's five stages **in the contract's order** — ``read -> resolve -> apply ->
validate -> identity`` — and that order is what the tests below pin: an override is written *before* validation
(so an invalid override is refused by ``validate``, not smuggled into the model) and *before* the identity (so an
override cannot reuse a cache digest computed without it). ``merge_layers`` is a different rule for a different
requirement (rcp 23): it refuses a CONTENT field two layers set differently and allows a RUNTIME override.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, ClassVar

import pytest
from configkit.document import Applied, deep_merge, leaf_paths, load, read, resolve, validate_or_fault
from configkit.faults import FAULT_CODES, Fault
from configkit.overrides import (
    VALUE_ERRORS,
    Override,
    apply,
    from_env,
    merge_layers,
    parse,
    parse_value,
    pipeline,
    unset,
)
from configkit.roles import Role, identity_of, roles_of
from pydantic import BaseModel, ConfigDict, Field


class _Inner(BaseModel):
    """A nested block with one RUNTIME knob and one CONTENT value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concurrency: int = 1
    endpoint: str = "http://localhost"

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"concurrency": Role.RUNTIME, "endpoint": Role.CONTENT}


class _Run(BaseModel):
    """A frozen config: two CONTENT fields, one PATH field, one RUNTIME field and a nested block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: str
    seed: int = 42
    judge: _Inner = Field(default_factory=_Inner)

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "name": Role.CONTENT,
        "source": Role.PATH,
        "seed": Role.RUNTIME,
        "judge": Role.CONTENT,
    }


class _Partial(BaseModel):
    """A model whose declaration omits one field: a malformed declaration, not a model that declares none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    name: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"name": Role.CONTENT}


class _NoRoles(BaseModel):
    """A model that declares no ``IDENTITY_ROLES`` at all: an override of it is written verbatim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    name: str


class _Judge(BaseModel):
    """A block held in a list: ``leaf_paths`` enumerates its leaves, so the writer must write one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concurrency: int = 1

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"concurrency": Role.RUNTIME}


class _Panel(BaseModel):
    """A config whose one nested block is a list of models (the product's ``judges`` shape)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    judges: list[_Judge] = Field(default_factory=list)

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"name": Role.CONTENT, "judges": Role.CONTENT}


def _write(path: Path, text: str) -> Path:
    """Write *text* to *path* as UTF-8, creating the directory, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _draft() -> _Run:
    """A validated, frozen draft: the shape a consumer's spec has."""
    return _Run.model_validate({"name": "x", "source": "/data", "judge": {"endpoint": "a"}})


# --- the value ladder ----------------------------------------------------------------------------------------


def test_the_ladder_is_the_grammar_rcp_ships() -> None:
    """rcp 12: one parser, and it is ``yaml.safe_load(raw) if raw.strip() else ""``."""
    assert parse_value("5") == 5
    assert isinstance(parse_value("5"), int)
    assert parse_value("5.0") == 5.0
    assert parse_value("true") is True
    assert parse_value("yes") is True
    assert parse_value("null") is None
    assert parse_value("[a, b]") == ["a", "b"]
    assert parse_value("{k: v}") == {"k": "v"}
    assert parse_value("hello") == "hello"
    assert parse_value("") == ""
    assert parse_value("   ") == ""

    assert parse(["judge.concurrency=8"]) == (Override(path="judge.concurrency", value=8, source="--set"),)
    assert parse(["name="]) == (Override(path="name", value="", source="--set"),)


def test_parse_refuses_a_pair_that_is_not_key_value_and_a_repeated_path_with_two_values() -> None:
    """rcp 12: one error table, and a repeated path with two values is a refusal, not a silent last-wins."""
    with pytest.raises(Fault) as caught:
        parse(["nonsense"])
    assert "KEY=VALUE" in caught.value.message
    assert caught.value.details["refusal"] == "not_a_pair"

    with pytest.raises(Fault) as caught:
        parse(["judge.concurrency=8", "judge.concurrency=9"])
    assert "judge.concurrency" in caught.value.message
    assert caught.value.details["refusal"] == "repeated_path"

    assert parse(["judge.concurrency=8", "judge.concurrency=8"]) == parse(["judge.concurrency=8"])


def test_parse_value_imports_yaml_lazily_and_refuses_a_value_it_cannot_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C4's lazy half: an empty value needs no YAML reader; a malformed literal is refused by the table."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert parse_value("") == ""
    with pytest.raises(Fault) as caught:
        parse_value("5")
    assert caught.value.code == "optional.missing"
    monkeypatch.undo()

    with pytest.raises(Fault) as caught:
        parse_value("[1, 2")
    assert caught.value.details["refusal"] == "value_not_parsed"


# --- apply, on a frozen draft --------------------------------------------------------------------------------


def test_apply_returns_a_new_model_and_leaves_the_frozen_draft_unchanged() -> None:
    """C2: ``model_copy``, never a mutation: a frozen spec is the shape a config should have."""
    draft = _draft()
    applied = apply(draft, parse(["judge.concurrency=8"]), base=Path("/tmp"))
    assert applied is not draft
    assert applied.judge.concurrency == 8
    assert draft.judge.concurrency == 1


def test_an_override_enters_the_identity() -> None:
    """C3, emb-eval's cache bug: a CONTENT override changes the payload, so a cached digest cannot be reused."""
    draft = _draft()
    applied = apply(draft, parse(["judge.endpoint=b"]), base=Path("/tmp"))
    assert identity_of(applied)["judge"]["endpoint"] == "b"
    assert identity_of(applied) != identity_of(draft)


def test_base_is_required_and_the_override_paths_resolve_against_it() -> None:
    """C4, the second of the two bases: an override resolves against the base its caller names, never the cwd."""
    draft = _draft()
    with pytest.raises(TypeError):
        apply(draft, parse(["judge.concurrency=8"]))  # type: ignore[call-arg]
    applied = apply(draft, parse(["source=data/inputs"]), base=Path("/tmp/base"))
    assert applied.source == str((Path("/tmp/base") / "data/inputs").resolve())


def test_an_unknown_leaf_is_refused_with_the_path() -> None:
    """C6: a path that names no leaf is refused, and the message carries the path and the known leaves."""
    with pytest.raises(Fault) as caught:
        apply(_draft(), parse(["judge.nope=1"]), base=Path("/tmp"))
    assert "judge.nope" in caught.value.message
    assert "judge.concurrency" in caught.value.message
    assert caught.value.details["refusal"] == "not_a_leaf"

    with pytest.raises(Fault) as caught:
        apply(_draft(), parse(["name.first=1"]), base=Path("/tmp"))
    assert caught.value.details["refusal"] == "not_a_mapping"


def test_an_override_against_a_partial_role_declaration_is_refused(tmp_path: Path) -> None:
    """H1: a malformed declaration must not write a PATH override unresolved and silently.

    ``_role_of`` caught every ``Fault`` from ``roles_of`` and returned ``None``, so a leaf the declaration forgot
    took the override value verbatim instead of resolving it against the base -- a wrong value in the identity,
    not a refusal.
    """
    draft = _Partial.model_construct(source="data/inputs.jsonl", name="x")
    with pytest.raises(Fault) as caught:
        apply(draft, parse(["source=other/inputs.jsonl"]), base=tmp_path)
    assert "source" in caught.value.message
    assert caught.value.code == "value_error"


def test_an_override_on_a_model_with_no_role_declaration_is_written_verbatim(tmp_path: Path) -> None:
    """The presence case that stays silent: no declaration means no PATH leaf, so no base is applied."""
    draft = _NoRoles.model_construct(source="data/inputs.jsonl", name="x")
    applied = apply(draft, parse(["source=other/inputs.jsonl"]), base=tmp_path)
    assert applied.source == "other/inputs.jsonl"


def test_apply_writes_through_a_draft_whose_nested_block_is_still_a_mapping() -> None:
    """The pipeline's draft is ``model_construct``'s: a nested block is a mapping, and the write goes through it."""
    draft = _Run.model_construct(name="x", source="/data", judge={"endpoint": "a"})
    applied = apply(draft, parse(["judge.concurrency=8"]), base=Path("/tmp"))
    assert applied.judge == {"endpoint": "a", "concurrency": 8}
    assert draft.judge == {"endpoint": "a"}


def test_unset_removes_a_leaf() -> None:
    """C5: a removal is the one structured edit the grammar cannot express."""
    draft = apply(_draft(), parse(["judge.concurrency=8"]), base=Path("/tmp"))
    assert unset(draft, "judge.concurrency").judge.concurrency == 1

    with pytest.raises(Fault) as caught:
        unset(_draft(), "name")
    assert caught.value.details["refusal"] == "no_default"

    with pytest.raises(Fault) as caught:
        unset(_draft(), "judge.nope")
    assert caught.value.details["refusal"] == "not_a_leaf"


# --- the precedence table ------------------------------------------------------------------------------------


def test_from_env_reads_only_the_prefix_names() -> None:
    """rcp 17: the prefix is a parameter, and a name under it that maps to no leaf is refused."""
    overrides = from_env(
        {"P_NAME": "y", "P_JUDGE_CONCURRENCY": "8", "OTHER_NAME": "z"},
        prefix="P",
        model=_Run,
    )
    assert overrides == (
        Override(path="name", value="y", source="env:P_NAME"),
        Override(path="judge.concurrency", value=8, source="env:P_JUDGE_CONCURRENCY"),
    )

    with pytest.raises(Fault) as caught:
        from_env({"P_NOPE": "1"}, prefix="P", model=_Run)
    assert "P_NOPE" in caught.value.message
    assert caught.value.details["refusal"] == "unknown_env"

    with pytest.raises(Fault) as caught:
        from_env({"NAME": "1"}, prefix="", model=_Run)
    assert caught.value.details["refusal"] == "no_prefix"


def test_precedence_is_file_then_env_then_flag_and_the_winner_is_recorded() -> None:
    """rcp 17: ``file < env < flag``; the winning value's ``source`` is on the record that won."""
    overrides = (
        Override(path="judge.concurrency", value=2, source="file:dev.yaml"),
        *from_env({"P_JUDGE_CONCURRENCY": "3"}, prefix="P", model=_Run),
        *parse(["judge.concurrency=4"]),
    )
    applied = apply(_draft(), overrides, base=Path("/tmp"))
    assert applied.judge.concurrency == 4
    winner = [override for override in overrides if override.path == "judge.concurrency"][-1]
    assert winner.source == "--set"


# --- the layered merge ---------------------------------------------------------------------------------------


def test_merge_layers_refuses_a_content_conflict_and_allows_a_runtime_override() -> None:
    """rcp 23: a CONTENT field two layers set differently is refused; a RUNTIME field may be overridden."""
    roles = roles_of(_Run)
    merged = merge_layers({"name": "a", "seed": 1}, {"name": "a", "seed": 2}, roles=roles)
    assert merged == {"name": "a", "seed": 2}

    with pytest.raises(Fault) as caught:
        merge_layers({"name": "a"}, {"name": "b"}, roles=roles)
    assert "name" in caught.value.message
    assert caught.value.details["refusal"] == "content_conflict"

    with pytest.raises(Fault) as caught:
        merge_layers({"judge": {"endpoint": "a"}}, {"judge": {"endpoint": "b"}}, roles=roles)
    assert caught.value.details["refusal"] == "content_conflict"

    with pytest.raises(Fault) as caught:
        merge_layers({"nope": 1}, {"nope": 2}, roles=roles)
    assert caught.value.details["refusal"] == "no_role"


def test_a_list_of_models_is_a_branch_at_both_ends_of_the_grammar() -> None:
    """M5: ``leaf_paths``/``from_env`` enumerate ``judges.concurrency``; ``apply`` must write it, not refuse it.

    The two halves of "what is a leaf" disagreed: the enumeration descends through a list of models and the
    writer refused it ("judges runs through judges, which is not a model"), so an environment name the library
    itself produced could not be applied.
    """
    assert "judges.concurrency" in leaf_paths(_Panel)
    records = from_env({"P_JUDGES_CONCURRENCY": "3"}, prefix="P", model=_Panel)
    assert [record.path for record in records] == ["judges.concurrency"]

    draft = _Panel.model_validate({"name": "x", "judges": [{"concurrency": 1}, {"concurrency": 2}]})
    applied = apply(draft, records, base=Path("/data"))
    assert [judge.concurrency for judge in applied.judges] == [3, 3]


def test_deep_merge_reports_each_leaf_the_child_changes() -> None:
    """M8: one merge rule. ``on_diff`` is how the raising layer merge reads the same recursion."""
    seen: list[tuple[tuple[str, ...], Any, Any]] = []
    merged = deep_merge(
        {"a": {"b": 1}, "keep": 2},
        {"a": {"b": 2}},
        on_diff=lambda path, base, child: seen.append((path, base, child)),
    )
    assert merged == {"a": {"b": 2}, "keep": 2}
    assert seen == [(("a", "b"), 1, 2)]
    assert deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}


def test_a_layer_value_that_only_compares_equal_is_a_conflict() -> None:
    """L10: ``==`` makes ``seed: true`` equal to ``seed: 1``, so the base won with no record.

    The compare is the canonical form every identity in the library is taken over, so two values are equal only
    when they hash equal.
    """
    roles = {"seed": Role.CONTENT}
    with pytest.raises(Fault) as caught:
        merge_layers({"seed": True}, {"seed": 1}, roles=roles)
    assert caught.value.details["refusal"] == "content_conflict"
    assert merge_layers({"seed": 1}, {"seed": 1}, roles=roles) == {"seed": 1}
    assert merge_layers({"seed": True}, {"seed": True}, roles=roles) == {"seed": True}


def test_validate_or_fault_maps_a_validation_error_to_the_one_fault(tmp_path: Path) -> None:
    """M9: the ``ValidationError`` -> ``Fault`` tail is one function, and both loaders call it."""
    draft = resolve(read(_write(tmp_path / "doc.yaml", "name: x\nsource: data\njudge:\n  concurrency: nope\n")), _Run)
    with pytest.raises(Fault) as caught:
        validate_or_fault(draft, model=_Run, source="doc.yaml")
    assert caught.value.code == "value_error"
    assert caught.value.details["errors"][0]["field"] == "judge.concurrency"
    assert "doc.yaml" in caught.value.message


# --- the pipeline, and the order it runs in ------------------------------------------------------------------


def test_pipeline_runs_read_resolve_apply_validate_identity(tmp_path: Path) -> None:
    """C7, the contract's order: the payload carries the override, and no-override equals ``load``."""
    path = _write(tmp_path / "doc.yaml", "name: x\nsource: data\njudge:\n  endpoint: a\n")
    overrides = parse(["judge.endpoint=b"])
    applied = pipeline(path, _Run, base=tmp_path, overrides=overrides)
    assert isinstance(applied, Applied)
    assert applied.model.judge.endpoint == "b"
    assert applied.payload == {"name": "x", "judge": {"endpoint": "b"}}
    assert applied.overrides == overrides
    assert pipeline(path, _Run).model == load(path, _Run)


def test_pipeline_refuses_an_invalid_override_in_validate_after_apply(tmp_path: Path) -> None:
    """C7: apply before validate, so the invalid value is the one validation sees (the order's whole point)."""
    path = _write(tmp_path / "doc.yaml", "name: x\nsource: data\n")
    with pytest.raises(Fault) as caught:
        pipeline(path, _Run, base=tmp_path, overrides=parse(["judge.concurrency=not-a-number"]))
    problem = caught.value.details["errors"][0]
    assert problem["field"] == "judge.concurrency"
    assert problem["expected"] == "int"
    # H2: the override is what set the bad value, so it is what the refusal blames -- not the file.
    assert problem["source"] == "--set"
    assert caught.value.message.startswith("--set judge.concurrency:")
    assert "fix the override" in (caught.value.hint or "")
    assert problem["input"] == "not-a-number"


def test_pipeline_refuses_an_override_with_no_base(tmp_path: Path) -> None:
    """The base is stated, not defaulted: the document's directory is not the override's base."""
    path = _write(tmp_path / "doc.yaml", "name: x\nsource: data\n")
    with pytest.raises(Fault) as caught:
        pipeline(path, _Run, overrides=parse(["judge.concurrency=8"]))
    assert caught.value.details["refusal"] == "no_base"


# --- the published error table -------------------------------------------------------------------------------


def test_value_errors_is_the_published_error_table() -> None:
    """rcp 12: one table, one code and one template per refusal, and every code is a fault code."""
    assert {code for code, _ in VALUE_ERRORS.values()} <= {code for _, code, _owner in FAULT_CODES}
    assert set(VALUE_ERRORS) == {
        "not_a_pair",
        "repeated_path",
        "value_not_parsed",
        "not_a_leaf",
        "not_a_mapping",
        "no_default",
        "no_base",
        "no_prefix",
        "unknown_env",
        "content_conflict",
        "no_role",
    }
    for template in VALUE_ERRORS.values():
        assert template[1]


def test_override_is_a_frozen_record_of_path_value_and_source() -> None:
    """The record names the path, the parsed value and where it came from, and it does not change."""
    override = Override(path="judge.concurrency", value=8, source="--set")
    assert (override.path, override.value, override.source) == ("judge.concurrency", 8, "--set")
    with pytest.raises(FrozenInstanceError):
        override.value = 9  # type: ignore[misc]
