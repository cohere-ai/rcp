"""The product's loader over the library's: the two bases, the ladder, and the exit codes it keeps.

The loader delegates to :mod:`configkit.document` and :mod:`configkit.overrides`; what stays the product's is
the policy: each file's relative values resolve against the file that declares them, a config that is not a
mapping is a CONFIG refusal (exit 3), a base that is missing is MISSING_INPUT (exit 4), and no key is dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rcp.errors import ConfigError, MissingInputError, classify
from rcp.support.config import apply_overrides, deep_merge, load_config


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rebase(payload: dict[str, Any], directory: Path) -> dict[str, Any]:
    """A caller's ``relative``: a value under ``path`` resolves against the file that declares it."""
    return {key: str((directory / value).resolve()) if key == "path" else value for key, value in payload.items()}


def test_each_file_resolves_its_relative_values_against_its_own_directory(tmp_path: Path) -> None:
    """The two-base rule: a base config's path resolves against the base's directory, the child's against the
    child's -- the hook runs per file, before the merge, not once on the merged mapping."""
    _write(tmp_path / "base.yaml", "name: base\npath: rows.jsonl\n")
    child = _write(tmp_path / "config" / "child.yaml", "extends: ../base.yaml\nnotes: notes.md\n")

    def rebase(payload: dict[str, Any], directory: Path) -> dict[str, Any]:
        return {
            key: str((directory / value).resolve()) if key in {"path", "notes"} else value
            for key, value in payload.items()
        }

    assert load_config(child, relative=rebase) == {
        "name": "base",
        "path": str(tmp_path / "rows.jsonl"),
        "notes": str(tmp_path / "config" / "notes.md"),
    }


def test_deep_merge_returns_a_new_mapping_and_shares_nothing(tmp_path: Path) -> None:
    """``deep_merge`` is the ``extends`` rule's one implementation: mappings merge, anything else is replaced."""
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    update = {"a": 2, "nested": {"y": 3}}
    merged = deep_merge(base, update)
    assert merged == {"a": 2, "nested": {"x": 1, "y": 3}}
    assert merged["nested"] is not base["nested"] and merged["nested"] is not update["nested"]
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}} and update == {"a": 2, "nested": {"y": 3}}


def test_the_override_ladder_is_yaml_literals_and_an_empty_value_is_empty_text() -> None:
    """``--set k=10`` is an int, ``k=null`` is ``None``, ``k=[1, 2]`` a list, ``k=yes`` a boolean, ``k=`` ``""``."""
    got = apply_overrides({"a": {"b": 1}}, ["a.b=yes", "a.c=null", "d.e=[1, 2]", "f=", "g=text"])
    assert got == {"a": {"b": True, "c": None}, "d": {"e": [1, 2]}, "f": "", "g": "text"}


def test_a_malformed_override_is_a_config_error_with_the_ladder_hint() -> None:
    """A pair without ``=`` and a path through a scalar keep their messages and their CONFIG exit code."""
    with pytest.raises(ConfigError, match="not key=value"):
        apply_overrides({}, ["novalue"])
    with pytest.raises(ConfigError, match="is not a mapping"):
        apply_overrides({"a": 1}, ["a.b=2"])


def test_a_missing_base_is_a_missing_input_not_an_internal_failure(tmp_path: Path) -> None:
    """A base the host cannot read stays MISSING_INPUT (exit 4): the library's generic ``os_error`` would be
    INTERNAL, which tells a caller to report a bug instead of fixing the path."""
    child = _write(tmp_path / "child.yaml", "extends: missing.yaml\nname: child\n")
    with pytest.raises(FileNotFoundError):
        load_config(child)
    assert isinstance(classify(FileNotFoundError("missing.yaml")), MissingInputError)


def test_a_document_that_is_not_a_mapping_is_a_config_error(tmp_path: Path) -> None:
    """A YAML file that holds a list is CONFIG (exit 3), not an internal failure."""
    path = _write(tmp_path / "list.yaml", "- a\n- b\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert str(path) in caught.value.message


def test_a_stray_schema_key_is_not_dropped_silently(tmp_path: Path) -> None:
    """A run config declares no ``schema``: a stray key stays in the mapping, so the model refuses it as an
    unknown key instead of the reader dropping it."""
    path = _write(tmp_path / "run.yaml", "schema: not-a-run-config\nname: x\n")
    assert load_config(path) == {"schema": "not-a-run-config", "name": "x"}


@pytest.mark.parametrize("declared", ["schema: null\nname: x\n", "schema:\nname: x\n"])
def test_a_declared_null_schema_is_not_dropped_silently(tmp_path: Path, declared: str) -> None:
    """CP-1: a declared null is a declaration, not an absent key, so the loader keeps it for the model to refuse.

    The value alone (``None``) could not tell the two apart, so the library records whether the key was there;
    the product re-adds the key on that bit. Before the bit, both spellings were dropped and a run config
    accepted a key it declares no field for.
    """
    assert load_config(_write(tmp_path / "run.yaml", declared)) == {"schema": None, "name": "x"}
