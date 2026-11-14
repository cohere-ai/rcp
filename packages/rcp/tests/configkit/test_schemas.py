"""The schema vocabulary: one tag base, one document record, one registry, one artifact home.

The library owns no repository's document (the concepts' rule): a consumer declares its ``DocumentId`` records and
passes them in, so no consumer name lands in module state. A record names every spelling a reader accepts and the
one spelling a consumer may delete (rcp-30), and a malformed record is an error rather than a silent no-declaration.
"""

from __future__ import annotations

import pytest
from configkit import schemas
from configkit.schemas import (
    ArtifactHome,
    DocumentId,
    SchemaRegistry,
    SchemaTagged,
    accepted_schema_ids,
    home_of,
    registry,
)
from pydantic import ValidationError

#: A consumer's document record: the current spelling, the pre-rename one, and which of them may be deleted.
RESULT_RECORD = DocumentId(
    name="result-record",
    accepted_ids=("rcp.result-record.v1", "rcp-ndcg.result-record.v1"),
    removable="rcp-ndcg.result-record.v1",
)

#: A second record, so the registry has two documents and two spellings each.
CALIBRATION = DocumentId(
    name="calibration",
    accepted_ids=("rcp.calibration.v1", "rcp-ndcg.calibration.v1"),
    removable="rcp-ndcg.calibration.v1",
)


def test_accepted_schema_ids_returns_the_records_ids() -> None:
    """rcp-26: the pair a reader accepts comes from the record the consumer declared, not from a table here."""
    assert accepted_schema_ids(RESULT_RECORD) == ("rcp.result-record.v1", "rcp-ndcg.result-record.v1")
    assert accepted_schema_ids(CALIBRATION) == CALIBRATION.accepted_ids


def test_the_removable_spelling_is_named_and_accepted() -> None:
    """rcp-30: the alias list says which spelling a consumer may delete, and it is one a reader accepts."""
    assert RESULT_RECORD.removable == "rcp-ndcg.result-record.v1"
    assert RESULT_RECORD.removable in accepted_schema_ids(RESULT_RECORD)


def test_a_record_whose_removable_spelling_is_not_accepted_is_refused() -> None:
    """A removable spelling nobody accepts is a declaration error, not a no-op."""
    with pytest.raises(ValueError, match="removable"):
        DocumentId(name="x", accepted_ids=("rcp.x.v1",), removable="rcp-ndcg.x.v1")


@pytest.mark.parametrize(
    ("accepted", "removable"),
    [((), ""), (("rcp.x.v1", "rcp.x.v1"), "rcp.x.v1"), (("rcp.x.v1",), "")],
)
def test_a_malformed_record_is_refused(accepted: tuple[str, ...], removable: str) -> None:
    """No ids, a repeated id, or no removable spelling: each is a declaration nobody made."""
    with pytest.raises(ValueError):
        DocumentId(name="x", accepted_ids=accepted, removable=removable)


def test_an_empty_name_is_refused() -> None:
    """The name is what ``home_of`` derives the artifact's home from, so it cannot be blank."""
    with pytest.raises(ValueError):
        DocumentId(name=" ", accepted_ids=("rcp.x.v1",), removable="rcp.x.v1")


def test_home_of_returns_directory_file_name_and_schema_id() -> None:
    """rcp-46: a cross-command artifact's home and schema id, derived from the record alone."""
    home = home_of(RESULT_RECORD)
    assert isinstance(home, ArtifactHome)
    assert home == ArtifactHome(
        directory="result-record", file_name="result-record.json", schema_id="rcp.result-record.v1"
    )
    # The schema id is the spelling a writer emits: the first accepted id, never the removable one.
    assert home.schema_id == accepted_schema_ids(RESULT_RECORD)[0]
    assert home.schema_id != RESULT_RECORD.removable


def test_registry_indexes_every_spelling() -> None:
    """emb-train 25: one registry answers "which document is this id?" for every spelling a reader accepts."""
    index = registry([RESULT_RECORD, CALIBRATION])
    assert isinstance(index, SchemaRegistry)
    assert index.names == ("result-record", "calibration")
    assert index.document_for("rcp.result-record.v1") is RESULT_RECORD
    assert index.document_for("rcp-ndcg.calibration.v1") is CALIBRATION
    assert index.document_for("rcp.unknown.v1") is None
    assert index.ids == accepted_schema_ids(RESULT_RECORD) + accepted_schema_ids(CALIBRATION)


def test_registry_refuses_two_documents_claiming_one_spelling() -> None:
    """A spelling names one document: two records for one id would make a reader's answer order-dependent."""
    twin = DocumentId(name="twin", accepted_ids=("rcp.result-record.v1",), removable="rcp.result-record.v1")
    with pytest.raises(ValueError, match="rcp.result-record.v1"):
        registry([RESULT_RECORD, twin])


def test_registry_refuses_two_documents_with_one_name() -> None:
    """The name keys the registry, so two records with one name are one document declared twice."""
    twin = DocumentId(name="result-record", accepted_ids=("rcp.other.v1",), removable="rcp.other.v1")
    with pytest.raises(ValueError, match="result-record"):
        registry([RESULT_RECORD, twin])


def test_schema_tagged_carries_the_id_and_no_default() -> None:
    """rcp-26: the base a tagged payload declares, with the id required (a schema field is derived, never defaulted)."""
    assert SchemaTagged.model_fields["schema_id"].is_required()
    assert SchemaTagged(schema_id="rcp.x.v1").schema_id == "rcp.x.v1"
    with pytest.raises(ValidationError):
        SchemaTagged()  # type: ignore[call-arg]


def test_the_module_exports_seven_names_and_holds_no_consumer_document() -> None:
    """The published surface is the seven names, and no document record is module state: both take a parameter."""
    assert set(schemas.__all__) == {
        "SchemaTagged",
        "DocumentId",
        "SchemaRegistry",
        "registry",
        "accepted_schema_ids",
        "ArtifactHome",
        "home_of",
    }
    assert not [name for name in vars(schemas) if name.isupper()]
