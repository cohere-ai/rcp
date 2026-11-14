"""The host services the library names and never builds, and the log facade.

One protocol per call, so a consumer injects its own implementation and the library holds no graph: a clock, a
step or wall-clock budget, a log sink, an exit-code table, a result renderer, an atomic writer and a URI
reader/writer. ``Ports`` is the one frozen record that carries them (rcp-3), and it names **every** service a
library module calls -- ``storage`` takes ``Ports.atomic`` and ``Ports.uri``, never a global.

``logger``/``configure``/``once`` are the log facade (rcp-35): the library creates loggers and installs no
handler until ``configure`` is called, so importing it changes nothing about the host's logging, and dedup is a
parameter rather than a policy. The facade's state is a :class:`LogState` the record carries, so two records
never share a handler or a dedup key (M2), and its root is the consumer's :class:`~configkit.spec.Brand` name,
so the library writes under no other program's root (M3).

``Tensor`` is typing-only: it names the array shape the numeric fits read, and this module imports no engine
(emb-train 29).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from configkit.spec import Brand

__all__ = [
    "AtomicWriter",
    "Budget",
    "Clock",
    "ExitCodeTable",
    "LogState",
    "Ports",
    "Renderer",
    "Sink",
    "Tensor",
    "UriIO",
    "configure",
    "logger",
    "once",
]


#: The brand the facade writes under when the consumer passes none: the library's own name, never another's.
_LIBRARY_BRAND = Brand(
    name="configkit", prefix="CONFIGKIT", schema_root="configkit.v1", output_schema_prefix="configkit.run"
)

#: The one line format ``configure`` installs: level, logger name, message.
_FORMAT = "%(levelname)s %(name)s: %(message)s"


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


@dataclass
class LogState:
    """One record's log facade state: the brand, the handler it installed, the dedup keys and the lock.

    ``Ports`` carries one per record, so no two records share a handler or a dedup key. The logging module's
    logger for a root is process-wide -- that is the logging module's contract, not the facade's state -- so what
    this record owns is the handler it installed and the keys it has seen.

    Attributes:
        brand: The consumer's :class:`~configkit.spec.Brand`; its ``name`` is the log root, so the library writes
            under ``configkit`` unless a consumer passes its own brand.
    """

    brand: Brand = _LIBRARY_BRAND
    _handler: logging.Handler | None = field(default=None, init=False, repr=False)
    _seen: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def root(self) -> str:
        """The root every logger the facade hands out sits under: the brand's program name."""
        return self.brand.name


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
        log: The log facade's state: one per record, whose root is the consumer's brand name.
    """

    clock: Clock
    budget: Budget
    sink: Sink
    exit_codes: ExitCodeTable
    renderer: Renderer
    atomic: AtomicWriter
    uri: UriIO
    log: LogState = field(default_factory=LogState)


class _SinkHandler(logging.Handler):
    """The one handler ``configure`` installs: format the record, dedup it by key, hand the line to the sink."""

    def __init__(self, state: LogState, sink: Sink, *, dedup_key: Callable[[Any], str] | None) -> None:
        super().__init__()
        self._state = state
        self._sink = sink
        self._dedup_key = dedup_key

    def emit(self, record: logging.LogRecord) -> None:
        """One record as one sink call; a sink that raises is reported by the logging module, never to the caller."""
        try:
            if self._dedup_key is not None and not once(self._state, self._dedup_key(record)):
                return
            self._sink(self.format(record))
        except Exception:  # a log line must never break the call it describes
            self.handleError(record)


def logger(state: LogState, name: str) -> logging.Logger:
    """The logger for *name*, under *state*'s root (the prefix is added once).

    Args:
        state: The record's log state; its root is the one every logger sits under.
        name: A module's ``__name__`` or a subsystem name.

    Returns:
        The logger. No handler is installed by this call.
    """
    root = state.root
    if name == root or name.startswith(f"{root}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{root}.{name}")


def configure(
    state: LogState, sink: Sink, *, level: str = "INFO", dedup_key: Callable[[Any], str] | None = None
) -> None:
    """Send the library's records to *sink*, replacing the handler this *state* installed before.

    No handler is installed at import; this call is the only place the library touches logging, and it never
    touches the root logger or another library's loggers. The handler it installs is remembered on *state*, so a
    second record's ``configure`` removes nothing of this one's.

    Args:
        state: The record's log state: the root it writes under, and the handler this call replaces.
        sink: Receives each formatted line.
        level: The minimum level, by name (``"DEBUG"``, ``"INFO"``, ...).
        dedup_key: When given, a record whose ``dedup_key(record)`` was already emitted is dropped.
    """
    with state._lock:
        package = logging.getLogger(state.root)
        if state._handler is not None:
            package.removeHandler(state._handler)
        handler = _SinkHandler(state, sink, dedup_key=dedup_key)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.setLevel(level)
        package.addHandler(handler)
        package.setLevel(level)
        state._handler = handler


def once(state: LogState, key: str) -> bool:
    """True the first time *key* is seen in *state*, False every later time in that state.

    The primitive behind ``configure(dedup_key=...)``: a caller emits a once-per-record notice without holding
    the state itself.

    Args:
        state: The record's log state, which holds the keys already seen.
        key: The dedup key (a code, a path, a message template's identity).

    Returns:
        Whether *key* is new to *state*.
    """
    with state._lock:
        if key in state._seen:
            return False
        state._seen.add(key)
        return True
