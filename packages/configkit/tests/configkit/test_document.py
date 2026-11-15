"""The one loader: the five v0 rules, the two bases, lazy YAML and the one validation-error mapper.

The five rules are ``emb-train``'s (``emb_train_cli/config.py:5-18``), kept and parameterised: the document is
UTF-8; a duplicate key is refused; the ``schema`` id is checked against an id the caller passes; a relative
``PATH`` value resolves against the document's own directory (an absolute one is kept); and the resolution
happens before anything hashes the payload. Two rules changed and the module says so: YAML is read (lazily, so a
JSON document never imports it) and ``extends`` is honoured.

Every document is built in ``tmp_path``: a test that resolved against the checkout would pass for the wrong
reason, and the two-base rule is only visible from a directory other than the working one.
"""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import pytest
from configkit.document import (
    Applied,
    Document,
    FieldTypes,
    config_error,
    deep_merge,
    leaf_paths,
    load,
    read,
    register_field_type,
    resolve,
    resolve_reference,
    validate,
)
from configkit.faults import Fault
from configkit.identity import hash_payload
from configkit.roles import Role, identity_of
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Doc(BaseModel):
    """A document with the id, two PATH leaves and one CONTENT leaf."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = ""
    source: str
    output: str
    name: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "schema_id": Role.CONTENT,
        "source": Role.PATH,
        "output": Role.PATH,
        "name": Role.CONTENT,
    }


class _Secrets(BaseModel):
    """A model whose first field is a SECRET: the redaction test's subject."""

    model_config = ConfigDict(extra="forbid")

    secret: int
    name: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"secret": Role.SECRET, "name": Role.CONTENT}


class _Inner(BaseModel):
    """A nested model with one leaf."""

    weight: int


class _Outer(BaseModel):
    """A model with a scalar leaf, a nested model and a list of models: three shapes ``leaf_paths`` walks."""

    name: str
    inner: _Inner
    items: list[_Inner]


class _Defaults(BaseModel):
    """A model whose nested block is a ``default_factory``: ``model_construct`` makes it a real instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inner: _Inner = Field(default_factory=lambda: _Inner(weight=1))

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"inner": Role.CONTENT}


class _Partial(BaseModel):
    """A model whose declaration omits one field: a malformed declaration, not a model that declares none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    name: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"name": Role.CONTENT}


class _NoRoles(BaseModel):
    """A model that declares no ``IDENTITY_ROLES`` at all: it declares no PATH leaf, so none is resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    name: str


def _write(path: Path, text: str) -> Path:
    """Write *text* to *path* as UTF-8, creating the directory, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the five v0 rules ---------------------------------------------------------------------------------------


def test_utf8_document_reads_and_a_decode_failure_names_the_file(tmp_path: Path) -> None:
    """Rule 1: the document is UTF-8; a file that is not UTF-8 is refused with its own name."""
    path = _write(tmp_path / "doc.yaml", "name: caf\u00e9\n")
    assert read(path).payload["name"] == "caf\u00e9"

    broken = tmp_path / "broken.yaml"
    broken.write_bytes(b"name: \xff\n")
    with pytest.raises(Fault) as caught:
        read(broken)
    assert "broken.yaml" in caught.value.message
    assert caught.value.code == "value_error"


def test_duplicate_key_is_refused_with_the_key_and_the_file(tmp_path: Path) -> None:
    """Rule 2: a duplicate key can hide a value, so YAML and JSON both refuse it."""
    yaml_doc = _write(tmp_path / "dup.yaml", "name: a\nname: b\n")
    with pytest.raises(Fault) as caught:
        read(yaml_doc)
    assert "'name'" in caught.value.message and "dup.yaml" in caught.value.message

    json_doc = _write(tmp_path / "dup.json", json.dumps({"name": "a"}).replace("}", ', "name": "b"}'))
    with pytest.raises(Fault) as caught:
        read(json_doc)
    assert "'name'" in caught.value.message and "dup.json" in caught.value.message


