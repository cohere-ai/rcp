"""The bytes: one atomic-write seam, one advisory lock, one run layout, and the URI seam.

Three seams and no assumption about the caller's storage (rcp-36, rcp-37, rcp-38). The atomic write is an
argument, so the library never swaps a file its caller did not ask it to swap; the lock is one helper with a
documented POSIX requirement, so the three hand-written locks in the product collapse onto one; and a URI is read
and written through :class:`~configkit.ports.UriIO`, so a ``gs://`` or ``s3://`` handler is the consumer's adapter
and the library ships the ``file://`` one only. That last sentence is the *partial by design* half of rcp-38: the
library owns the seam and one scheme, and a remote scheme with no handler is refused rather than guessed at.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from configkit.faults import Fault
from configkit.ports import Ports
from configkit.storage import RunsRoot, lock, publish, publish_bytes, read_uri, write_uri


class _Atomic:
    """A recording :class:`~configkit.ports.AtomicWriter`: it keeps the bytes and the staged file of every swap."""

    def __init__(self) -> None:
        self.swaps: list[tuple[Path, bytes]] = []

    def write(self, path: Path, data: bytes) -> None:
        """Record the buffer form and perform it, so the file's content is the seam's proof."""
        self.swaps.append((path, data))
        path.write_bytes(data)

    def swap(self, path: Path, staged: Path) -> None:
        """Record the path form: the staged file the producer filled is renamed into place."""
        self.swaps.append((path, staged.read_bytes()))
        os.replace(staged, path)


class _Uri:
    """A recording :class:`~configkit.ports.UriIO`: it answers from a dict and records every call."""

    def __init__(self, held: dict[str, bytes] | None = None) -> None:
        self.held = dict(held or {})
        self.calls: list[tuple[str, str]] = []

    def read(self, uri: str) -> bytes:
        """The bytes stored at *uri*."""
        self.calls.append(("read", uri))
        return self.held[uri]

    def write(self, uri: str, data: bytes) -> None:
        """Store *data* at *uri*."""
        self.calls.append(("write", uri))
        self.held[uri] = data


def _ports(uri: _Uri) -> Ports:
    """Every host service a storage call may need, with the URI handler under test."""
    return Ports(
        clock=object(),  # type: ignore[arg-type]  # unused by storage
        budget=object(),  # type: ignore[arg-type]
        sink=lambda line: None,
        exit_codes={},
        renderer=str,
        atomic=_Atomic(),
        uri=uri,
    )


# ---------------------------------------------------------------------------------------------------------------
# publish: the atomic-write seam is an argument (rcp-36)
# ---------------------------------------------------------------------------------------------------------------


def test_publish_streams_through_a_staging_path_and_swaps_it(tmp_path: Path) -> None:
    """H3: the producer fills a staging file beside the destination; the artifact is never held in memory."""
    atomic = _Atomic()
    target = tmp_path / "artifact.bin"
    seen: list[Path] = []

    def write(staged: Path) -> None:
        seen.append(staged)
        with staged.open("wb") as handle:
            handle.write(b"first\n")
            handle.write(b"second\n")

    publish(f"file://{target}", write, atomic=atomic)
    assert len(seen) == 1
    assert seen[0].name.endswith(".tmp") and seen[0].parent == target.parent
    assert atomic.swaps == [(target, b"first\nsecond\n")]
    assert target.read_bytes() == b"first\nsecond\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_publish_applies_the_declared_mode_to_the_published_file(tmp_path: Path) -> None:
    """H3: the product's five run-record call sites pass ``mode=0o600`` on a shared filesystem."""
    target = tmp_path / "record.json"
    publish(f"file://{target}", lambda staged: staged.write_bytes(b"{}"), atomic=_Atomic(), mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == b"{}"


def test_publish_bytes_is_the_buffer_form_of_the_same_rule(tmp_path: Path) -> None:
    """A caller that already holds the bytes writes them through the same staging swap and mode."""
    atomic = _Atomic()
    target = tmp_path / "payload.bin"
    publish_bytes(f"file://{target}", b"x", atomic=atomic, mode=0o640)
    assert atomic.swaps == [(target, b"x")]
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_publish_accepts_a_bare_path(tmp_path: Path) -> None:
    """A local path with no scheme is a ``file://`` URI, so the two spellings reach the same seam."""
    atomic = _Atomic()
    publish(str(tmp_path / "plain.bin"), lambda staged: staged.write_bytes(b"x"), atomic=atomic)
    assert atomic.swaps == [(tmp_path / "plain.bin", b"x")]


def test_publish_refuses_a_remote_uri_naming_the_scheme() -> None:
    """The atomic swap is one local file's; a remote artifact is written through the URI seam instead."""
    with pytest.raises(Fault) as raised:
        publish("gs://YOUR-BUCKET/artifact.bin", lambda staged: staged.write_bytes(b"x"), atomic=_Atomic())
    assert "gs" in raised.value.message
    assert raised.value.details["scheme"] == "gs"


def test_a_file_uri_authority_is_stripped(tmp_path: Path) -> None:
    """L8: ``file://localhost/tmp/x`` was read as the relative path ``localhost/tmp/x``."""
    target = tmp_path / "x.bin"
    target.write_bytes(b"x")
    assert read_uri(f"file://localhost{target}") == b"x"
    assert read_uri(f"file://{target}") == b"x"


def test_lock_takes_the_shared_form_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H4: the product takes the publication lock shared for a reader (``publication_lock(..., shared=True)``).

    The library offered the exclusive form only, so a reader and a writer could not share the pair.
    """
    fcntl = pytest.importorskip("fcntl")
    real = fcntl.flock
    seen: list[int] = []

    def record(handle: int, operation: int) -> Any:
        seen.append(operation)
        return real(handle, operation)

    monkeypatch.setattr(fcntl, "flock", record)
    with lock(tmp_path / "reader.lock", shared=True):
        pass
    assert seen[0] == fcntl.LOCK_SH
    seen.clear()
    with lock(tmp_path / "writer.lock"):
        pass
    assert seen[0] == fcntl.LOCK_EX


# ---------------------------------------------------------------------------------------------------------------
# lock: one advisory lock, POSIX (rcp-37)
# ---------------------------------------------------------------------------------------------------------------


def test_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    """The lock is advisory and exclusive: a second holder of the same file waits, and the release frees it."""
    import fcntl

    target = tmp_path / "run.lock"
    with lock(target):
        with target.open("rb") as second:
            with pytest.raises(BlockingIOError):
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with target.open("rb") as third:
        fcntl.flock(third.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(third.fileno(), fcntl.LOCK_UN)


def test_lock_creates_the_directory_and_the_file(tmp_path: Path) -> None:
    """A lock on a path whose directory does not exist creates both, so a caller needs no setup."""
    target = tmp_path / "runs" / "r1" / "run.lock"
    with lock(target):
        assert target.is_file()
    assert target.is_file()


def test_lock_releases_on_an_exception(tmp_path: Path) -> None:
    """A body that raises still releases the lock, so one failure does not wedge the next run."""
    import fcntl

    target = tmp_path / "run.lock"
    with pytest.raises(RuntimeError):
        with lock(target):
            raise RuntimeError("the body failed")
    with target.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------------------------------------------
# RunsRoot: one run and step directory layout (emb-train 24)
# ---------------------------------------------------------------------------------------------------------------


def test_runs_root_names_the_run_and_step_directories(tmp_path: Path) -> None:
    """A run's directory is ``<root>/<run id>`` and a step's is ``<run dir>/<step>``, for one reader and one writer."""
    layout = RunsRoot(tmp_path)
    assert layout.run_dir("run-1") == tmp_path / "run-1"
    assert layout.step_dir("run-1", "embed") == tmp_path / "run-1" / "embed"


def test_runs_root_refuses_a_segment_that_escapes_the_root(tmp_path: Path) -> None:
    """A run id or a step carrying a separator is refused: a layout cannot name a directory outside its root."""
    layout = RunsRoot(tmp_path)
    for segment in ("", "../elsewhere", "a/b", "  "):
        with pytest.raises(Fault):
            layout.run_dir(segment)
    with pytest.raises(Fault):
        layout.step_dir("run-1", "a/b")


# ---------------------------------------------------------------------------------------------------------------
# the URI seam: through Ports.uri, or the library's file:// handler (rcp-38, partial by design)
# ---------------------------------------------------------------------------------------------------------------


def test_a_file_uri_is_served_by_the_library_handler(tmp_path: Path) -> None:
    """With no handler injected, the library's own ``file://`` handler reads and writes the local file."""
    target = tmp_path / "nested" / "artifact.bin"
    write_uri(f"file://{target}", b"bytes")
    assert target.read_bytes() == b"bytes"
    assert read_uri(f"file://{target}") == b"bytes"
    assert read_uri(str(target)) == b"bytes"


def test_a_file_uri_goes_through_ports_uri(tmp_path: Path) -> None:
    """With a handler injected, every URI -- a ``file://`` one included -- is the handler's."""
    uri = _Uri({f"file://{tmp_path}/held.bin": b"held"})
    ports = _ports(uri)
    assert read_uri(f"file://{tmp_path}/held.bin", ports=ports) == b"held"
    write_uri(f"file://{tmp_path}/held.bin", b"written", ports=ports)
    assert uri.calls == [("read", f"file://{tmp_path}/held.bin"), ("write", f"file://{tmp_path}/held.bin")]
    assert uri.held[f"file://{tmp_path}/held.bin"] == b"written"


def test_a_remote_uri_without_a_handler_is_refused_naming_the_scheme() -> None:
    """The library ships no cloud SDK, so a ``gs://`` URI with no handler is refused rather than guessed at."""
    with pytest.raises(Fault) as raised:
        read_uri("gs://YOUR-BUCKET/artifact.bin")
    assert raised.value.details["scheme"] == "gs"
    assert "Ports.uri" in (raised.value.hint or "")


def test_a_remote_uri_with_a_handler_is_the_handlers() -> None:
    """A consumer's ``gs://`` adapter is one :class:`~configkit.ports.UriIO`, and the library asks it."""
    uri = _Uri({"s3://YOUR-BUCKET/a.bin": b"remote"})
    assert read_uri("s3://YOUR-BUCKET/a.bin", ports=_ports(uri)) == b"remote"
    write_uri("s3://YOUR-BUCKET/a.bin", b"next", ports=_ports(uri))
    assert uri.held["s3://YOUR-BUCKET/a.bin"] == b"next"


def test_write_uri_creates_the_parent_directory(tmp_path: Path) -> None:
    """A ``file://`` write creates the directory it needs, so a writer needs no setup of its own."""
    target = tmp_path / "deep" / "path" / "artifact.bin"
    write_uri(f"file://{target}", b"x")
    assert target.is_file()


def test_a_callable_write_receives_a_buffer_not_the_file(tmp_path: Path) -> None:
    """``publish``'s ``write`` is handed a staging path, never the destination's own path."""
    seen: list[Path] = []
    publish(f"file://{tmp_path}/x.bin", lambda staged: seen.append(staged), atomic=_Atomic())
    assert len(seen) == 1
    assert seen[0] != tmp_path / "x.bin"
    assert seen[0].parent == tmp_path
