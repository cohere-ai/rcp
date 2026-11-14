"""The drift guard: every product ``schema_name: Literal[...]`` names exactly the ids its document accepts.

The annotation is load-bearing -- pydantic exports the ``Literal`` as the ``enum`` of a schema's ``schema``
property (``schemas/calibration-identity.v1.json``) -- so it is not replaced by a ``str``. What is checked instead
is that the two spellings cannot drift from the source the product reads them with: for every declaration, the
``Literal``'s members equal ``rcp_core.schemas.accepted_schema_ids(<the document's name>)`` and equal what a
:class:`configkit.schemas.DocumentId` record for that document accepts. A changed spelling on either side fails
here, naming the file, the line, the document and the two spellings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest
from configkit.schemas import DocumentId
from rcp_core.schemas import accepted_schema_ids as core_accepted_schema_ids

from rcp.schemas import SchemaTagged, accepted_schema_ids

#: ``packages/rcp/src/rcp``: the product's source tree, the only tree this guard reads.
PRODUCT = Path(__file__).resolve().parents[2] / "src/rcp"

#: The two prefixes a payload's ``schema`` field may carry: the current one and the pre-rename one.
CURRENT_PREFIX = "rcp."
LEGACY_PREFIX = "rcp-ndcg."


@dataclass(frozen=True)
class _Site:
    """One ``schema_name: Literal[...]`` declaration: where it is, the document it names, and its spellings."""

    path: str
    line: int
    name: str
    members: tuple[str, ...]

    def record(self) -> DocumentId:
        """The document record this declaration is: its name, its spellings and the removable one."""
        removable = [member for member in self.members if member.startswith(LEGACY_PREFIX)]
        assert len(removable) == 1, f"{self.path}:{self.line}: expected exactly one {LEGACY_PREFIX} spelling"
        return DocumentId(name=self.name, accepted_ids=self.members, removable=removable[0])


def _sites() -> list[_Site]:
    """Every ``schema_name: Literal[...]`` annotation in the product, in file and line order."""
    sites: list[_Site] = []
    for path in sorted(PRODUCT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if node.target.id != "schema_name" or not isinstance(node.annotation, ast.Subscript):
                continue
            if not isinstance(node.annotation.value, ast.Name) or node.annotation.value.id != "Literal":
                continue
            slice_ = node.annotation.slice
            elements = slice_.elts if isinstance(slice_, ast.Tuple) else [slice_]
            members = tuple(str(element.value) for element in elements if isinstance(element, ast.Constant))
            current = [member for member in members if member.startswith(CURRENT_PREFIX)]
            assert len(current) == 1, f"{path}:{node.lineno}: expected exactly one {CURRENT_PREFIX} spelling"
            name = current[0][len(CURRENT_PREFIX) : -len(".v1")]
            sites.append(_Site(str(path.relative_to(PRODUCT.parents[3])), node.lineno, name, members))
    return sites


_SITES = _sites()


def test_the_schema_literal_set_is_pinned() -> None:
    """Fourteen declarations in twelve files (measured): a new one is a new document, and it lands in this guard."""
    assert len(_SITES) == 14
    assert len({site.path for site in _SITES}) == 12


@pytest.mark.parametrize("site", _SITES, ids=[f"{site.name}@{site.path}:{site.line}" for site in _SITES])
def test_schema_literal_names_the_accepted_ids(site: _Site) -> None:
    """The ``Literal``'s members are the ids the record accepts and the ids the product's source computes."""
    record = site.record()
    accepted = accepted_schema_ids(record)
    expected = core_accepted_schema_ids(site.name)
    assert accepted == site.members, (
        f"{site.path}:{site.line}: document {site.name!r} accepts {accepted}, the annotation says {site.members}"
    )
    assert expected == site.members, (
        f"{site.path}:{site.line}: document {site.name!r} computes {expected}, the annotation says {site.members}"
    )


def test_the_schema_vocabulary_is_the_librarys() -> None:
    """``rcp.schemas`` re-exports the library's two vocabulary names -- one class and one function, not a copy --
    so a caller reads the payload tag and the accepted spellings from the module it already imports."""
    import configkit.schemas as library

    assert SchemaTagged is library.SchemaTagged
    assert accepted_schema_ids is library.accepted_schema_ids