def test_schema_id_is_a_parameter_and_is_checked_only_when_given(tmp_path: Path) -> None:
    """Rule 3: the required id is the caller's parameter; a missing or different id is refused by name."""
    matching = _write(tmp_path / "matching.yaml", "schema: p.doc.v1\nname: x\n")
    assert read(matching, schema_id="p.doc.v1").payload["name"] == "x"

    absent = _write(tmp_path / "absent.yaml", "name: x\n")
    assert read(absent).payload["name"] == "x"  # no id named, no check: the model may declare it
    with pytest.raises(Fault) as caught:
        read(absent, schema_id="p.doc.v1")
    assert "p.doc.v1" in caught.value.message and "absent.yaml" in caught.value.message

    other = _write(tmp_path / "other.yaml", "schema: p.other.v1\nname: x\n")
    with pytest.raises(Fault) as caught:
        read(other, schema_id="p.doc.v1")
    assert "p.other.v1" in caught.value.message and "p.doc.v1" in caught.value.message


def test_a_conforming_document_round_trips_through_load_with_its_schema_id(tmp_path: Path) -> None:
    """B3: the id is the document's own key, so ``load(schema_id=...)`` accepts the document ``read`` accepts.

    ``read`` used to leave ``schema`` in the payload, and ``resolve`` then refused it as a key the model does not
    declare (``extra="forbid"``): every conforming document was rejected one stage after it was checked.
    """
    path = _write(tmp_path / "doc.yaml", "schema: p.doc.v1\nsource: data\noutput: runs\nname: x\n")
    document = read(path, schema_id="p.doc.v1")
    assert document.schema == "p.doc.v1"
    assert "schema" not in document.payload

    loaded = load(path, _Doc, schema_id="p.doc.v1")
    assert loaded.name == "x"
    assert loaded.source == str((tmp_path / "data").resolve())


def test_a_document_that_declares_no_schema_records_none(tmp_path: Path) -> None:
    """A caller that names no id checks none, and the record says the document declared no tag."""
    document = read(_write(tmp_path / "untagged.yaml", "name: x\n"))
    assert document.schema is None
    assert document.schema_declared is False


def test_a_declared_null_schema_is_distinguishable_from_no_schema_key(tmp_path: Path) -> None:
    """CP-1: ``schema:`` and ``schema: null`` declare the key with a null value; an absent key declares none.

    The value alone cannot tell the two cases apart, so the record carries the presence bit: a consumer that
    re-adds a declared key to its model (the product's loader) must keep a declared null, not drop it.
    """
    declared = read(_write(tmp_path / "null.yaml", "schema: null\nname: x\n"))
    assert declared.schema is None
    assert declared.schema_declared is True
    assert "schema" not in declared.payload

    absent = read(_write(tmp_path / "absent.yaml", "name: x\n"))
    assert absent.schema is None
    assert absent.schema_declared is False


def test_resolves_a_relative_path_against_the_documents_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 4: the document's directory is the base, never the process's working directory."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    path = _write(
        tmp_path / "config" / "doc.yaml",
        "source: data/inputs.jsonl\noutput: runs\nname: x\n",
    )
    resolved = load(path, _Doc)
    assert resolved.source == str((tmp_path / "config" / "data/inputs.jsonl").resolve())
    assert resolved.output == str((tmp_path / "config" / "runs").resolve())
    assert not resolved.source.startswith(str(elsewhere))


def test_resolution_happens_before_hashing(tmp_path: Path) -> None:
    """Rule 5: two documents that differ only in where they live are two fingerprints, not one."""
    text = "source: data/inputs.jsonl\noutput: runs\nname: x\n"
    first = load(_write(tmp_path / "a" / "doc.yaml", text), _Doc)
    second = load(_write(tmp_path / "b" / "doc.yaml", text), _Doc)
    assert hash_payload(read(tmp_path / "a" / "doc.yaml").payload) == hash_payload(
        read(tmp_path / "b" / "doc.yaml").payload
    )
    assert hash_payload({"source": first.source}) != hash_payload({"source": second.source})


