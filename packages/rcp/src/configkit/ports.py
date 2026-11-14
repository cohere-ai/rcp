"""The host services the library names and never builds, and the log facade.

One protocol per call, so a consumer injects its own implementation and the library holds no graph: a clock, a
step or wall-clock budget, a log sink, an exit-code table, a result renderer, an atomic writer and a URI
reader/writer. ``Ports`` is the one frozen record that carries them (rcp-3), and it names **every** service a
library module calls -- ``storage`` takes ``Ports.atomic`` and ``Ports.uri``, never a global.

``logger``/``configure``/``once`` are the log facade (rcp-35): the library creates loggers and installs no
handler until ``configure`` is called, so importing it changes nothing about the host's logging, and dedup is a
parameter rather than a policy.

``Tensor`` is typing-only: it names the array shape the numeric fits read, and this module imports no engine
(emb-train 29).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "AtomicWriter",
    "Budget",
    "Clock",
    "ExitCodeTable",
    "Ports",
    "Renderer",
    "Sink",
    "Tensor",
    "UriIO",
    "configure",
    "logger",
    "once",
]


class Clock(Protocol):
    """The clock, in seconds."""

    def now(self) -> float:
        """The current time in seconds; a monotonic clock for elapsed-time reporting."""
        ...


class Budget(Protocol):
    """A step count or a wall-clock deadline, checked before each step (rcp-39).

    One call, so an implementation decides whether it counts steps or seconds. The library raises nothing of its
    own here: an exhausted budget raises the caller's fault.
    """

    def check(self) -> None:
        """Return while the budget holds; raise the caller's fault when it is spent."""
        ...


class Sink(Protocol):
    """Where the log facade writes: the consumer's stream, file or in-memory list."""

    def __call__(self, line: str) -> None:
        """Write one formatted record line (no trailing newline is added)."""
        ...


class ExitCodeTable(Protocol):
    """The stable fault code -> process exit number table a front end reports (the consumer's numbers).

    A plain mapping satisfies it: ``exit_codes`` returns one, and a front end reads ``table[fault.code]`` (or
    ``table.get`` for a code the consumer did not name). The parameters are positional-only because that is the
    shape :class:`collections.abc.Mapping` gives them.
    """

    def __getitem__(self, code: str, /) -> int:
        """The exit number of *code*."""
        ...

    def get(self, code: str, default: int | None, /) -> int | None:
        """The exit number of *code*, or *default* when the table does not name it."""
        ...


class Renderer(Protocol):
    """A front end's renderer: one command result as the text it prints.

    The front end formats the library's model; it does not project a second payload (C6).
    """

    def __call__(self, result: Any) -> str:
        """The text for *result*."""
        ...


class AtomicWriter(Protocol):
    """One atomic swap of a file's contents: write a sibling, then rename (rcp-36)."""

    def write(self, path: Path, data: bytes) -> None:
        """Replace *path* with *data* atomically; no reader ever sees a half-written file."""
        ...

    def swap(self, path: Path, staged: Path) -> None:
        """Replace *path* with the file *staged* beside it: one rename, no half-written file.

        The form a streaming producer needs: the artifact is filled in *staged* and never held in memory.
        """
        ...


class UriIO(Protocol):
    """Bytes in and out of a storage URI, the implementation routing by scheme (rcp-38)."""

    def read(self, uri: str) -> bytes:
        """The bytes at *uri*."""
        ...

    def write(self, uri: str, data: bytes) -> None:
        """Write *data* at *uri*."""
        ...


class Tensor(Protocol):
    """The array a numeric fit reads: a shape and a dtype, nothing engine-specific (typing-only; imports nothing)."""

    @property
    def shape(self) -> tuple[int, ...]:
        """The array's dimensions."""
        ...

    @property
    def dtype(self) -> Any:
        """The array's element type, as the engine's own object."""
        ...


@dataclass(frozen=True)
class Ports:
    """Every host service one library call may need, injected by the consumer (the library builds no graph).

    Attributes:
        clock: The clock, in seconds.
        budget: The budget, checked before each step: a step count, or a wall-clock deadline in seconds.
        sink: Where the log facade writes.
        exit_codes: The stable code -> process exit number table.
        renderer: The front end's renderer for a command result.
        atomic: The atomic-write seam: one file, one swap.
        uri: The URI reader/writer seam, routed by scheme by the implementation.
    """

    clock: Clock
    budget: Budget
    sink: Sink
    exit_codes: ExitCodeTable
    renderer: Renderer
    atomic: AtomicWriter
    uri: UriIO


#: The library's own logger root: every logger the facade hands out sits under it, and nothing else is touched.
_LOGGER_NAME = "configkit"

_FORMAT = "%(levelname)s %(name)s: %(message)s"

_LOCK = threading.Lock()

#: The handler the last ``configure`` installed, so a second call replaces it instead of stacking a second one.
_HANDLER: logging.Handler | None = None

#: The keys ``once`` has seen, for the process's lifetime.
_SEEN: set[str] = set()


class _SinkHandler(logging.Handler):
    """The one handler ``configure`` installs: format the record, dedup it by key, hand the line to the sink."""

    def __init__(self, sink: Sink, *, dedup_key: Callable[[Any], str] | None) -> None:
        super().__init__()
        self._sink = sink
        self._dedup_key = dedup_key

    def emit(self, record: logging.LogRecord) -> None:
        """One record as one sink call; a sink that raises is reported by the logging module, never to the caller."""
        try:
            if self._dedup_key is not None and not once(self._dedup_key(record)):
                return
            self._sink(self.format(record))
        except Exception:  # a log line must never break the call it describes
            self.handleError(record)


def logger(name: str) -> logging.Logger:
    """The logger for *name*, under the library's ``configkit`` logger (the prefix is added once).

    Args:
        name: A module's ``__name__`` or a subsystem name.

    Returns:
        The logger. No handler is installed by this call.
    """
    if name == _LOGGER_NAME or name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def configure(sink: Sink, *, level: str = "INFO", dedup_key: Callable[[Any], str] | None = None) -> None:
    """Send the library's records to *sink*, replacing the handler a previous ``configure`` installed.

    No handler is installed at import; this call is the only place the library touches logging, and it never
    touches the root logger or another library's loggers.

    Args:
        sink: Receives each formatted line.
        level: The minimum level, by name (``"DEBUG"``, ``"INFO"``, ...).
        dedup_key: When given, a record whose ``dedup_key(record)`` was already emitted is dropped.
    """
    global _HANDLER
    with _LOCK:
        package = logging.getLogger(_LOGGER_NAME)
        if _HANDLER is not None:
            package.removeHandler(_HANDLER)
        handler = _SinkHandler(sink, dedup_key=dedup_key)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.setLevel(level)
        package.addHandler(handler)
        package.setLevel(level)
        _HANDLER = handler


def once(key: str) -> bool:
    """True the first time *key* is seen in this process, False every later time.

    The primitive behind ``configure(dedup_key=...)``: a caller emits a once-per-process notice without holding
    the state itself.

    Args:
        key: The dedup key (a code, a path, a message template's identity).

    Returns:
        Whether *key* is new.
    """
    with _LOCK:
        if key in _SEEN:
            return False
        _SEEN.add(key)
        return True
