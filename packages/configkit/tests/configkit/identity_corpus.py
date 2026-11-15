"""The recorded identity corpus, read by both implementations' tests.

``fixtures/identity_vectors.json`` is the one record of the canonical bytes and the digest: ``test_identity.py``
checks ``configkit.identity`` against it and ``test_identity_parity.py`` checks ``rcp_core._hashing`` against the
same file, so the two serialisers are compared rather than each trusted on its own.

A ``Path`` and an ``Enum`` member have no JSON form, so a vector whose payload holds one records the string and
the ``payload_kind`` that says how to rebuild it; :func:`payload_of` is that one rebuild.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

#: The recorded corpus: for each payload, the canonical text and the digest every implementation produced.
FIXTURE = Path(__file__).parent / "fixtures/identity_vectors.json"

#: The vectors the file holds: the ten JSON-native ones plus ``-0.0``, a ``Path`` and an ``Enum`` member.
VECTORS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]


class Tie(StrEnum):
    """The ``enum`` vector's member: a ``str`` Enum, the shape ``canonical`` must encode as its value."""

    GROUP = "group_mean"


def payload_of(vector: dict[str, Any]) -> Any:
    """The Python payload *vector* records: JSON-native data, or the value its ``payload_kind`` names."""
    kind = vector.get("payload_kind")
    if kind == "path":
        return {name: Path(value) for name, value in vector["payload"].items()}
    if kind == "enum":
        return {name: Tie(value) for name, value in vector["payload"].items()}
    return vector["payload"]