# --- the two bases, and the absolute-path rule ---------------------------------------------------------------


def test_two_bases_the_document_directory_and_an_absolute_path(tmp_path: Path) -> None:
    """A relative PATH resolves against the document; an absolute one is kept unchanged."""
    absolute = str((tmp_path / "absolute" / "out").resolve())
    path = _write(tmp_path / "config" / "doc.yaml", f"source: data/inputs.jsonl\noutput: {absolute}\nname: x\n")
    document = read(path)
    assert document.base == (tmp_path / "config").resolve()
    resolved = resolve(document, _Doc)
    assert resolved.source == str((tmp_path / "config" / "data/inputs.jsonl").resolve())
    assert resolved.output == absolute


def test_a_path_fields_row_written_as_one_string_is_refused(tmp_path: Path) -> None:
    """A string is a sequence of characters: a dotted row would walk ``s``, ``o``, ``u``... and resolve nothing."""
    path = _write(tmp_path / "doc.yaml", "source: data\noutput: runs\nname: x\n")
    with pytest.raises(Fault) as caught:
        resolve(read(path), _Doc, rows=["source"])
    assert "path_fields" in caught.value.message


def test_a_partial_role_declaration_is_refused_not_read_as_no_declaration(tmp_path: Path) -> None:
    """H1: a malformed declaration must not leave every PATH leaf silently unresolved.

    ``_path_rows`` caught every ``Fault`` from ``path_fields`` and read it as "this model declares no PATH leaf",
    so a model whose declaration omits one field resolved nothing and said nothing -- the ``model.checkpoint``
    never resolves defect the library exists to remove.
    """
    path = _write(tmp_path / "doc.yaml", "source: data/inputs.jsonl\nname: x\n")
    with pytest.raises(Fault) as caught:
        resolve(read(path), _Partial)
    assert "source" in caught.value.message
    assert caught.value.code == "value_error"


def test_a_model_with_no_role_declaration_resolves_no_declared_path(tmp_path: Path) -> None:
    """The presence case the loader may still read as "no roles": no declaration, no PATH leaf, value unchanged."""
    path = _write(tmp_path / "doc.yaml", "source: data/inputs.jsonl\nname: x\n")
    draft = resolve(read(path), _NoRoles)
    assert draft.source == "data/inputs.jsonl"


# --- lazy YAML -----------------------------------------------------------------------------------------------


def test_yaml_absent_raises_optional_missing_with_the_callers_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C4: a JSON document never imports YAML; a YAML one refuses with the caller's install line."""
    yaml_doc = _write(tmp_path / "doc.yaml", "name: x\n")
    json_doc = _write(tmp_path / "doc.json", json.dumps({"name": "x"}))
    monkeypatch.setitem(sys.modules, "yaml", None)

    hint = "pip install <the-callers-extra>"
    with pytest.raises(Fault) as caught:
        read(yaml_doc, yaml_hint=hint)
    assert caught.value.code == "optional.missing"
    assert caught.value.hint == hint
    assert hint in caught.value.message

    assert read(json_doc).payload == {"name": "x"}


def test_the_default_yaml_hint_is_the_distributions_own_extra() -> None:
    """The default install line named no distribution ("the distribution that ships this library"), which after the
    carve is the reader's own: the hint is the extra this manifest declares, so copying it installs the reader."""
    from configkit.document import _YAML_HINT

    manifest = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "yaml" in manifest["project"]["optional-dependencies"]
    assert _YAML_HINT == f"pip install {manifest['project']['name']}[yaml]"


# --- the validation-error mapper -----------------------------------------------------------------------------


