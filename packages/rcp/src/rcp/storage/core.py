"""Filesystem operations that work the same on local disk and object storage.

Everything routes through fsspec, so ``gs://``, ``s3://``, ``az://``, ``hf://``,
``http://`` and plain paths are interchangeable at every call site.  The
protocol-specific packages are optional extras; asking for a protocol that is
not installed raises a message naming the extra to install rather than an
``ImportError`` from three frames down.

The two publication seams are :mod:`configkit.storage`'s: :func:`publish` hands the library the staging file and
an :class:`~configkit.ports.AtomicWriter` (this module's default is :class:`AtomicReplace`, and a caller with a
stricter swap passes its own), and :func:`publication_lock` is the library's one advisory lock. fsspec stays
here: it is the consumer's URI layer, and :class:`FsspecUri` is the ``Ports.uri`` implementation the library's
``read_uri``/``write_uri`` route through (rcp-38 is partial by design -- the library ships the ``file://`` handler
only).
"""

from __future__ import annotations

import os
import posixpath
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from configkit.ports import AtomicWriter
from configkit.storage import lock as _lock
from configkit.storage import publish as _publish

from rcp.errors import ConfigError, DataError, DependencyError, dependency_error
from rcp.storage.uri import is_remote, local_path, protocol_of, split_protocol

#: Protocol -> the module that serves it, so a missing backend names the install command
#: (:func:`rcp.errors.dependency_error` knows which extra provides which module).
_BACKEND_MODULES: dict[str, str] = {
    "gs": "gcsfs",
    "gcs": "gcsfs",
    "s3": "s3fs",
    "s3a": "s3fs",
    "az": "adlfs",
    "abfs": "adlfs",
    "abfss": "adlfs",
    "adl": "adlfs",
    "hf": "huggingface_hub",
    "http": "aiohttp",
    "https": "aiohttp",
}

_FS_CACHE: dict[tuple[str, tuple[tuple[str, Any], ...]], Any] = {}


def filesystem(uri: str | Path, **options: Any):
    """Return the fsspec filesystem serving *uri*.

    Instances are cached per (protocol, options) because fsspec filesystems
    hold connection pools and credential state that is expensive to rebuild --
    the retrieval and judging loops resolve thousands of paths.
    """
    import fsspec

    protocol = protocol_of(uri)
    key = (protocol, tuple(sorted(options.items())))
    cached = _FS_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        fs = fsspec.filesystem(protocol, **options)
    except ValueError as exc:  # fsspec: "Protocol not known"
        raise DependencyError(
            f"no fsspec filesystem is registered for {protocol}:// paths",
            hint=f"install the package that provides it, or register one: fsspec.register_implementation("
            f"{protocol!r}, MyFileSystem) or an 'fsspec.specs' entry point",
        ) from exc
    except ImportError as exc:
        module = _BACKEND_MODULES.get(protocol)
        if module is None:
            raise DependencyError(
                f"no storage backend installed for {protocol}:// paths",
                hint=f"install the fsspec implementation of {protocol}:// paths",
            ) from exc
        raise dependency_error(module, needed_for=f"reading {protocol}:// paths") from exc
    _FS_CACHE[key] = fs
    return fs


def _strip(uri: str | Path) -> str:
    """Return the path fsspec expects (protocol retained; it parses it itself)."""
    return str(uri)


@contextmanager
def open_path(uri: str | Path, mode: str = "rb", **kwargs: Any) -> Iterator[IO[Any]]:
    """Open *uri* for reading or writing, creating parent directories on write.

    ``encoding`` defaults to UTF-8 for text modes so behaviour does not drift
    with the host locale.
    """
    if "b" not in mode:
        kwargs.setdefault("encoding", "utf-8")
    if any(flag in mode for flag in ("w", "a", "x")):
        makedirs(parent_of(uri))
    fs = filesystem(uri)
    with fs.open(_strip(uri), mode, **kwargs) as handle:
        yield handle


def parent_of(uri: str | Path) -> str:
    """Return the containing directory of *uri*."""
    from rcp.storage.uri import parent

    return parent(uri)


def exists(uri: str | Path) -> bool:
    """Return ``True`` if *uri* exists."""
    local = local_path(uri)
    if local is not None:
        return local.exists()
    return bool(filesystem(uri).exists(_strip(uri)))


def makedirs(uri: str | Path) -> None:
    """Create *uri* as a directory, including parents.

    Object stores have no directories; the call is a no-op there, which is why
    writers can call this unconditionally.
    """
    local = local_path(uri)
    if local is not None:
        local.mkdir(parents=True, exist_ok=True)
        return
    fs = filesystem(uri)
    try:
        fs.makedirs(_strip(uri), exist_ok=True)
    except (NotImplementedError, FileExistsError):
        pass


