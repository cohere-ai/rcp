"""The words and the numbers: the vocabularies the library owns, and the consumer data it must not own.

Three rules decide what is here. A **vocabulary** the library ships is one the consumers must agree on for
identity to mean the same thing (a capability, a role, a budget key); a vocabulary a **consumer** declares is
absent -- ``SERVE_KEYS`` and ``DECISION_SCALE`` are consumer data, and a library constant would make two consumers
agree by accident, which is the defect the library exists to remove (F5, ee-S24, et-7). A **number** the library
ships is one the consumers treat as library-defined (the cut, in tokens of the shape's tokenizer; rcp-47), and its
test pins it, because "numerics do not move by accident".

The open task vocabulary is the third shape (et-7: "It must not assume a closed task set"): :class:`TaskKind` is a
``str`` with no member set, so a consumer's own task word is a task kind and its own :class:`KeyVocabulary` decides
which words a mapping accepts.
"""

from __future__ import annotations

import configkit.vocab as vocab
import pytest
from configkit.faults import Fault
from configkit.vocab import (
    BudgetKey,
    Capability,
    DatasetRef,
    DigestPin,
    GainsKey,
    KeyVocabulary,
    MetricKey,
    TaskKind,
    WorldSize,
    cut_for,
)
from pydantic import BaseModel, ValidationError

from rcp.calibration._projection import QUERY_ID_SEP
from rcp.data.text_policy import DEFAULT_MAX_TOKENS


class _Sized(BaseModel):
    """A model whose field is the library's typed world size, so the validator is exercised where it is used."""

    world_size: WorldSize


class _Pinned(BaseModel):
    """A model whose field is the library's digest pin."""

    digest: DigestPin


# ---------------------------------------------------------------------------------------------------------------
# Capability, TaskKind: one closed vocabulary and one open one
# ---------------------------------------------------------------------------------------------------------------


def test_capability_is_the_three_strategies() -> None:
    """The capability vocabulary is the three strategies a model adapter declares support for (emb-train 6)."""
    assert [member.value for member in Capability] == ["grad_cache", "ep", "multimodal"]
    assert Capability("grad_cache") is Capability.GRAD_CACHE


def test_task_kind_is_open() -> None:
    """``TaskKind`` is a ``str`` with no member set: a consumer's own task word is a task kind (et-7)."""
    assert TaskKind("retrieval") == "retrieval"
    assert isinstance(TaskKind("retrieval"), str)
    assert TaskKind("a-task-the-library-never-heard-of") == "a-task-the-library-never-heard-of"
    assert not hasattr(TaskKind, "__members__"), "a closed member set is what requirement 7 forbids"


def test_a_consumer_extends_the_task_vocabulary() -> None:
    """A consumer subclasses ``TaskKind`` and registers its words through its own key vocabulary."""

    class TrainTask(TaskKind):
        """A consumer's task kind: the same value semantics, the consumer's own name."""

    vocabulary = KeyVocabulary(name="task.kind", keys=("retrieval", "rerank"))
    vocabulary.check({TrainTask("retrieval"): 1}, where="task")
    with pytest.raises(Fault):
        vocabulary.check({TrainTask("generation"): 1}, where="task")


def test_a_blank_task_kind_is_refused() -> None:
    """A blank word names no task, so it is refused at construction rather than at a comparison."""
    with pytest.raises(Fault):
        TaskKind("  ")


# ---------------------------------------------------------------------------------------------------------------
# MetricKey: one grammar for a `metric@k` name (emb-eval S17/M4)
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "canonical", "name", "depth"),
    [
        ("ndcg", "ndcg", "ndcg", None),
        ("NDCG", "ndcg", "ndcg", None),
        ("ndcg@10", "ndcg@10", "ndcg", 10),
        ("NDCG@010", "ndcg@10", "ndcg", 10),
        (
            "compressed_1536d_original_fp32_ndcg@10",
            "compressed_1536d_original_fp32_ndcg@10",
            "compressed_1536d_original_fp32_ndcg",
            10,
        ),
        ("ndcg@0", "ndcg", "ndcg", None),
    ],
)
def test_metric_key_canonicalises(written: str, canonical: str, name: str, depth: int | None) -> None:
    """One name, one spelling: the name is lowercased and the depth is an int (``@0`` is no cutoff)."""
    key = MetricKey(written)
    assert key == canonical
    assert key.name == name
    assert key.k == depth


def test_metric_key_parse_is_the_door() -> None:
    """``parse`` returns the canonical key, so a caller reads one spelling and not the text it was given."""
    assert MetricKey.parse("Recall@100") == "recall@100"
    assert isinstance(MetricKey.parse("recall@100"), MetricKey)


@pytest.mark.parametrize("written", ["", "   ", "@10", "ndcg@", "ndcg@x", "ndcg@-1", "ndcg@1@2"])
def test_a_malformed_metric_key_is_refused(written: str) -> None:
    """A name that is not ``name`` or ``name@k`` is refused, naming the grammar, rather than read as a word."""
    with pytest.raises(Fault) as raised:
        MetricKey(written)
    assert "name@k" in raised.value.message