def test_config_error_names_the_field_the_expected_type_the_known_keys_and_redacts_a_secret() -> None:
    """rcp 14: one fault per problem, with the field path, the expected type, the known keys and no secret."""
    with pytest.raises(ValidationError) as caught:
        _Secrets.model_validate({"secret": "abc", "name": "x"})
    fault = config_error(caught.value, model=_Secrets, source="dev.yaml")
    problem = fault.details["errors"][0]
    assert problem["field"] == "secret"
    assert problem["expected"] == "integer"
    assert problem["input"] == "<redacted>"
    assert "abc" not in fault.message
    assert "dev.yaml" in fault.message and "secret" in fault.message

    with pytest.raises(ValidationError) as caught:
        _Secrets.model_validate({"secret": 1, "name": "x", "nam": "y"})
    fault = config_error(caught.value, model=_Secrets, source="dev.yaml")
    problem = fault.details["errors"][0]
    assert problem["field"] == "nam"
    assert problem["known"] == ["name", "secret"]
    assert problem["did_you_mean"] == "name"


class _Slurm(BaseModel):
    """A union member: a discriminated union's tag is in the validation location and is no key a reader wrote."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"kind": Role.CONTENT, "partition": Role.CONTENT}

    kind: Literal["slurm"] = "slurm"
    partition: int


class _Local(BaseModel):
    """The other member, so the union has a discriminator to name."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"kind": Role.CONTENT}

    kind: Literal["local"] = "local"


class _Judge(BaseModel):
    """The block `--set judge.…` validates on its own."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"backend": Role.CONTENT}

    backend: Annotated[_Slurm | _Local, Field(discriminator="kind")] = Field(default_factory=_Local)


class _Run(BaseModel):
    """The document whose `judge` block holds the union."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"judge": Role.CONTENT}

    judge: _Judge


class _Steps(BaseModel):
    """A list of one closed set: the annotation names no item, so only the walked item schema can say what
    the value should have been."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"steps": Role.CONTENT}

    steps: list[Literal["retrieve", "calibrate"]]


def _union_error() -> ValidationError:
    """The one error a union member's bad value raises, at its raw pydantic location."""
    with pytest.raises(ValidationError) as caught:
        _Run.model_validate({"judge": {"backend": {"kind": "slurm", "partition": "nope"}}})
    return caught.value


def test_a_union_members_tag_is_not_a_field_the_reader_wrote() -> None:
    """H2: pydantic names the member it tried (``slurm``); the document has no such key.

    The product's ``validation_problems`` dropped it with ``_walk``, and the replacement reported
    ``judge.backend.slurm.partition``: a path no document has, and one no ``--set`` can address.
    """
    exc = _union_error()
    assert [part for part in exc.errors(include_url=False)[0]["loc"]] == ["judge", "backend", "slurm", "partition"]
    fault = config_error(exc, model=_Run, source="dev.yaml")
    problem = fault.details["errors"][0]
    assert problem["field"] == "judge.backend.partition"
    assert problem["expected"] == "integer"
    assert fault.message.startswith("dev.yaml: judge.backend.partition:")
    assert "did you mean" not in fault.message


def test_expected_names_the_choices_of_a_literal_inside_a_list() -> None:
    """R3: pydantic's context holds the choices and the walk reaches the list's item schema, so a value that is
    not one of them is refused naming them. Spelled from the annotation, this problem named nothing at all."""
    with pytest.raises(ValidationError) as caught:
        _Steps.model_validate({"steps": ["retrieve", "calibrat"]})
    problem = config_error(caught.value, model=_Steps, source="dev.yaml").details["errors"][0]
    assert problem["field"] == "steps.1"
    assert problem["expected"] == "'retrieve' or 'calibrate'"


def test_an_override_that_set_the_field_is_blamed_on_the_override() -> None:
    """H2: with ``overrides=``, a ``--set`` typo is blamed on the override, not on the file it overrode."""
    fault = config_error(
        _union_error(),
        model=_Run,
        source="dev.yaml",
        overrides=["judge.backend.partition=abc"],
    )
    problem = fault.details["errors"][0]
    assert problem["source"] == "--set"
    assert fault.message.startswith("--set judge.backend.partition:")
    assert fault.hint is not None and "override" in fault.hint
    assert fault.cli_hint is not None and "--set" in fault.cli_hint