def ls(uri: str | Path, *, recursive: bool = False) -> list[str]:
    """List the children of *uri* (every file below it with ``recursive``).

    Returns fully-qualified URIs (protocol preserved) so results can be fed
    straight back into any other function here; :func:`relative` gives an
    entry's path below *uri*.
    """
    fs = filesystem(uri)
    path = _strip(uri).rstrip("/")
    if not fs.exists(path):
        return []
    entries = fs.find(path) if recursive else fs.ls(path, detail=False)
    protocol, _ = split_protocol(uri)
    out: list[str] = []
    for entry in entries:
        text = str(entry)
        if text.endswith("/"):
            continue
        if protocol and "://" not in text:
            text = f"{protocol}://{text.lstrip('/')}" if protocol != "file" else text
        out.append(text)
    return out


def _normalised(fs: Any, text: str) -> str:
    """*text* with dot segments resolved and a trailing slash dropped, in the filesystem's own
    spelling (some backends keep the scheme in ``_strip_protocol``, some drop it; both are
    normalised the same way, so the containment check sees the same shape on both sides). A
    surviving ``..`` can never cross the root, whatever the spelling."""
    protocol, _ = split_protocol(text)
    stripped = str(fs._strip_protocol(text)).rstrip("/")
    if protocol is None or protocol == "file":
        return os.path.normpath(stripped)
    return posixpath.normpath(stripped)


def relative(uri: str | Path, root: str | Path) -> str:
    """The ``/``-separated path of *uri* below the directory *root*, however either is spelled.

    Both are normalised as their filesystem normalises paths (dot segments resolved, a ``file://``
    or ``gs://`` prefix dropped), so an entry of :func:`ls` and the root it was listed from agree,
    and a ``..`` cannot escape the root: ``relative('/base/root/../../etc/passwd', '/base/root')``
    is refused, not returned.

    Raises:
        DataError: *uri* is not below *root*.
    """
    fs = filesystem(root)
    base = _normalised(fs, str(root))
    path = _normalised(fs, str(uri))
    if not path.startswith(f"{base}/"):
        raise DataError(f"{uri} is not below {root}")
    return path[len(base) + 1 :]


def read_bytes(uri: str | Path) -> bytes:
    """Read *uri* in full."""
    with open_path(uri, "rb") as handle:
        return handle.read()


def read_text(uri: str | Path, encoding: str = "utf-8") -> str:
    """Read *uri* as text."""
    return read_bytes(uri).decode(encoding)


def write_bytes(uri: str | Path, payload: bytes) -> None:
    """Write *payload* to *uri*, creating parents as needed."""
    with open_path(uri, "wb") as handle:
        handle.write(payload)


def write_text(uri: str | Path, payload: str, encoding: str = "utf-8") -> None:
    """Write *payload* to *uri* as text."""
    write_bytes(uri, payload.encode(encoding))


def get(remote: str | Path, local: str | Path) -> Path:
    """Download *remote* to *local* and return the local path."""
    target = Path(local)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = local_path(remote)
    if source is not None:
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return target
    filesystem(remote).get_file(_strip(remote), str(target))
    return target


def info(uri: str | Path) -> dict[str, Any]:
    """Return the backend's metadata for *uri* (size, mtime, etag/generation).

    A local file also reports ``mtime_ns``: ``mtime`` is a float of seconds whose resolution can be coarse
    enough that two same-size writes land on one stamp, and an identity that keys on it must see the
    difference.
    """
    local = local_path(uri)
    if local is not None:
        stat = local.stat()
        return {"size": stat.st_size, "mtime": stat.st_mtime, "mtime_ns": stat.st_mtime_ns}
    return dict(filesystem(uri).info(_strip(uri)))


@contextmanager
def publication_lock(target: str | Path, *, shared: bool = False) -> Iterator[None]:
    """An advisory lock over *target*'s publication (a payload and its record written as a pair).

    Two writers of one cache entry, index directory or artifact would otherwise interleave their payloads and
    records, leaving a pair that is internally inconsistent (one writer's vectors under another's record);
    the lock serialises them. A *reader* that must hold the payload still while it verifies and reads it
    takes the shared form (``shared=True``): it excludes a writer but not other readers. The lock is taken on
    a sibling ``<target>.lock`` file, so it is never part of what is published. It is
    :func:`configkit.storage.lock`, so it needs a POSIX host (the library refuses elsewhere rather than
    pretending); callers publish the payload before its record, so a torn pair is detected (the record
    describes what the payload must be) rather than served.

    Args:
        target: The path being published (a file or a directory).
        shared: Take a shared (reader) lock instead of the exclusive (writer) one.
    """
    lock_path = Path(target).with_name(f"{Path(target).name}.lock")
    with _lock(lock_path, shared=shared):
        yield