# ---------------------------------------------------------------------------------------------------------------
# WorldSize, DigestPin: the two typed scalars (et-5, et-9)
# ---------------------------------------------------------------------------------------------------------------


def test_world_size_is_a_positive_int() -> None:
    """A world size is the number of ranks, so it is at least one."""
    assert WorldSize(4) == 4
    assert isinstance(WorldSize(4), int)
    with pytest.raises(Fault):
        WorldSize(0)
    with pytest.raises(Fault):
        WorldSize(-2)


def test_world_size_is_the_type_of_a_model_field() -> None:
    """The validator is the type's, so a model that declares it cannot hold an impossible world size."""
    assert _Sized(world_size=8).world_size == 8
    with pytest.raises((ValidationError, Fault)):
        _Sized(world_size=0)


def test_digest_pin_is_64_lowercase_hex() -> None:
    """A digest pin is a full SHA-256 digest: 64 lowercase hex characters, and nothing else (et-9)."""
    pinned = "a" * 64
    assert DigestPin(pinned) == pinned
    for written in ("a" * 63, "a" * 65, "A" * 64, "g" * 64, "sha256:" + "a" * 64):
        with pytest.raises(Fault):
            DigestPin(written)


def test_digest_pin_is_the_type_of_a_model_field() -> None:
    """A model field typed as the pin refuses a short digest where it is read."""
    assert _Pinned(digest="0" * 64).digest == "0" * 64
    with pytest.raises((ValidationError, Fault)):
        _Pinned(digest="0" * 8)


# ---------------------------------------------------------------------------------------------------------------
# DatasetRef: one dataset-reference value object (rcp-25)
# ---------------------------------------------------------------------------------------------------------------


def test_dataset_ref_parses_a_hub_uri() -> None:
    """``hf://<owner>/<repo>[/<subset>][@<revision>]`` is the one Hub spelling, parsed once."""
    ref = DatasetRef.parse("hf://owner/repo/subset@abc123")
    assert (ref.scheme, ref.repo, ref.subset, ref.revision) == ("hf", "owner/repo", "subset", "abc123")
    assert ref.as_uri() == "hf://owner/repo/subset@abc123"
    bare = DatasetRef.parse("hf://owner/repo")
    assert (bare.subset, bare.revision) == (None, None)
    assert bare.as_uri() == "hf://owner/repo"


def test_dataset_ref_parses_a_named_scheme() -> None:
    """A reader scheme is ``<scheme>:<name>``, and it takes no subset and no revision."""
    ref = DatasetRef.parse("beir:scifact")
    assert (ref.scheme, ref.repo, ref.subset, ref.revision) == ("beir", "scifact", None, None)
    assert ref.as_uri() == "beir:scifact"


@pytest.mark.parametrize(("written", "scheme"), [("HF://owner/repo", "hf"), ("BEIR:scifact", "beir")])
def test_the_scheme_is_lowercased_before_it_is_matched(written: str, scheme: str) -> None:
    """L9: the record documents a lower-case scheme and never lowercased it.

    ``HF://owner/repo`` was refused as a non-Hub reference ("only an 'hf://' reference names a repository
    path") and ``BEIR:scifact`` kept its upper case, so two spellings of one dataset were two records.
    """
    ref = DatasetRef.parse(written)
    assert ref.scheme == scheme
    assert ref.as_uri() == written.lower()
    with pytest.raises(Fault):
        DatasetRef(scheme="beir", repo="scifact", subset="s").as_uri()


@pytest.mark.parametrize("written", ["owner/repo", "", "hf:/owner/repo", "hf://owner", "beir:"])
def test_a_malformed_dataset_reference_is_refused(written: str) -> None:
    """A URI with no scheme, or a Hub URI with one path part, is refused rather than guessed at."""
    with pytest.raises(Fault):
        DatasetRef.parse(written)


def test_dataset_ref_is_frozen_and_identity_shaped() -> None:
    """The record is frozen and declares its roles, so it can be a CONTENT field of a consumer's model."""
    ref = DatasetRef.parse("hf://owner/repo")
    with pytest.raises(ValidationError):
        ref.repo = "other"  # type: ignore[misc]
    assert set(DatasetRef.IDENTITY_ROLES) == set(DatasetRef.model_fields)


# ---------------------------------------------------------------------------------------------------------------
# KeyVocabulary: one declared key set per extensible mapping, with a refusal (emb-train 4)
# ---------------------------------------------------------------------------------------------------------------


def test_key_vocabulary_accepts_a_declared_mapping() -> None:
    """Every key declared, no key undeclared: the mapping passes."""
    vocabulary = KeyVocabulary(name="model.config", keys=("pooling", "normalize"))
    vocabulary.check({"pooling": "mean", "normalize": True}, where="model.config")