def test_a_prefix_names_the_validated_value_inside_the_overrides_namespace() -> None:
    """H2: ``--set judge.x=…`` validates the judge block alone, so the problem's path carries the prefix."""
    with pytest.raises(ValidationError) as caught:
        _Judge.model_validate({"backend": {"kind": "slurm", "partition": "nope"}})
    fault = config_error(
        caught.value,
        model=_Judge,
        source="run config",
        prefix="judge",
        overrides=["judge.backend.partition=abc"],
    )
    problem = fault.details["errors"][0]
    assert problem["field"] == "judge.backend.partition"
    assert problem["source"] == "--set"


def test_a_caller_supplied_hint_replaces_the_default() -> None:
    """H2: a caller that knows the next step passes ``hint=``; no default is invented on top of it."""
    fault = config_error(_union_error(), model=_Run, source="dev.yaml", hint="fix the judge block")
    assert fault.hint == "fix the judge block"
    assert fault.cli_hint is None


class _Broken(BaseModel):
    """A model whose role declaration is malformed: ``other`` has no Role, so ``roles_of`` refuses it."""

    model_config = ConfigDict(extra="forbid")

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"token": Role.SECRET}

    token: int
    other: str = "x"


def test_a_malformed_role_declaration_never_renders_the_value() -> None:
    """H8: a model whose ``IDENTITY_ROLES`` is malformed made the error renderer fall back to the raw value.

    ``_redacted_input`` read a ``roles_of`` refusal as "no field is a secret" and rendered the input verbatim,
    so a value written for a SECRET field reached the message and ``details``. A refusal may not leak: every
    string is redacted instead.
    """
    with pytest.raises(ValidationError) as caught:
        _Broken.model_validate({"token": "s3cr3t-value"})
    fault = config_error(caught.value, model=_Broken, source="dev.yaml")
    assert fault.details["errors"][0]["input"] == "<redacted>"
    assert "s3cr3t-value" not in fault.message
    assert "s3cr3t-value" not in json.dumps(fault.details)


# --- extends ------------------------------------------------------------------------------------------------


def test_an_unknown_key_is_refused_by_the_loader_not_dropped(tmp_path: Path) -> None:
    """A typo in a document fails here, not silently: ``model_construct`` would drop the key."""
    path = _write(tmp_path / "typo.yaml", "source: data\noutput: runs\nnam: x\n")
    with pytest.raises(Fault) as caught:
        load(path, _Doc)
    assert "'nam'" in caught.value.message
    assert caught.value.details["known"] == ["name", "output", "schema_id", "source"]
    # M13: the refusal names the file, the way ``config_error`` names its source on every problem.
    assert "typo.yaml" in caught.value.message
    assert caught.value.details["source"] == str(path)


def test_extends_merges_the_base_under_the_child(tmp_path: Path) -> None:
    """ds-B1: the base is deep-merged *under* the child, relative to the extending file, and popped."""
    _write(
        tmp_path / "base.yaml",
        "name: base\nscalar: 1\nnested:\n  a: 1\n  b: 2\n",
    )
    child = _write(
        tmp_path / "config" / "child.yaml",
        "extends: ../base.yaml\nname: child\nnested:\n  b: 3\n",
    )
    payload = read(child).payload
    assert payload == {"name": "child", "scalar": 1, "nested": {"a": 1, "b": 3}}

    with pytest.raises(Fault) as caught:
        read(child, extends=False)
    assert "extends" in caught.value.message and "child.yaml" in caught.value.message


def test_a_base_configs_relative_value_resolves_against_the_bases_directory(tmp_path: Path) -> None:
    """R3: the two-base rule -- a base file's relative value resolves against the base's own directory, not the
    child's, so the hook runs on each file's payload with that file's directory *before* the merge."""
    _write(tmp_path / "base.yaml", "name: base\npath: rows.jsonl\n")
    child = _write(tmp_path / "config" / "child.yaml", "extends: ../base.yaml\nname: child\n")

    def rebase(payload: dict[str, Any], directory: Path) -> dict[str, Any]:
        return {key: str((directory / value).resolve()) if key == "path" else value for key, value in payload.items()}

    assert read(child, on_file=rebase).payload == {"name": "child", "path": str(tmp_path / "rows.jsonl")}