def publish(
    target: str | Path,
    write: Callable[[Path], Any],
    *,
    mode: int | None = None,
    atomic: AtomicWriter | None = None,
) -> None:
    """Materialise *target* atomically: *write* fills a temp file beside it, then one rename puts it in place.

    A reader of *target* sees either the previous file or the complete new one, never a half-written one
    -- which is what concurrent readers of a media cache, a rendered page or a downloaded corpus would
    otherwise see. The one home of the discipline: writers elsewhere call this instead of growing their
    own temp-file copies. The staging file, the mode rule and the swap are
    :func:`configkit.storage.publish`'s; the swap itself is the caller's :class:`~configkit.ports.AtomicWriter`
    (:class:`AtomicReplace` unless one is injected). The published file keeps the mode a plain write would give
    it: an existing target's own mode, else ``0666 & ~umask`` (``mkstemp`` creates the temp file 0600, which a
    shared reader could not open). *mode* is the file's permission bits (masked by the process umask); a run's
    records pass ``0o600``: what a shared cluster filesystem must not expose.

    Only a local path can publish by rename; a remote target raises, because object stores make each
    object visible whole anyway -- their writers use :func:`publish_bytes` or :func:`write_bytes`.

    Args:
        target: The local destination: a bare path or a ``file://`` URI.
        write: The producer; it fills the staging path it is handed.
        mode: The published file's permission bits, masked by the process umask.
        atomic: The atomic-write seam; the default is this module's :class:`AtomicReplace`.

    Raises:
        ConfigError: *target* is a remote URI.
    """
    path = local_path(target)
    if path is None:
        raise ConfigError(
            f"publish needs a local target, got {target}",
            hint="object stores make each object visible whole: write_bytes them directly",
        )
    _publish(str(path), write, atomic=atomic or _ATOMIC_REPLACE, mode=mode)


def publish_bytes(target: str | Path, payload: bytes, *, mode: int | None = None) -> None:
    """Write *payload* to *target*, atomically on a local filesystem (:func:`publish`).

    A remote URI takes the payload directly: an object store publishes per object, so there is no
    half-written file a concurrent reader could see. *mode* is :func:`publish`'s permission bits (e.g. ``0o600``
    for a run's records).
    """
    if is_remote(target):
        write_bytes(target, payload)
        return
    publish(target, lambda tmp: tmp.write_bytes(payload), mode=mode)


#: Serialises the fallback umask read (setting and restoring it is not atomic across threads).
_UMASK_LOCK = threading.Lock()


class AtomicReplace:
    """This package's atomic-write seam: write a sibling, then one ``os.replace`` (rcp-36).

    The default :func:`publish` passes; a caller with a stricter swap (an ``fsync`` before the rename, a copy onto
    another device) passes its own :class:`~configkit.ports.AtomicWriter` instead.
    """

    def write(self, path: Path, data: bytes) -> None:
        """Replace *path* with *data* in one rename: no reader ever sees a half-written file."""
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_bytes(data)
        os.replace(staging, path)

    def swap(self, path: Path, staged: Path) -> None:
        """Replace *path* with the file *staged* beside it: one rename."""
        os.replace(staged, path)


_ATOMIC_REPLACE = AtomicReplace()


class FsspecUri:
    """This package's :class:`~configkit.ports.UriIO`: every scheme fsspec serves, the local one included.

    The consumer's half of the storage-URI seam (rcp-38): :func:`configkit.storage.read_uri` and ``write_uri``
    route a URI through ``Ports.uri`` when the caller has one, so a ``gs://`` object is this layer's job and the
    library's ``file://`` handler is the only one the library ships.
    """

    def read(self, uri: str) -> bytes:
        """The bytes at *uri*."""
        return read_bytes(uri)

    def write(self, uri: str, data: bytes) -> None:
        """Write *data* at *uri*."""
        write_bytes(uri, data)


def _umask() -> int:
    """The process umask, without changing it: the kernel reports it in ``/proc/self/status``; elsewhere it is
    read by setting and restoring it under a module lock (the stdlib has no getter).

    Setting the process-wide umask to read it is not thread-safe: two callers (a run mirror's flush thread and
    the main thread) can interleave and leave the process umask at 0, making every later file world-writable.
    """
    reported = _umask_from_proc()
    if reported is not None:
        return reported
    with _UMASK_LOCK:
        current = os.umask(0)
        os.umask(current)
    return current


def _umask_from_proc() -> int | None:
    """The ``Umask:`` line of ``/proc/self/status`` in octal, or ``None`` where it is not readable."""
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        return None
    return None


__all__ = [
    "AtomicReplace",
    "FsspecUri",
    "exists",
    "filesystem",
    "get",
    "info",
    "ls",
    "makedirs",
    "open_path",
    "parent_of",
    "publish",
    "publish_bytes",
    "publication_lock",
    "read_bytes",
    "read_text",
    "relative",
    "write_bytes",
    "write_text",
]
