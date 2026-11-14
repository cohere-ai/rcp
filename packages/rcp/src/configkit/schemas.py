"""The schema vocabulary: one tag base, one document record, one registry, one artifact's home.

A payload names the schema it was written as, in its ``schema`` field, and a reader accepts two spellings of that
name: the current id (``rcp.<name>.v1``) and the one every artifact written before the rename carries
(``rcp-ndcg.<name>.v1``). Both spellings are declared in one place, the consumer's
:class:`DocumentId` record -- the library owns no repository's document (the concepts' rule), so
:func:`accepted_schema_ids` and :func:`registry` take the records as a parameter and no consumer name lands in
module state.

:class:`DocumentId.removable` names the spelling a consumer may delete (rcp-30): the alias list states which one
goes, so a cleanup is a declaration rather than a guess, and ``audit.scan`` reports the call sites that still
write it. :func:`home_of` gives a cross-command artifact its declared directory, file name and schema id (rcp-46),
so a writer and a reader of the same artifact do not each spell the path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

__all__ = [
    "SchemaTagged",
    "DocumentId",
    "SchemaRegistry",
    "registry",
    "accepted_schema_ids",
    "ArtifactHome",
    "home_of",
]


class SchemaTagged(BaseModel):
    """A payload that carries the id of the schema it was written as, in a ``schema`` field (rcp-26).

    ``schema_id`` has no default: a schema field is derived at the write site, never defaulted (the rule ``audit``
    checks as A6), because a payload that does not say which schema it is cannot be read back safely.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_id: str


@dataclass(frozen=True)
class DocumentId:
    """One document's ids: the spellings a reader accepts, and the one a consumer may delete (emb-train 3).

    A consumer declares one record per document it writes (``RESULT_RECORD = DocumentId(...)``) and passes it to
    the reader and to :func:`registry`; the library holds no such record itself.

    Attributes:
        name: The document's name (``"result-record"``), the stem of every id and of :func:`home_of`'s names.
        accepted_ids: The spellings a reader accepts, the current one first (each a non-empty string, no repeats).
        removable: The spelling a consumer may delete (``"rcp-ndcg.result-record.v1"``); it is one of
            ``accepted_ids``, because a spelling nobody accepts is not removable, it is already gone.
    """

    name: str
    accepted_ids: tuple[str, ...]
    removable: str

    def __post_init__(self) -> None:
        """Refuse a declaration nobody made: a blank name, one string, no ids, a repeated id, or a bad removable id.

        Raises:
            ValueError: the record is malformed. The message names the field and the value.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"a DocumentId needs a non-empty name, got {self.name!r}")
        if isinstance(self.accepted_ids, str):
            raise ValueError(
                f"{self.name}: accepted_ids is a sequence of schema ids, not the string {self.accepted_ids!r}; "
                "a string is read one character at a time"
            )
        ids = tuple(self.accepted_ids)
        if not ids:
            raise ValueError(f"{self.name}: a DocumentId needs at least one accepted schema id")
        if any(not isinstance(spelling, str) or not spelling for spelling in ids):
            raise ValueError(f"{self.name}: every accepted schema id must be a non-empty string, got {ids!r}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.name}: accepted schema ids declared twice: {ids!r}")
        if self.removable not in ids:
            raise ValueError(
                f"{self.name}: the removable spelling {self.removable!r} is not one of the accepted ids {ids!r}; "
                "a consumer can only delete a spelling a reader accepts"
            )
        object.__setattr__(self, "accepted_ids", ids)


def accepted_schema_ids(document: DocumentId) -> tuple[str, ...]:
    """The spellings a reader accepts for *document*, current first.

    Args:
        document: The consumer's record for the document.

    Returns:
        The accepted schema ids, in the record's declared order (the current spelling first).
    """
    return tuple(document.accepted_ids)


@dataclass(frozen=True)
class ArtifactHome:
    """Where one cross-command artifact lives, relative to the consumer's own root (rcp-46).

    The library owns no repository path: the directory and the file name are derived from the document's name, and
    the consumer joins them with the root it owns.

    Attributes:
        directory: The artifact's directory name, relative to the consumer's root.
        file_name: The artifact's file name inside that directory.
        schema_id: The id the file carries in its ``schema`` field: the current spelling.
    """

    directory: str
    file_name: str
    schema_id: str


def home_of(document: DocumentId) -> ArtifactHome:
    """The declared home of *document*'s artifact: its directory, its file name and its schema id.

    Args:
        document: The consumer's record for the document.

    Returns:
        ``ArtifactHome(directory=<name>, file_name="<name>.json", schema_id=<current spelling>)``.
    """
    return ArtifactHome(
        directory=document.name,
        file_name=f"{document.name}.json",
        schema_id=accepted_schema_ids(document)[0],
    )


@dataclass(frozen=True)
class SchemaRegistry:
    """The documents a consumer declares, indexed by every spelling a reader accepts (emb-train 25).

    Built by :func:`registry`, which is the door that refuses a malformed table; the record itself is frozen data,
    so a reader can keep one registry and answer "which document is this id?" without a module global.

    Attributes:
        documents: The records by name, in declaration order.
        spellings: The records by accepted schema id, in declaration order.
    """

    documents: Mapping[str, DocumentId]
    spellings: Mapping[str, DocumentId]

    @property
    def names(self) -> tuple[str, ...]:
        """The declared documents' names, in declaration order."""
        return tuple(self.documents)

    @property
    def ids(self) -> tuple[str, ...]:
        """Every accepted spelling, in declaration order (each document's ids in their declared order)."""
        return tuple(self.spellings)

    def document_for(self, schema_id: str) -> DocumentId | None:
        """The document that accepts *schema_id*, or ``None`` when no declared document does.

        Args:
            schema_id: A payload's ``schema`` value.

        Returns:
            The consumer's :class:`DocumentId` record, or ``None``.
        """
        return self.spellings.get(schema_id)


def registry(documents: Iterable[DocumentId]) -> SchemaRegistry:
    """The registry of *documents*, refusing two documents that claim one name or one spelling.

    Args:
        documents: The consumer's records, in the order its index should list them.

    Returns:
        The read-only :class:`SchemaRegistry`; the caller's iterable is not kept.

    Raises:
        ValueError: two records share a name, or one accepted spelling names two documents. Either would make a
            reader's answer depend on the order it was handed the records.
    """
    by_name: dict[str, DocumentId] = {}
    by_id: dict[str, DocumentId] = {}
    for document in documents:
        if document.name in by_name:
            raise ValueError(f"document {document.name!r} is declared twice; a name names one document")
        for spelling in accepted_schema_ids(document):
            claimed = by_id.get(spelling)
            if claimed is not None:
                raise ValueError(
                    f"schema id {spelling!r} is claimed by {claimed.name!r} and {document.name!r}; "
                    "a spelling names one document"
                )
            by_id[spelling] = document
        by_name[document.name] = document
    return SchemaRegistry(documents=MappingProxyType(by_name), spellings=MappingProxyType(by_id))