def test_a_childs_relative_value_resolves_against_the_childs_directory(tmp_path: Path) -> None:
    """R3: the same rule for the extending file, and the hook is optional: ``on_file=None`` (the default)
    reads the files unchanged."""
    _write(tmp_path / "base.yaml", "name: base\npath: rows.jsonl\n")
    child = _write(tmp_path / "config" / "child.yaml", "extends: ../base.yaml\nnotes: notes.md\n")

    def rebase(payload: dict[str, Any], directory: Path) -> dict[str, Any]:
        return {
            key: str((directory / value).resolve()) if key in {"path", "notes"} else value
            for key, value in payload.items()
        }

    assert read(child, on_file=rebase).payload == {
        "name": "base",
        "path": str(tmp_path / "rows.jsonl"),
        "notes": str(tmp_path / "config" / "notes.md"),
    }
    assert (
        read(child, on_file=None).payload
        == read(child).payload
        == {
            "name": "base",
            "path": "rows.jsonl",
            "notes": "notes.md",
        }
    )


def test_a_cycle_is_refused_naming_both_files(tmp_path: Path) -> None:
    """ds-B1: a file that reaches itself is refused, and the message names the chain."""
    first = _write(tmp_path / "first.yaml", "extends: second.yaml\nname: first\n")
    _write(tmp_path / "second.yaml", "extends: first.yaml\nname: second\n")
    with pytest.raises(Fault) as caught:
        read(first)
    assert "first.yaml" in caught.value.message and "second.yaml" in caught.value.message


def test_a_missing_extends_base_is_a_fault_naming_the_child_and_the_base(tmp_path: Path) -> None:
    """M14: a bare ``FileNotFoundError`` named neither the child, the base reference nor the fix."""
    child = _write(tmp_path / "config" / "child.yaml", "extends: ../missing.yaml\nname: child\n")
    with pytest.raises(Fault) as caught:
        read(child)
    assert caught.value.code == "os_error"
    assert "child.yaml" in caught.value.message
    assert "../missing.yaml" in caught.value.message
    assert caught.value.details["base"] == "../missing.yaml"
    assert caught.value.hint is not None and "missing.yaml" in caught.value.hint


# --- the rest of the module's surface -----------------------------------------------------------------------


def test_deep_merge_puts_the_base_under_the_child_recursively(tmp_path: Path) -> None:
    """``deep_merge`` is the ``extends`` rule's one implementation: mappings merge, anything else is replaced."""
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "items": [1, 2]}
    child = {"a": 2, "nested": {"y": 3}, "items": [9]}
    merged = deep_merge(base, child)
    assert merged == {"a": 2, "nested": {"x": 1, "y": 3}, "items": [9]}
    assert merged["nested"] is not base["nested"]
    assert merged["items"] is not child["items"]
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}, "items": [1, 2]}
    assert child == {"a": 2, "nested": {"y": 3}, "items": [9]}


def test_resolve_returns_an_unvalidated_draft_and_validate_rebuilds_it(tmp_path: Path) -> None:
    """``resolve`` is ``model_construct``; ``validate`` is the only stage that runs pydantic."""
    path = _write(tmp_path / "doc.yaml", "source: data\noutput: runs\nname: x\n")
    draft = resolve(read(path), _Doc)
    assert isinstance(draft, _Doc)
    assert validate(draft) == _Doc(
        source=str((tmp_path / "data").resolve()), output=str((tmp_path / "runs").resolve()), name="x"
    )

    invalid = _write(tmp_path / "invalid.yaml", "source: data\noutput: runs\n")
    draft = resolve(read(invalid), _Doc)
    with pytest.raises(ValidationError):
        validate(draft)
    with pytest.raises(Fault) as caught:
        load(invalid, _Doc)
    assert caught.value.details["errors"][0]["field"] == "name"


