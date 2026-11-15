"""The surface decision (H10): ``configkit`` is not a public module, and this test is where that is recorded.

The library is its own distribution (``cohere-configkit``), so its names are additive to the product's surface
(A3) and appear in no ``tests/contract/snapshots/`` file. The exclusion is a product decision, recorded in
``CHANGELOG.md``'s Unreleased -> Internal entry; a *test* is what makes a change to it red, because the snapshot
is the definition of public and nothing else reads this list for the library.

Two failures this guards: adding ``configkit`` to ``PUBLIC_MODULES`` without regenerating the snapshots (the
first assertion), and a snapshot that starts recording a library name by accident (the second). The inclusion
decision and the regeneration are T4's: no R card owns ``packages/rcp/src/rcp/support/api_docs.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: The repository root: ``packages/configkit/tests/configkit`` -> four parents up.
REPOSITORY = Path(__file__).resolve().parents[4]

#: The snapshot directory the contract suite pins the public surface in.
SNAPSHOTS = REPOSITORY / "tests/contract/snapshots"


def test_configkit_is_in_no_public_modules_entry() -> None:
    """H10: the library is excluded from the frozen surface, and the exclusion is this test's subject."""
    api_docs = pytest.importorskip("rcp.support.api_docs")
    assert "configkit" not in api_docs.PUBLIC_MODULES
    assert [module for module in api_docs.PUBLIC_MODULES if module.startswith("configkit")] == []


def test_no_snapshot_records_a_configkit_name() -> None:
    """The exclusion's other half: no snapshot names the library, so none can move with it."""
    assert SNAPSHOTS.is_dir(), SNAPSHOTS
    named = sorted(path.name for path in SNAPSHOTS.glob("*.json") if "configkit" in path.read_text(encoding="utf-8"))
    assert named == []


def test_the_snapshot_directory_is_the_one_the_contract_suite_reads() -> None:
    """A control: the directory exists and holds the pinned surface, so the exclusion above is not vacuous."""
    python_api = SNAPSHOTS / "python_api.json"
    assert python_api.is_file(), python_api
    snapshot = json.loads(python_api.read_text(encoding="utf-8"))
    assert "rcp.errors" in snapshot
