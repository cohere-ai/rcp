#!/usr/bin/env python3
"""The ``configkit`` carve-out as a re-runnable transform (PLAN.md, CARVE-OUT C0).

Moves the configuration library out of the product's distribution into its own, so a second consumer
can depend on it without the product's stack:

    packages/rcp/src/configkit/   -> packages/configkit/src/configkit/
    packages/rcp/tests/configkit/ -> packages/configkit/tests/configkit/

and substitutes the four manifest lines the new workspace member needs -- the root
``[tool.uv.sources]`` entry, the root ``testpaths`` entry, the ``cohere-rcp`` dependency line in
``packages/rcp/pyproject.toml`` and the root ``[tool.basedpyright]`` include. Everything is a declared
table, never a recorded patch, so the same script replays on a later tip.

The transform writes no new package file: ``packages/configkit/pyproject.toml``, ``README.md``,
``LICENSE``, ``NOTICE``, ``src/configkit/py.typed`` and ``tests/conftest.py`` are the new
distribution's own (authored in the same commit), and ``--check`` asserts they exist rather than
letting a build warn about a license it does not ship.

Usage::

    python tools/carve_configkit.py            # perform the carve-out in this repository
    python tools/carve_configkit.py --check    # exit 0 iff the tree is already carved

Idempotent: on an already-carved tree it changes nothing and says so. It refuses (exit 1, listed) a
move it cannot make -- a source that is gone, a destination that already exists beside it -- any file
still tracked under the two old paths, and any manifest line that is neither the pre-carve spelling
nor the carved one.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

__all__ = ["MOVES", "OLD_ROOTS", "REQUIRED_FILES", "SUBSTITUTIONS", "main", "run"]

#: The name a manifest line declares: a table key (``testpaths = [...]``) or the requirement a dependency list
#: names (``"cohere-configkit==0.0.1",``). A comment or a table header declares none.
_DECLARATION = re.compile(r"\s*[\"']?([A-Za-z0-9][A-Za-z0-9_.-]*)")

MOVES: tuple[tuple[str, str], ...] = (
    # The library's source and its suite, each gaining the packages/ level under the new member. The
    # import name does not move with them: the package is still `configkit`.
    ("packages/rcp/src/configkit", "packages/configkit/src/configkit"),
    ("packages/rcp/tests/configkit", "packages/configkit/tests/configkit"),
)

OLD_ROOTS: tuple[str, ...] = tuple(source for source, _destination in MOVES)
"""The paths the carve-out empties: a tracked file under one after the move is a refusal, never a stray."""

SUBSTITUTIONS: tuple[tuple[str, str | None, str, str | None], ...] = (
    # (manifest, the line it carries before the carve-out, the line it must carry after, the line to
    # insert the new one after). A replacement names both spellings; an insertion names no `before`
    # and an `anchor` line that must already be in the table.
    #
    # An inter-member requirement resolves to the checkout, never to an index.
    ("pyproject.toml", None, "cohere-configkit = { workspace = true }", "rcp-test = { workspace = true }"),
    # The new member's suite is collected with the root tree, like the two distributions' suites.
    (
        "pyproject.toml",
        'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests"]',
        'testpaths = ["tests", "packages/rcp/tests", "packages/rcp-core/tests", "packages/configkit/tests"]',
        None,
    ),
    # The product depends on the library, pinned exactly like the core: one version across the tree.
    ("packages/rcp/pyproject.toml", None, '  "cohere-configkit==0.0.1",', '  "cohere-rcp-core==0.0.1",'),
    # The library's source tree is type-checked with the four the move already carries.
    (
        "pyproject.toml",
        'include = ["packages/rcp/src", "packages/rcp-core/src", "packages/rcp-vllm/src", "packages/rcp-test/src"]',
        'include = ["packages/rcp/src", "packages/rcp-core/src", "packages/rcp-vllm/src", '
        '"packages/rcp-test/src", "packages/configkit/src"]',
        None,
    ),
)
"""The manifest lines the carve-out substitutes, line-exact: the ``[tool.uv.sources]`` entry, the
``testpaths`` entry, the ``cohere-rcp`` dependency line and the ``basedpyright`` include."""

REQUIRED_FILES: tuple[str, ...] = (
    # The new distribution's own files. The transform does not write them -- a manifest, a license and
    # a NOTICE are authored, not substituted -- but `--check` refuses a tree without them: a wheel
    # built without a README or a license file warns and ships neither.
    "packages/configkit/pyproject.toml",
    "packages/configkit/README.md",
    "packages/configkit/LICENSE",
    "packages/configkit/NOTICE",
    "packages/configkit/src/configkit/py.typed",
    "packages/configkit/tests/conftest.py",
)
"""What the carved tree must carry besides the moves and the substituted lines."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _tracked_files(root: Path) -> list[Path]:
    proc = _git(root, "ls-files", "-z")
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files failed: {proc.stderr.strip()}")
    return [Path(name) for name in proc.stdout.split("\0") if name]


def _declares(line: str) -> str | None:
    """The key *line* declares, or ``None`` for a line that declares none (a comment, a table header)."""
    match = _DECLARATION.match(line)
    return match.group(1) if match else None


