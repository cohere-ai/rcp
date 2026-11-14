"""One canonical form, one digest, one chain: the bytes three repositories already hash.

A digest is a claim -- *these two things are interchangeable* -- and it is only a claim while the serialiser is
the same one everywhere. The canonical form here is not invented: ``rcp_core._hashing`` and
``emb_eval_contracts.identity`` both produce it, and they agree byte for byte:

    json.dumps(canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        .encode("utf-8")

:func:`canonical_bytes` is that call, :func:`hash_payload` is SHA-256 over it, and ``tests/configkit/
fixtures/identity_vectors.json`` records every implementation's output for a corpus, so a dropped ``sort_keys``, an
added ``default=``, an unnormalised ``-0.0`` or a refused ``Path`` fails a test rather than a stored digest.

What makes it canonical, rule for rule ``rcp_core._hashing.canonical``'s (the library may not import the core, so
the rule is mirrored here and ``tests/configkit/test_identity_parity.py`` compares the two):

* keys sorted, no insertion-order dependence, compact separators;
* one declared encoder -- an ``Enum`` member is its declared value, a ``Path`` its string, a pydantic model its
  ``model_dump()``, an aware ``datetime`` and a ``date`` their ISO text, a tuple a list -- and everything else is
  refused. ``json.dumps(..., default=str)`` is how ``<object at 0x7f3c...>`` lands in a key that then changes
  between processes: a refusal is a bug report, a fallback ``str()`` a silent non-deterministic identity;
* ``NaN``/``Infinity`` refused (not JSON, and ``NaN != NaN`` makes an identity that never matches itself),
  ``-0.0`` normalised to ``0.0`` (``-0.0 == 0.0``, so they must not hash apart);
* ``set``/``bytes`` refused, and a mapping key that is not a ``str`` too -- a set has no canonical order, bytes
  have no JSON form, and an ``int`` key would be stringified into a key nobody wrote.

``int`` and ``float`` are deliberately not unified: a count of ``1`` and a fraction of ``1.0`` are different
settings, so they must not share a key.

The module hashes with :mod:`hashlib` and imports no ``rcp_core``: the library is the successor, not a client.
``rcp_core`` and ``emb_eval_contracts.identity`` re-export these functions after the extraction and keep no copy;
until then the recorded fixture and the parity test are the guard.

:data:`SHORT_ID_LENGTH` is rcp's length today (16), and it is the *default*, not a policy: emb-eval passes 12 and
8 to the same :func:`short`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any, Final

from pydantic import BaseModel

from configkit.faults import ValueErrorFault
from configkit.spec import _frozen

__all__ = [
    "SHORT_ID_LENGTH",
    "Identity",
    "canonical_bytes",
    "hash_payload",
    "short",
    "combine_digests",
    "identity_chain",
    "freeze",
]

#: The short length rcp displays today, in characters; a caller passes its own to :func:`short`.
SHORT_ID_LENGTH: Final[int] = 16

#: The characters a full sha256 hex digest is made of; a truncated or upper-case value is refused.
_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")


def _no_form(where: str, value: Any, why: str) -> TypeError:
    """The refusal for a value with no declared canonical form: never a ``str()`` fallback."""
    return TypeError(
        f"cannot hash {where}: {why} (type {type(value).__name__}). "
        "Convert it in the caller so the identity says what it means."
    )


def _canonical(value: Any, *, where: str = "payload") -> Any:
    """*value* as JSON-native data under the one declared encoder, or a refusal naming *where* it sits."""
    # Enum first: a ``StrEnum``/``IntEnum`` member is also a str/int, and the canonical form of an enum is its
    # declared value, not the member.
    if isinstance(value, Enum):
        return _canonical(value.value, where=f"{where}(enum)")
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot hash {where}: non-finite floats have no JSON form (type float)")
        return 0.0 if value == 0.0 else value
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(), where=where)
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise _no_form(where, value, "a naive datetime names no instant")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        raise _no_form(where, value, "a set has no canonical order; pass a sorted list")
    if isinstance(value, bytes | bytearray | memoryview):
        raise _no_form(where, value, "bytes have no canonical JSON form; pass a digest or a str")
    if isinstance(value, Mapping):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _no_form(f"{where}[{key!r}]", key, "mapping keys must be str")
            encoded[key] = _canonical(item, where=f"{where}.{key}")
        return encoded
    if isinstance(value, Sequence):
        return [_canonical(item, where=f"{where}[{index}]") for index, item in enumerate(value)]
    raise _no_form(where, value, "no declared canonical form")


def canonical_bytes(payload: Any) -> bytes:
    """The exact bytes every digest in this library is taken over: one encoder, sorted keys, no whitespace, UTF-8.

    Args:
        payload: The data to encode: JSON-native data (``str``, ``int``, ``float``, ``bool``, ``None``, a list or
            tuple, a mapping with ``str`` keys), or a value with a declared encoding (:func:`_canonical`): an
            ``Enum`` member, a ``Path``, a pydantic model, an aware ``datetime`` or a ``date``.

    Returns:
        The canonical UTF-8 bytes of *payload*: ``-0.0`` written as ``0.0``, a ``Path`` as its string, an ``Enum``
        member as its declared value.

    Raises:
        TypeError: a value has no declared canonical form (an arbitrary object, a ``set``, ``bytes``, a mapping
            key that is not a ``str``, a naive ``datetime``). There is no ``default=`` fallback: ``str(value)``
            would put a repr in a digest.
        ValueError: a float is ``NaN`` or infinite, which JSON has no form for and ``NaN != NaN`` would make an
            identity that never matches itself.
    """
    return json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def hash_payload(payload: Any) -> str:
    """The full SHA-256 digest of *payload*'s canonical bytes.

    Args:
        payload: JSON-native data; see :func:`canonical_bytes`.

    Returns:
        The digest as 64 lowercase hex characters.

    Raises:
        TypeError: a value has no JSON form.
        ValueError: a float is ``NaN`` or infinite.
    """
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _full_digest(digest: str, *, caller: str) -> str:
    """*digest* if it is a full sha256 hex digest; a refusal naming *caller* otherwise."""
    if len(digest) != 64 or any(character not in _HEX for character in digest):
        raise ValueErrorFault(f"{caller} takes a full sha256 hex digest (64 lowercase hex characters), got {digest!r}")
    return digest


def short(digest: str, length: int = SHORT_ID_LENGTH) -> str:
    """A truncated digest, for a human-facing display or a bounded name component.

    Args:
        digest: A full sha256 hex digest: 64 lowercase hex characters. A short value is refused, so a short can
            never be shortened twice and quietly claim to be a shorter identity than it is.
        length: The cut, in characters; the default is :data:`SHORT_ID_LENGTH` (16).

    Returns:
        The first *length* characters of *digest*.

    Raises:
        ValueErrorFault: *digest* is not a full lowercase hex digest, or *length* is outside ``[1, 64]``.
    """
    _full_digest(digest, caller="short()")
    if not 1 <= length <= 64:
        raise ValueErrorFault(f"short() length must be in [1, 64] characters, got {length}")
    return digest[:length]


def combine_digests(*digests: str) -> str:
    """One digest from several, in order: a run's identity from its parts.

    Each part is a full 32-byte digest, so the concatenation cannot run two parts together (``"ab"`` and
    ``"cd"`` would otherwise hash the same bytes as ``"abcd"``); the same parts in the same order always give the
    same digest, and a different order gives a different one.

    Args:
        *digests: Full sha256 hex digests (64 lowercase hex characters each).

    Returns:
        The full SHA-256 digest of the parts' bytes, in the order given: 64 lowercase hex characters.

    Raises:
        ValueErrorFault: a part is not a full lowercase hex digest.
    """
    combined = hashlib.sha256()
    for digest in digests:
        combined.update(bytes.fromhex(_full_digest(digest, caller="combine_digests()")))
    return combined.hexdigest()


@dataclass(frozen=True)
class Identity:
    """One run's identity: the four parts a key is built from (emb-train 13, emb-eval S16/X7).

    Attributes:
        config: The full sha256 hex digest of the config payload (64 lowercase hex characters).
        data: The data fingerprint: a full sha256 hex digest.
        model: The model identity: a full sha256 hex digest.
        run: The run id, the caller's tags composed into one name (a non-empty string; not a digest).
    """

    config: str
    data: str
    model: str
    run: str


def identity_chain(*, config: Mapping[str, Any], data: str, model: str, run: str) -> Identity:
    """The four parts of one run's identity, in one call.

    The chain is where a run's key is *derived* rather than defaulted: the config mapping is hashed here, so a
    changed CONTENT value is a different chain, and the three values the caller already holds are validated
    rather than trusted.

    Args:
        config: The CONTENT payload to hash (``roles.identity_of``'s mapping, or any JSON-native mapping).
        data: The data fingerprint: a full sha256 hex digest (64 lowercase hex characters).
        model: The model identity: a full sha256 hex digest.
        run: The run id: a non-empty string, the caller's tags composed into one name.

    Returns:
        The :class:`Identity` record carrying all four parts.

    Raises:
        ValueErrorFault: *data* or *model* is not a full lowercase hex digest, or *run* is empty.
        TypeError: the config payload has a value with no JSON form.
    """
    if not isinstance(run, str) or not run.strip():
        raise ValueErrorFault(f"identity_chain() needs a non-empty run id, got {run!r}")
    return Identity(
        config=hash_payload(config),
        data=_full_digest(data, caller="identity_chain(data=)"),
        model=_full_digest(model, caller="identity_chain(model=)"),
        run=run,
    )


def freeze(value: Any) -> Any:
    """An identity artifact with every container frozen, at every depth (rcp-29).

    A model is already frozen as a record, but a list inside it is not: ``model.list.append(...)`` changes the
    artifact after it was recorded. The one walk lives in :mod:`configkit.spec` (``_frozen``), so this name and
    ``frozen_mapping``/``frozen_sequence`` are one rule with three doors --
    a model becomes a read-only mapping of its fields (extras included), a mapping a
    :class:`types.MappingProxyType`, and a list or tuple a tuple.

    Args:
        value: The artifact to freeze: a model, a mapping, a sequence, or a scalar.

    Returns:
        The frozen form: read-only mappings and tuples at every depth, a scalar unchanged.
    """
    return _frozen(value)
