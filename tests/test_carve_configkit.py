"""``tools/carve_configkit.py`` moves the library out of the product and stays replayable.

The tests run the real script against a small fixture repository under ``tmp_path``: the two moves,
the four manifest substitutions, a second run that changes nothing, and the refusals (an unmoved
source, a conflicting target, a surviving old path, a drifted manifest, a missing package file). The
checkout is only ever read -- at C0 the real tree is *not* carved, so an assertion on it would be red
by design; the real-tree ``--check`` belongs to the commit that runs the transform.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REAL_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "carve_configkit.py"

ROOT_PYPROJECT = """\
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
# An inter-member requirement resolves to the checkout, never to an index.
cohere-rcp = { workspace = true }
cohere-rcp-core = { workspace = true }
cohere-rcp-vllm = { workspace = true }
rcp-test = { workspace = true }

[tool.pytest.ini_options]
testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests"]

[tool.basedpyright]
include = ["packages/rcp/src", "packages/rcp-core/src", "packages/rcp-vllm/src", "packages/rcp-test/src"]
"""

RCP_PYPROJECT = """\
[project]
name = "cohere-rcp"
version = "0.0.1"
dependencies = [
  # The core is released in lockstep with this package: the same version, pinned.
  "cohere-rcp-core==0.0.1",
  "pydantic>=2.11",
]
"""

FIXTURE: dict[str, str] = {
    "pyproject.toml": ROOT_PYPROJECT,
    "packages/rcp/pyproject.toml": RCP_PYPROJECT,
    "packages/rcp/src/configkit/__init__.py": '"""The library."""\n',
    "packages/rcp/src/configkit/audit.py": '"""The audit rules."""\n',
    "packages/rcp/src/configkit/py.typed": "",
    "packages/rcp/tests/configkit/test_audit.py": "def test_scan():\n    assert True\n",
    "packages/rcp/tests/configkit/fixtures/identity_vectors.json": "{}\n",
}

#: The new distribution's own files: the transform writes none of them, and `--check` refuses a tree
#: without them. The fixture creates them to reach the carved state.
PACKAGE_FILES: tuple[str, ...] = (
    "packages/configkit/pyproject.toml",
    "packages/configkit/README.md",
    "packages/configkit/LICENSE",
    "packages/configkit/NOTICE",
    "packages/configkit/src/configkit/py.typed",
    "packages/configkit/tests/conftest.py",
)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return proc.stdout


def make_fixture(tmp_path: Path) -> Path:
    """A committed fixture repository, in the layout the carve-out starts from."""
    repo = tmp_path / "fixture"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for name, text in FIXTURE.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=fixture", "-c", "user.email=f@example.invalid", "commit", "-qm", "fixture")
    return repo


def carve(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REAL_SCRIPT), "--repo", str(repo), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def digest(root: Path) -> dict[str, str]:
    """Every file under ``root``, by content hash: what an idempotent second run must not move."""
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def tool() -> ModuleType:
    """``tools/carve_configkit.py`` loaded by path: the transform is a script, not an installed module."""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("carve_configkit_under_test", REAL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_table_names_the_two_moves_and_the_four_manifest_lines() -> None:
    """The transform is its tables: the two ``git mv``s, the four line-exact substitutions, six files."""
    module = tool()
    assert module.MOVES == (
        ("packages/rcp/src/configkit", "packages/configkit/src/configkit"),
        ("packages/rcp/tests/configkit", "packages/configkit/tests/configkit"),
    )
    assert module.OLD_ROOTS == tuple(source for source, _destination in module.MOVES)
    assert [after for _manifest, _before, after, _anchor in module.SUBSTITUTIONS] == [
        "cohere-configkit = { workspace = true }",
        'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests", "packages/configkit/tests"]',
        '  "cohere-configkit==0.0.1",',
        'include = ["packages/rcp/src", "packages/rcp-core/src", "packages/rcp-vllm/src", '
        '"packages/rcp-test/src", "packages/configkit/src"]',
    ]
    assert module.REQUIRED_FILES == PACKAGE_FILES


def test_check_refuses_an_uncarved_tree(tmp_path: Path) -> None:
    """Today's tree, in miniature: ``--check`` exits 1 and names every unmoved piece."""
    repo = make_fixture(tmp_path)
    proc = carve(repo, "--check")
    assert proc.returncode == 1
    assert "packages/rcp/src/configkit is unmoved" in proc.stderr
    assert "packages/rcp/tests/configkit is unmoved" in proc.stderr
    assert "cohere-configkit = { workspace = true }' is missing" in proc.stderr
    assert "packages/configkit/README.md: missing" in proc.stderr
    # the check changed nothing
    assert (repo / "packages" / "rcp" / "src" / "configkit" / "audit.py").is_file()


