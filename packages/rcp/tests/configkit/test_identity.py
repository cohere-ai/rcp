"""One canonical form, one digest, one chain: the bytes are the ones the two consumers already produce.

The canonical form is not invented here. ``rcp_core._hashing`` and ``emb_eval_contracts.identity`` both serialise
a payload with sorted keys, compact separators, UTF-8 and no NaN, and they agree byte for byte; the fixture records
their output for a corpus, so a change to the dump (a dropped ``sort_keys``, a ``default=`` fallback) fails here
rather than in a stored digest. The corpus is JSON-native data: a value with no JSON form is refused, never
stringified into a digest that changes between processes.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from configkit.identity import (
    SHORT_ID_LENGTH,
    Identity,
    canonical_bytes,
    combine_digests,
    freeze,
    hash_payload,
    identity_chain,
    short,
)
from pydantic import BaseModel, ConfigDict

#: The recorded corpus: for each payload, the canonical text and the digest both consumers produced.
FIXTURE = Path(__file__).parent / "fixtures/identity_vectors.json"

_VECTORS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]


def _digest(text: str = "payload") -> str:
    """A full 64-character hex digest, so the truncation tests have a value to cut."""
    return hash_payload({"text": text})


def test_byte_identical_to_the_recorded_fixture() -> None:
    """D4: every recorded payload serialises to the recorded bytes and hashes to the recorded digest."""
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert document["recorded_with"] == [
        "rcp_core._hashing.canonical_json and rcp_core._hashing.hash_payload",
        "emb_eval_contracts.identity.canonical_json and emb_eval_contracts.identity.hash_payload",
    ]
    assert len(_VECTORS) == 10
    for vector in _VECTORS:
        payload = vector["payload"]
        assert canonical_bytes(payload) == vector["canonical"].encode("utf-8"), vector["name"]
        assert hash_payload(payload) == vector["digest"], vector["name"]


def test_the_canonical_form_is_sorted_compact_utf8_and_refuses_nan() -> None:
    """The four dump parameters, each observable: key order, no whitespace, UTF-8 text, no NaN."""
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert canonical_bytes({"name": "café"}) == '{"name":"café"}'.encode()
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("inf")})


def test_a_value_with_no_json_form_is_refused_not_stringified() -> None:
    """No ``default=`` fallback: an object's repr carries a memory address, so it is a refusal, not a digest."""
    with pytest.raises(TypeError):
        hash_payload({"x": object()})
    with pytest.raises(TypeError):
        hash_payload({"x": {1, 2}})


def test_short_bounds_and_the_default_length() -> None:
    """D5: the default length is :data:`SHORT_ID_LENGTH`, and a caller may pass its own."""
    digest = _digest()
    assert SHORT_ID_LENGTH == 16
    assert short(digest, 16) == digest[:16]
    assert len(short(digest, 16)) == 16
    assert len(short(digest, 12)) == 12
    assert short(digest) == short(digest, SHORT_ID_LENGTH)
    assert len(short(digest)) == 16
    # The two consumers' own lengths: emb-train's content id is 16, emb-eval's ``cfg8`` is 8.
    assert len(short(hash_payload({"model": "x"}), 16)) == 16
    assert len(short(digest, 8)) == 8


@pytest.mark.parametrize("bad", ["ab", "", "A" * 64, "0" * 63, "0" * 65])
def test_short_refuses_a_value_that_is_not_a_full_digest(bad: str) -> None:
    """A short value can never be shortened twice: the input is a full lowercase sha256 hex digest."""
    with pytest.raises(ValueError):
        short(bad, 8)


@pytest.mark.parametrize("length", [0, 65])
def test_short_refuses_a_length_outside_the_digest(length: int) -> None:
    """The length is a cut of a 64-character digest, so it is in [1, 64]."""
    with pytest.raises(ValueError):
        short(_digest(), length)


def test_combine_digests_is_order_stable() -> None:
    """One identity from its parts: the same parts in the same order give one digest, a new order a new one."""
    first, second = _digest("first"), _digest("second")
    assert combine_digests(first, second) == combine_digests(first, second)
    assert combine_digests(first, second) != combine_digests(second, first)
    assert len(combine_digests(first, second)) == 64


def test_combine_digests_refuses_a_part_that_is_not_a_full_digest() -> None:
    """The parts are fixed-width, so ``("ab", "cd")`` cannot hash the same bytes as ``("abcd",)``."""
    with pytest.raises(ValueError):
        combine_digests("ab", "cd")


class _Inner(BaseModel):
    """A nested model carrying a list of mappings: the depth ``freeze`` has to reach."""

    model_config = ConfigDict(frozen=True)

    items: list[dict[str, int]]


def test_freeze_is_deep() -> None:
    """rcp-29: a mapping inside a list inside a model is frozen too, and a nested tuple is a tuple."""
    frozen = freeze({"rows": [{"a": 1}, {"b": 2}], "tags": ("x", "y")})
    assert frozen["tags"] == ("x", "y")
    assert isinstance(frozen["rows"], tuple)
    with pytest.raises(TypeError):
        frozen["rows"][0]["a"] = 9  # type: ignore[index]


def test_freeze_reaches_inside_a_model() -> None:
    """The identity artifact's own shape: the model becomes read-only data, one level down included."""
    frozen = freeze(_Inner(items=[{"a": 1}]))
    assert frozen["items"][0]["a"] == 1
    assert isinstance(frozen["items"], tuple)
    with pytest.raises(TypeError):
        frozen["items"][0]["a"] = 2  # type: ignore[index]


def test_freeze_leaves_a_scalar_alone() -> None:
    """A value with no container is returned as it stands."""
    assert freeze(7) == 7
    assert freeze("text") == "text"
    assert freeze(None) is None


def test_identity_chain_returns_the_four_parts() -> None:
    """et-13: the config hash, the data fingerprint, the model identity and the run id, in one call."""
    config = {"metric": "ndcg@10", "runtime": {"seed": 7}}
    chain = identity_chain(
        config=config,
        data=_digest("data"),
        model=_digest("model"),
        run="embed__20260101T000000Z__suite__0123abcd",
    )
    assert isinstance(chain, Identity)
    assert chain.config == hash_payload(config)
    assert chain.data == _digest("data")
    assert chain.model == _digest("model")
    assert chain.run == "embed__20260101T000000Z__suite__0123abcd"
    # The config is hashed here, so a changed CONTENT value is a different chain and an unchanged one is the same.
    data, model = _digest("data"), _digest("model")
    changed = identity_chain(config={**config, "metric": "ndcg@5"}, data=data, model=model, run="r")
    same = identity_chain(config=config, data=data, model=model, run="r")
    assert changed.config != chain.config
    assert same.config == chain.config


def test_identity_chain_is_frozen_and_refuses_a_bad_part() -> None:
    """A chain is a record, and a part that is not a full digest or a non-empty run id is refused."""
    chain = identity_chain(config={}, data=_digest("data"), model=_digest("model"), run="r")
    with pytest.raises(FrozenInstanceError):
        chain.config = "0" * 64  # type: ignore[misc]
    with pytest.raises(ValueError):
        identity_chain(config={}, data="short", model=_digest("model"), run="r")
    with pytest.raises(ValueError):
        identity_chain(config={}, data=_digest("data"), model="short", run="r")
    with pytest.raises(ValueError):
        identity_chain(config={}, data=_digest("data"), model=_digest("model"), run="")
