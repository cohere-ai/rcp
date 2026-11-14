"""One canonical form in two implementations: ``configkit.identity`` and ``rcp_core._hashing`` agree byte for byte.

The library ships inside the ``cohere-rcp`` wheel and may not import ``rcp_core`` (A1), so the rule exists twice
while the extraction is pending; this module is what keeps the two copies one rule. It reads the recorded corpus
``test_identity.py`` reads and asserts the **core's** output against the same bytes and digests, so neither
implementation is trusted on its own: a change to either reddens here.

The module is skipped when ``rcp_core`` is absent, which is the state after the library moves out of the wheel:
the recorded fixture is then the only guard, and the parity becomes the consumer's test (R7b).
"""

from __future__ import annotations

from typing import Any

import pytest
from configkit.faults import ValueErrorFault
from configkit.identity import canonical_bytes, hash_payload
from identity_corpus import VECTORS, payload_of

core = pytest.importorskip("rcp_core._hashing", reason="the other implementation ships in cohere-rcp-core")

#: Payloads with no canonical form at all: both implementations refuse them as a ``TypeError``.
NO_FORM: list[Any] = [
    {"x": {1, 2}},
    {"x": b"no JSON form"},
    {"x": object()},
    {1: "a key that is not a str"},
]

#: Floats JSON has no form for: the core wraps the refusal, the library documents ``ValueError`` for it.
NON_FINITE: list[Any] = [{"x": float("nan")}, {"x": float("inf")}]


def test_the_core_serialises_the_recorded_corpus_to_the_recorded_bytes() -> None:
    """B1: for every vector, the core produces the bytes and digest the fixture records -- the library's too."""
    for vector in VECTORS:
        payload = payload_of(vector)
        assert core.canonical_json(payload) == vector["canonical"].encode("utf-8"), vector["name"]
        assert core.hash_payload(payload) == vector["digest"], vector["name"]


def test_the_two_implementations_agree_on_every_vector() -> None:
    """The same payload, the same bytes: this is the claim ``identity.py``'s docstring makes, asserted."""
    for vector in VECTORS:
        payload = payload_of(vector)
        assert canonical_bytes(payload) == core.canonical_json(payload), vector["name"]
        assert hash_payload(payload) == core.hash_payload(payload), vector["name"]


def test_the_two_implementations_refuse_the_same_payloads() -> None:
    """A refusal in one is a refusal in the other: the accepted domain is one domain, not two."""
    for payload in NO_FORM:
        with pytest.raises(TypeError):
            canonical_bytes(payload)
        with pytest.raises(core.UnhashableValueError):
            core.canonical_json(payload)
    for payload in NON_FINITE:
        with pytest.raises(ValueErrorFault):
            canonical_bytes(payload)
        with pytest.raises(core.UnhashableValueError):
            core.canonical_json(payload)
