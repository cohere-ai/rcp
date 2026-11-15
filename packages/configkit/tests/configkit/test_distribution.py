"""The library is a distribution of its own -- ``cohere-configkit``, at ``packages/configkit/``.

This is the fact a parity test cannot see: the identity parity with ``rcp_core._hashing`` passes identically
while the library sits inside the ``cohere-rcp`` wheel, so it proves the rule and not the carve. Here
``importlib.metadata`` resolves the distribution (the pre-carve name ``configkit`` raised
``PackageNotFoundError``, and the module lived under ``packages/rcp/src``), the imported package's files are the
carved tree, and the product's manifest pins the distribution instead of carrying the module.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import configkit

#: The repository root: ``packages/configkit/tests/configkit`` -> four parents up.
REPOSITORY = Path(__file__).resolve().parents[4]

#: ``packages/configkit/src/configkit``: the carved tree the imported package must be.
PACKAGE = REPOSITORY / "packages" / "configkit" / "src" / "configkit"


def test_the_library_is_installed_as_its_own_distribution() -> None:
    """``cohere-configkit`` resolves, at the carved path, and the product depends on it by that name."""
    distribution = importlib.metadata.distribution("cohere-configkit")
    assert distribution.metadata["Name"] == "cohere-configkit"
    assert Path(configkit.__file__).resolve().parent == PACKAGE
    assert "packages/rcp/" not in Path(configkit.__file__).resolve().as_posix()
    product = (REPOSITORY / "packages" / "rcp" / "pyproject.toml").read_text(encoding="utf-8")
    assert "cohere-configkit==" in product