def _parse_problem(text: str) -> str | None:
    """``None`` when *text* is TOML, else the parse error: the transform never writes what ``uv`` cannot read."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return str(exc)
    return None


def _substitute(
    root: Path,
    manifest: str,
    before: str | None,
    after: str,
    anchor: str | None,
    *,
    check: bool,
) -> tuple[bool, str | None]:
    """Bring one manifest line to its carved spelling.

    Inputs: the repository root, the manifest's path relative to it, the pre-carve line (``None`` for
    an insertion), the carved line, and the line an insertion goes after. Outputs: whether the
    manifest changed, and the refusal when the line could be neither found nor inserted.

    A manifest that holds both spellings is a duplicate TOML key, and an insertion whose key is already
    declared with another value would insert a twin: both are refused rather than counted as carved.
    """
    path = root / manifest
    if not path.is_file():
        return False, f"{manifest}: missing (the carve-out's manifest is not in this tree)"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if after in lines:
        if before is not None and before in lines:
            return False, f"{manifest}: both {before!r} and {after!r} are present (a duplicate key)"
        if lines.count(after) > 1:
            return False, f"{manifest}: {after!r} appears {lines.count(after)} times (a duplicate key)"
        if (problem := _parse_problem(text)) is not None:
            return False, f"{manifest}: is not TOML ({problem})"
        return False, None
    if before is not None and before in lines:
        if lines.count(before) > 1:
            return False, f"{manifest}: {before!r} appears {lines.count(before)} times (a line to replace is unique)"
        lines[lines.index(before)] = after
    elif before is None and anchor is not None and anchor in lines:
        key = _declares(after)
        declared = [line for line in lines if key is not None and _declares(line) == key and line.strip() != after]
        if declared:
            return False, f"{manifest}: {key!r} is already declared as {declared[0].strip()!r} (refusing a twin)"
        lines.insert(lines.index(anchor) + 1, after)
    else:
        wanted = before if before is not None else anchor
        return False, f"{manifest}: no {wanted!r} to carry {after!r}"
    if check:
        return False, f"{manifest}: {after!r} is missing (the manifest is not substituted)"
    rewritten = "\n".join(lines) + "\n"
    if (problem := _parse_problem(rewritten)) is not None:
        return False, f"{manifest}: the substitution would not be TOML ({problem}); nothing was written"
    path.write_text(rewritten, encoding="utf-8")
    return True, None


def run(root: Path, *, check: bool = False) -> int:
    """Perform (or, with ``check``, verify) the carve-out in ``root``.

    Inputs: the repository root. Outputs: the exit status -- 0 when the tree is carved (or, without
    ``check``, was brought to the moves and substitutions the transform owns), 1 when a move
    conflicts, a file survives under an old path, a manifest line is neither spelling, or a file the
    new distribution must carry is missing; prints every move and every refusal either way.
    """
    problems: list[str] = []
    moved: list[str] = []
    already: list[str] = []
    substituted: list[str] = []
    changed: list[str] = []
    unmoved: set[str] = set()

    # 1. The moves (git mv keeps the history).
    for source, destination in MOVES:
        src, dst = root / source, root / destination
        if not src.exists() and not dst.exists():
            problems.append(f"{source}: neither {source} nor {destination} exists")
            continue
        if not src.exists():
            already.append(f"{source} -> {destination}")
            continue
        if dst.exists():
            problems.append(f"{source}: destination {destination} already exists")
            continue
        if check:
            problems.append(f"{source} is unmoved (would move to {destination})")
            unmoved.add(source)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        proc = _git(root, "mv", source, destination)
        if proc.returncode != 0:
            problems.append(f"git mv {source} {destination} failed: {proc.stderr.strip()}")
            continue
        moved.append(f"{source} -> {destination}")
        changed.append(destination)

    # 2. The manifest substitutions, line-exact.
    for manifest, before, after, anchor in SUBSTITUTIONS:
        wrote, problem = _substitute(root, manifest, before, after, anchor, check=check)
        if problem is not None:
            problems.append(problem)
        elif wrote:
            substituted.append(f"{manifest}: {after}")
            changed.append(manifest)

    # 3. Nothing the carve-out took away may survive under an old path. A source the move itself
    #    refuses as unmoved is named there -- every file under it is still in place on purpose -- so
    #    this scan names what a move left behind, or what was added under the old path after it.
    for name in _tracked_files(root):
        text = name.as_posix()
        if any(text == old or text.startswith(f"{old}/") for old in OLD_ROOTS if old not in unmoved):
            problems.append(f"{text}: survives the carve-out")

    # 4. The new distribution's own files: the transform writes none of them (a manifest, a license and
    #    a NOTICE are authored, not substituted), so `--check` is where their absence is a refusal --
    #    a wheel built without them warns and ships no license. `run` reports them and carries on: they
    #    are the same commit's files, and a run that stopped on them could never stage its own work.
    missing_files = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if check:
        problems.extend(f"{name}: missing (the new distribution's own file)" for name in missing_files)

    # 5. Stage what the transform moved or rewrote, never the whole worktree.
    if not check and changed:
        staged = _git(root, "add", "--", *sorted(set(changed)))
        if staged.returncode != 0:
            print(f"carve_configkit: git add failed: {staged.stderr.strip()}", file=sys.stderr)
            return 1

    # Report.
    for line in moved:
        print(f"moved   {line}")
    for line in already:
        print(f"moved   {line} (already done)")
    for line in substituted:
        print(f"rewrote {line}")
    if problems:
        print("carve_configkit: refusals:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    if check:
        print("carve_configkit --check: the tree is carved, no old path anywhere")
        return 0
    if moved or substituted:
        print(f"carve_configkit: done ({len(moved)} moves, {len(substituted)} manifest lines)")
        print("carve_configkit: staged; commit the result")
    else:
        print("carve_configkit: already carved; nothing to do")
    if missing_files:
        print("carve_configkit: the new distribution's own files are still missing:")
        for name in missing_files:
            print(f"  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="carve_configkit", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the tree only; change nothing")
    parser.add_argument("--repo", type=Path, default=None, help="the repository to transform (default: this one)")
    args = parser.parse_args(argv)
    root = (args.repo or Path(__file__).resolve().parents[1]).resolve()
    return run(root, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