def test_key_vocabulary_refuses_an_unknown_key_naming_where() -> None:
    """An unknown key is refused, naming the mapping and the known keys, so the document is fixable."""
    vocabulary = KeyVocabulary(name="model.config", keys=("pooling", "normalize"))
    with pytest.raises(Fault) as raised:
        vocabulary.check({"pooling": "mean", "poolin": "mean"}, where="model.config")
    assert "model.config" in raised.value.message
    assert raised.value.details["unknown"] == ["poolin"]
    assert raised.value.details["known"] == ["normalize", "pooling"]


def test_key_vocabulary_refuses_a_malformed_declaration() -> None:
    """A vocabulary with no key, or the same key twice, is a declaration nobody can check."""
    with pytest.raises(Fault):
        KeyVocabulary(name="model.config", keys=())
    with pytest.raises(Fault):
        KeyVocabulary(name="model.config", keys=("a", "a"))
    with pytest.raises(Fault):
        KeyVocabulary(name=" ", keys=("a",))


def test_a_key_vocabulary_declared_as_one_string_is_refused() -> None:
    """H11: ``tuple("abc")`` is ``('a', 'b', 'c')``, so a string would enforce a vocabulary nobody declared."""
    with pytest.raises(Fault) as raised:
        KeyVocabulary(name="model.config", keys="abc")  # type: ignore[arg-type]
    assert "keys" in raised.value.message
    assert raised.value.details["keys"] == "abc"


# ---------------------------------------------------------------------------------------------------------------
# BudgetKey, GainsKey, cut_for: the numeric fits the library owns (rcp-18, rcp-47)
# ---------------------------------------------------------------------------------------------------------------


def test_budget_key_is_a_shape_and_a_role() -> None:
    """A budget is declared per request shape and per role, and the pair is the key of the table (rcp-18)."""
    key = BudgetKey(shape="document", role="judge")
    assert (key.shape, key.role) == ("document", "judge")
    assert {key: 4096}[BudgetKey(shape="document", role="judge")] == 4096
    for shape, role in (("", "judge"), ("document", " ")):
        with pytest.raises(Fault):
            BudgetKey(shape=shape, role=role)


def test_gains_key_is_the_namespaced_query_id() -> None:
    """The gains of one dataset scored alone need no name; a suite's parts share query ids, so they are
    namespaced ``<dataset>||<query_id>`` (rcp-47, the product's ``QUERY_ID_SEP``).

    M6: the pin reads the product's constant, so a change on either side reddens this test.
    """
    assert GainsKey.of("ds", "q1") == f"ds{QUERY_ID_SEP}q1"
    named = GainsKey(f"ds{QUERY_ID_SEP}q1")
    assert (named.dataset, named.query) == ("ds", "q1")
    bare = GainsKey("q1")
    assert (bare.dataset, bare.query) == (None, "q1")


@pytest.mark.parametrize(
    "written", ["", "  ", f"{QUERY_ID_SEP}q1", f"ds{QUERY_ID_SEP}", f"a{QUERY_ID_SEP}b{QUERY_ID_SEP}c"]
)
def test_a_malformed_gains_key_is_refused(written: str) -> None:
    """A key that names no query, or two separators, is refused rather than split by guess."""
    with pytest.raises(Fault):
        GainsKey(written)


def test_cut_for_pins_the_shape_cuts() -> None:
    """The cut is in tokens of the shape's tokenizer, and it is the product's cap for every shape it reads
    (``rcp.data.text_policy.DEFAULT_MAX_TOKENS``, 2**15). The number is pinned here: it does not move by accident."""
    assert cut_for("query") == DEFAULT_MAX_TOKENS
    assert cut_for("document") == DEFAULT_MAX_TOKENS
    assert cut_for("pair") == DEFAULT_MAX_TOKENS
    assert isinstance(cut_for("document"), int)


def test_cut_for_refuses_a_shape_it_does_not_declare() -> None:
    """A shape nobody declared a cut for is refused, so a new shape is a declared decision, not a default."""
    with pytest.raises(Fault) as raised:
        cut_for("passage")
    assert "passage" in raised.value.message
    assert raised.value.details["shapes"] == ["document", "pair", "query"]


# ---------------------------------------------------------------------------------------------------------------
# the surface: no consumer constant, and one published name per member
# ---------------------------------------------------------------------------------------------------------------


def test_vocab_ships_no_consumer_constant() -> None:
    """F5: the serve vocabulary and the decision scale are consumer data, and the library ships neither."""
    assert not hasattr(vocab, "SERVE_KEYS")
    assert not hasattr(vocab, "DECISION_SCALE")


def test_vocab_publishes_exactly_its_names() -> None:
    """``__all__`` is the module's surface, and every name in it is bound."""
    assert vocab.__all__ == [
        "Capability",
        "TaskKind",
        "MetricKey",
        "WorldSize",
        "DigestPin",
        "DatasetRef",
        "KeyVocabulary",
        "BudgetKey",
        "GainsKey",
        "cut_for",
    ]
    assert all(hasattr(vocab, name) for name in vocab.__all__)