def test_the_transform_moves_and_substitutes(tmp_path: Path) -> None:
    """Both directories move (history kept) and the four manifest lines land, line-exact."""
    repo = make_fixture(tmp_path)
    proc = carve(repo)
    assert proc.returncode == 0, proc.stderr

    assert (repo / "packages" / "configkit" / "src" / "configkit" / "audit.py").is_file()
    assert (repo / "packages" / "configkit" / "src" / "configkit" / "py.typed").is_file()
    assert (repo / "packages" / "configkit" / "tests" / "configkit" / "test_audit.py").is_file()
    assert (repo / "packages" / "configkit" / "tests" / "configkit" / "fixtures" / "identity_vectors.json").is_file()
    assert not (repo / "packages" / "rcp" / "src" / "configkit").exists()
    assert not (repo / "packages" / "rcp" / "tests" / "configkit").exists()

    root_manifest = (repo / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert "cohere-configkit = { workspace = true }" in root_manifest
    assert 'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests", "packages/configkit/tests"]' in (
        root_manifest
    )
    assert (
        'include = ["packages/rcp/src", "packages/rcp-core/src", "packages/rcp-vllm/src", '
        '"packages/rcp-test/src", "packages/configkit/src"]' in root_manifest
    )
    # the pre-carve spellings are replaced, never kept beside the carved ones
    assert 'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests"]' not in root_manifest
    rcp_manifest = (repo / "packages" / "rcp" / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert '  "cohere-configkit==0.0.1",' in rcp_manifest
    assert '  "cohere-rcp-core==0.0.1",' in rcp_manifest  # the core pin stays, the library pin joins it

    # HISTORY KEPT: committing the staged result and following a moved file reaches the fixture commit.
    git(repo, "-c", "user.name=fixture", "-c", "user.email=f@example.invalid", "commit", "-qm", "the carve-out")
    history = git(repo, "log", "--follow", "--oneline", "--", "packages/configkit/src/configkit/audit.py")
    assert "fixture" in history


def test_check_passes_once_the_new_package_carries_its_own_files(tmp_path: Path) -> None:
    """The moves and the lines are the transform's; the six files are authored, and ``--check`` wants them."""
    repo = make_fixture(tmp_path)
    assert carve(repo).returncode == 0
    missing = carve(repo, "--check")
    assert missing.returncode == 1
    assert "packages/configkit/LICENSE: missing" in missing.stderr

    for name in PACKAGE_FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    git(repo, "add", "-A")
    carved = carve(repo, "--check")
    assert carved.returncode == 0, carved.stderr
    assert "carve_configkit --check: the tree is carved" in carved.stdout


def test_the_transform_is_idempotent(tmp_path: Path) -> None:
    repo = make_fixture(tmp_path)
    assert carve(repo).returncode == 0
    before = digest(repo)
    second = carve(repo)
    assert second.returncode == 0
    assert "already carved; nothing to do" in second.stdout
    assert digest(repo) == before


def test_a_surviving_old_path_is_refused(tmp_path: Path) -> None:
    """A file added under an emptied root after the move is named, never left as a stray copy."""
    repo = make_fixture(tmp_path)
    assert carve(repo).returncode == 0
    stray = repo / "packages" / "rcp" / "src" / "configkit" / "stray.py"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("", encoding="utf-8")
    git(repo, "add", "packages/rcp/src/configkit/stray.py")
    proc = carve(repo)
    assert proc.returncode == 1
    assert "packages/rcp/src/configkit/stray.py: survives the carve-out" in proc.stderr


def test_a_conflicting_target_is_refused(tmp_path: Path) -> None:
    repo = make_fixture(tmp_path)
    (repo / "packages" / "configkit" / "src" / "configkit").mkdir(parents=True)
    proc = carve(repo)
    assert proc.returncode == 1
    assert "destination packages/configkit/src/configkit already exists" in proc.stderr


def test_a_drifted_manifest_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """Neither spelling present: the transform names the line it wanted instead of inserting a twin."""
    repo = make_fixture(tmp_path)
    manifest = repo / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests"]',
            'testpaths = ["tests"]',
        ),
        encoding="utf-8",
    )
    git(repo, "add", "pyproject.toml")
    proc = carve(repo)
    assert proc.returncode == 1
    assert 'pyproject.toml: no \'testpaths = ["tests", "packages/rcp/tests"' in proc.stderr
