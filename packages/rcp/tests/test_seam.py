"""The seam gate: no underscore name crosses a package boundary in the product (P5, C5).

:func:`configkit.audit.scan` reads the product's source (it parses, never imports) and reports eight checks. This
file asserts the two the refactor owns: **S5** (an import of a ``_``-prefixed name from outside the scanned tree
is a private seam crossing a package boundary) and **S10** (a ``**options: Any`` signature, which is a reader's or
a writer's options left undeclared, rcp-24).

Two allowances, and both are data rather than prose:

* :data:`THIRD_PARTY_SEAMS` names the private *upstream* names the product must reach through. They are not this
  package's seam to publish -- the package that defines them has no public alternative -- so each is listed with
  the file that needs it and why. A **new** private third-party name fails here, which is the point.
* S7 (a module whose ``__all__`` omits a public name), S9 (a file read outside the library's three readers) and
  S3 (a shipped data pin) are out of this card's scope: S7 and S9 are the product's own conventions against the
  library's defaults, and S3's one finding is a hint string. They are recorded as T4-FOLLOW-UP (S7-ALL) and in
  ``.pi-docs/configkit/T4-LOG.md``; this file does not silence them, it just does not claim them.
"""

from __future__ import annotations

from configkit.audit import scan

#: The product's source tree: the only tree these checks read.
PRODUCT = "packages/rcp/src/rcp"

#: The private third-party names the product imports, one line each: what it is, where it is used, and why no
#: public name will do. A private name that is not here fails the gate.
THIRD_PARTY_SEAMS: dict[str, str] = {
    "huggingface_hub._CACHED_NO_EXIST": (
        "data/io/hub.py: the Hub's '.no_exist' marker sentinel. huggingface_hub exposes it only under this name, "
        "and it is the only way to tell 'absent upstream' from 'not cached' offline"
    ),
    "mteb.get_tasks._TASKS_REGISTRY": (
        "eval/mteb/__init__.py: mteb's in-tree task registry, which has no public accessor; a mteb:<TaskName> "
        "source resolves its task through it"
    ),
}


def _seams() -> list[str]:
    """The S5 findings under :data:`PRODUCT`, in file and line order."""
    return [finding for finding in scan(PRODUCT, seam_prefix="_") if finding.startswith("S5 ")]


def _allowed(finding: str) -> bool:
    """Whether *finding* names one of :data:`THIRD_PARTY_SEAMS`."""
    return any(f"an underscore seam: {name} crosses" in finding for name in THIRD_PARTY_SEAMS)


def test_no_underscore_seam_crosses_a_package_boundary() -> None:
    """S5: the product imports no private name across a package boundary, apart from the recorded upstream ones.

    A finding names ``path:line`` and the name, so a new seam is one line to read.
    """
    unrecorded = [finding for finding in _seams() if not _allowed(finding)]
    assert unrecorded == [], "\n".join(unrecorded)


def test_the_third_party_allowance_is_still_needed() -> None:
    """Each recorded upstream seam is still imported: an allowance for a name nobody imports any more is stale."""
    imported = " ".join(_seams())
    stale = [name for name in THIRD_PARTY_SEAMS if f"an underscore seam: {name} crosses" not in imported]
    assert stale == [], f"drop these from THIRD_PARTY_SEAMS: {stale}"


def test_no_options_mapping_is_left_in_the_data_surface() -> None:
    """S10 over the reader and writer surface: ``load_dataset``'s options are a declared model, not a
    ``**options: Any`` mapping a typo disappears into (rcp-24)."""
    found = [
        finding
        for finding in scan(PRODUCT, api_globs=("data/**/*.py",))
        if finding.startswith("S10 ") and "a **options: Any signature" in finding
    ]
    assert found == [], "\n".join(found)
