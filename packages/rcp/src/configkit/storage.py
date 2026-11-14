"""The bytes: one atomic-write seam, one advisory lock, one run layout, and the storage-URI seam.

Three seams, and none of them assumes the caller's storage (rcp-36, rcp-37, rcp-38).

``publish``
    The **atomic write is an argument** (:class:`~configkit.ports.AtomicWriter`), so the library never swaps a file
    the caller did not hand it and never assumes the destination is a local path. ``storage/core.py``'s ``publish``
    and ``calibration/fit.py``'s ``_swap_in`` are two implementations of this one idea; the seam is the caller's
    and the buffer is this module's.
``lock``
    One **advisory lock**, with a documented POSIX requirement: it is ``fcntl.flock``, so it excludes a second
    holder on a POSIX host and refuses to pretend elsewhere. The product hand-writes three of these
    (``judging/store.py``, ``storage/core.py``, ``storage/census.py``); this is the one.
``RunLayout``
    One **run and step directory layout** (emb-train 24): a run's directory is ``<root>/<run id>`` and a step's is
    ``<run dir>/<step>``, so a CLI and a store that both name a run's directory cannot disagree. The library owns
    no repository path, so the root is the caller's.
``read_uri``/``write_uri``
    The **storage-URI seam** routes through :attr:`configkit.ports.Ports.uri`, and the library ships the
    ``file://`` handler only. This row is *partial by design* (rcp-38): a ``gs://`` or ``s3://`` handler is a
    consumer adapter on the same seam, so the library needs no cloud SDK -- and a remote scheme with no handler is
    refused, naming the scheme, rather than read as a local path.

Nothing here installs a handler, reads a file or opens a lock at import: every call is the caller's.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote

from configkit.faults import OSErrorFault, ValueErrorFault
from configkit.ports import AtomicWriter, Ports

__all__ = ["RunLayout", "lock", "publish", "read_uri", "write_uri"]

#: The one scheme the library serves itself; every other scheme is a consumer's :class:`~configkit.ports.UriIO`.
_LOCAL_SCHEME = "file"


@dataclass(frozen=True)
class RunLayout:
    """The one run and step directory layout (emb-train 24), rooted where the caller says.

    Attributes:
        root: The directory every run lives under. The library owns no repository path, so this is the caller's.
    """

    root: Path

    def run_dir(self, run_id: str) -> Path:
        """The directory of run *run_id*.

        Args:
            run_id: The run's id, one path segment.

        Returns:
            ``<root>/<run_id>``.

        Raises:
            Fault: *run_id* is blank or carries a path separator (``code`` is ``"value_error"``).
        """
        return Path(self.root) / _segment(run_id, what="run id")

    def step_dir(self, run_id: str, step: str) -> Path:
        """The directory of *step* inside run *run_id*.

        Args:
            run_id: The run's id, one path segment.
            step: The step's name, one path segment.

        Returns:
            ``<root>/<run_id>/<step>``.

        Raises:
            Fault: either segment is blank or carries a path separator (``code`` is ``"value_error"``).
        """
        return self.run_dir(run_id) / _segment(step, what="step")


def lock(path: Path) -> AbstractContextManager[None]:
    """An exclusive advisory lock on *path* for the duration of the ``with`` block (rcp-37; POSIX only).

    The lock is ``fcntl.flock`` on a file this call creates (its directory too), so it excludes a second holder on
    the same host and is released when the block ends -- normally or by an exception. It is **advisory**: a process
    that does not take the lock is not stopped by it, which is why the helper exists (one rule, three call sites)
    rather than because it makes anything safe by itself.

    Args:
        path: The lock file. It is created when absent; its content is never read.

    Returns:
        A context manager yielding ``None`` while the lock is held.

    Raises:
        Fault: the host is not POSIX (no ``fcntl``), or the lock file cannot be opened. ``code`` is
            ``"os_error"`` and the message names the requirement.
    """
    return _locked(path)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the exclusive lock on *path* for the body's duration; the body's own failure propagates."""
    fcntl = _fcntl()
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
    except OSError as exc:
        raise OSErrorFault(
            f"the lock file {str(target)!r} cannot be opened: {exc}", details={"path": str(target)}
        ) from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def publish(uri: str, write: Callable[[BinaryIO], None], *, atomic: AtomicWriter) -> None:
    """Write the bytes *write* produces at *uri*, through the caller's atomic swap (rcp-36).

    The destination must be local: an atomic swap is one file on one filesystem, and a remote artifact is written
    through :func:`write_uri` with a :class:`~configkit.ports.UriIO` handler instead. The buffer is this module's,
    so *write* never holds the destination's path and cannot write a half-swapped file.

    Args:
        uri: The destination: a bare path or a ``file://`` URI.
        write: The producer; it writes the artifact's bytes to the binary stream it is handed.
        atomic: The caller's atomic-write seam: one path, one byte string, one swap.

    Raises:
        Fault: *uri* names a scheme other than ``file`` (``code`` is ``"value_error"``,
            ``details["scheme"]`` is the scheme).
    """
    path = _local_path(uri)
    buffer = io.BytesIO()
    write(buffer)
    atomic.write(path, buffer.getvalue())