def test_validate_reads_the_drafts_nested_model_back_as_data() -> None:
    """``model_construct`` fills a ``default_factory`` model as an instance; ``validate`` still checks its values."""
    draft = _Defaults.model_construct(inner=_Inner.model_construct(weight="not-a-number"))
    with pytest.raises(ValidationError):
        validate(draft)


def test_field_types_are_registered_per_registry_and_never_globally() -> None:
    """rcp 19: the registry is the caller's, a duplicate registration is refused, and it leaks nowhere."""

    class _Endpoint(BaseModel):
        url: str

    class _Service(BaseModel):
        endpoint: _Endpoint

    registry = FieldTypes()
    register_field_type(registry, _Endpoint, lambda value: {**value, "url": f"https://{value['url']}"})
    draft = _Service.model_construct(endpoint={"url": "host/v1"})
    assert validate(draft, field_types=registry).endpoint.url == "https://host/v1"
    assert validate(draft).endpoint.url == "host/v1"
    assert FieldTypes().validator_for(_Endpoint) is None
    with pytest.raises(Fault):
        register_field_type(registry, _Endpoint, lambda value: value)


def test_field_types_schema_hook_composes_the_registered_hook() -> None:
    """The registry composes the JSON schema: the validator's own hook, else pydantic's."""

    class _Endpoint(BaseModel):
        url: str

    class _Validator:
        def __call__(self, value: Any) -> Any:
            return value

        @classmethod
        def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
            return {"type": "string", "format": "uri"}

    registry = FieldTypes()
    register_field_type(registry, _Endpoint, _Validator())
    assert registry.schema_hook(_Endpoint)(None, lambda core: {"type": "object"}) == {
        "type": "string",
        "format": "uri",
    }
    assert registry.schema_hook(_Inner)(None, lambda core: {"type": "object"}) == {"type": "object"}


def test_leaf_paths_lists_every_dotted_leaf_once() -> None:
    """emb-eval M22: one derived enumeration of a model's leaves, nested models and lists of models included."""
    assert leaf_paths(_Outer) == ("name", "inner.weight", "items.weight")


def test_resolve_reference_reports_the_attempts_it_made(tmp_path: Path) -> None:
    """rcp 22: a path, a shorthand and a bare name, each reporting the attempt that matched."""
    path = _write(tmp_path / "dev.yaml", "name: dev\n")

    attempts: list[str] = []
    assert resolve_reference(str(path), attempts=attempts) == {"name": "dev"}
    assert attempts == [f"path {str(path)!r}"]

    attempts = []
    assert resolve_reference("recipe:qwen3-reranker-0.6b", attempts=attempts) == {"recipe": "qwen3-reranker-0.6b"}
    assert attempts == [f"path {'recipe:qwen3-reranker-0.6b'!r}", "shorthand 'recipe:qwen3-reranker-0.6b'"]

    attempts = []
    assert resolve_reference("offline", attempts=attempts) == {"name": "offline"}
    assert attempts == [f"path {'offline'!r}", "shorthand 'offline'", "name 'offline'"]

    with pytest.raises(Fault):
        resolve_reference("   ", attempts=[])


def test_applied_is_a_frozen_record_of_the_model_the_overrides_and_the_payload() -> None:
    """ee-S5: the record a run's manifest reads back: the model, the overrides applied, the hashed payload."""
    model = _Doc.model_validate({"source": "/data", "output": "/runs", "name": "x"})
    applied = Applied(model=model, overrides=(), payload=identity_of(model))
    assert applied.model is model
    assert applied.payload == {"schema_id": "", "name": "x"}
    assert applied.overrides == ()
    with pytest.raises(FrozenInstanceError):
        applied.payload = {}  # type: ignore[misc]


def test_document_is_a_frozen_record_of_the_path_the_base_the_schema_and_the_payload(tmp_path: Path) -> None:
    """The reader returns a record, not a bare mapping: the base is what the resolver uses."""
    path = _write(tmp_path / "doc.yaml", "name: x\n")
    document = read(path)
    assert isinstance(document, Document)
    assert document.path == path
    assert document.base == tmp_path.resolve()
    assert document.payload == {"name": "x"}
    assert document.schema is None