def read_uri(uri: str, *, ports: Ports | None = None) -> bytes:
    """The bytes at *uri* (rcp-38).

    With *ports*, the read is the consumer's :class:`~configkit.ports.UriIO` -- every scheme, ``file://``
    included. Without it, the library's own ``file://`` handler serves a bare path or a ``file://`` URI, and any
    other scheme is refused.

    Args:
        uri: The source: a bare path, a ``file://`` URI, or a scheme a handler serves.
        ports: The host services, when the caller has them.

    Returns:
        The bytes at *uri*.

    Raises:
        Fault: the scheme has no handler here (``code`` is ``"value_error"``, ``details["scheme"]`` names it), or
            the local file cannot be read (``code`` is ``"os_error"``).
    """
    if ports is not None:
        return ports.uri.read(uri)
    return _FileUri().read(uri)


def write_uri(uri: str, data: bytes, *, ports: Ports | None = None) -> None:
    """Write *data* at *uri* (rcp-38).

    The routing is :func:`read_uri`'s, and the library's own handler creates the directory a local file needs. A
    local write here is **not** atomic: :func:`publish` is the seam for a swap.

    Args:
        uri: The destination: a bare path, a ``file://`` URI, or a scheme a handler serves.
        data: The bytes to write.
        ports: The host services, when the caller has them.

    Raises:
        Fault: the scheme has no handler here (``code`` is ``"value_error"``, ``details["scheme"]`` names it), or
            the local file cannot be written (``code`` is ``"os_error"``).
    """
    if ports is not None:
        ports.uri.write(uri, data)
        return
    _FileUri().write(uri, data)


class _FileUri:
    """The one scheme the library serves: a local path, as a bare path or a ``file://`` URI."""

    def read(self, uri: str) -> bytes:
        """The bytes of the local file *uri* names.

        Raises:
            Fault: the scheme is not ``file``, or the file cannot be read.
        """
        path = _local_path(uri)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise OSErrorFault(f"{str(path)!r} cannot be read: {exc}", details={"path": str(path)}) from exc

    def write(self, uri: str, data: bytes) -> None:
        """Write *data* at the local file *uri* names, creating the directory it needs.

        Raises:
            Fault: the scheme is not ``file``, or the file cannot be written.
        """
        path = _local_path(uri)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise OSErrorFault(f"{str(path)!r} cannot be written: {exc}", details={"path": str(path)}) from exc


def _local_path(uri: str) -> Path:
    """The local path *uri* names, refusing a scheme the library does not serve.

    Raises:
        Fault: *uri* names a scheme other than ``file``. ``code`` is ``"value_error"``, ``details["scheme"]`` is
            the scheme, and the hint names the seam a handler is injected through.
    """
    scheme, separator, rest = uri.partition("://")
    if not separator:
        return Path(uri)
    if scheme.lower() != _LOCAL_SCHEME:
        raise ValueErrorFault(
            f"{uri!r} names the {scheme!r} scheme, and the library serves {_LOCAL_SCHEME!r} only",
            details={"uri": uri, "scheme": scheme},
            hint="pass Ports.uri with a handler for this scheme (a consumer adapter on the same seam)",
        )
    return Path(unquote(rest))


def _segment(value: str, *, what: str) -> str:
    """*value* as one path segment, refused when it is blank or would escape its directory.

    Raises:
        Fault: *value* is blank, is ``.``/``..``, or carries a separator. ``code`` is ``"value_error"``.
    """
    if not isinstance(value, str) or not value.strip() or value in {".", ".."}:
        raise ValueErrorFault(f"a {what} must be a non-empty path segment, got {value!r}", details={what: value})
    if "/" in value or "\\" in value or os.sep in value:
        raise ValueErrorFault(
            f"a {what} must be one path segment, got {value!r}; a separator would name a directory outside the root",
            details={what: value},
        )
    return value


def _fcntl() -> Any:
    """The POSIX advisory-lock module, or the fault that names the requirement (rcp-37 is POSIX only).

    Raises:
        Fault: the host has no ``fcntl``. ``code`` is ``"os_error"`` and the message names the requirement.
    """
    try:
        import fcntl
    except ModuleNotFoundError as exc:  # a non-POSIX host: the requirement is documented, not emulated
        raise OSErrorFault(
            "the advisory lock needs a POSIX host (fcntl); this platform has none",
            hint="run on a POSIX host, or supply your own lock and pass it to the caller",
        ) from exc
    return fcntl
