"""One refusal base, one data-driven code table, one exit-code seam, one warning channel.

Every deliberate refusal in the library is a :class:`Fault`. It carries a stable ``code`` (a string a front end
maps to a process exit number), a ``message`` for a person, JSON-serialisable ``details``, and an optional
``hint``/``cli_hint`` the front end resolves at output time. The library bakes no exit number and no install
line: the numbers are the consumer's (:func:`exit_codes`) and the install line is the caller's
(:func:`require`).

:data:`FAULT_CODES` holds the generic rows only -- the four exceptions the library itself can leak, with the code
each maps to. A consumer adds its own rows through :func:`register_fault_codes`, which *returns* the merged
table: it registers nothing in module state, so two front ends in one process cannot overwrite each other's codes
and a test can build its own table.

The warning channel is keyed by a closed set the **record** declares, as ``WARNING_CODES`` on the model and
merged down the MRO exactly as ``IDENTITY_ROLES`` is: a warning code is the record's word, and :func:`warn`
refuses one the record did not declare.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType, ModuleType
from typing import Any, Final

from pydantic import BaseModel

from configkit.ports import ExitCodeTable

__all__ = [
    "FAULT_CODES",
    "WARNING_CODES",
    "Fault",
    "classify",
    "exit_codes",
    "register_fault_codes",
    "require",
    "warn",
]

#: The class attribute a record declares its closed warning-code set on, merged down the MRO like
#: ``IDENTITY_ROLES``. The name lives here so the library, a consumer and ``audit`` spell it once.
WARNING_CODES: Final[str] = "WARNING_CODES"


class Fault(Exception):
    """Every deliberate refusal in the library; a caller catches this once.

    The class is the raise's shape, the ``code`` is its taxonomy. A subclass may fix its code as a class
    attribute (see the four generic ones below); a direct :class:`Fault` names the code at the raise, because
    the code belongs to the call site that knows what went wrong, never to a catch block (emb-train 12).

    Attributes:
        message: What went wrong, for a person.
        code: The stable code, from :data:`FAULT_CODES` or a consumer's registered table.
        details: JSON-serialisable facts a program can act on (paths, counts, differing fields).
        hint: The next step, worded for a Python caller; resolved at output time, never baked by the library.
        cli_hint: The same next step worded for the command line, when it differs from ``hint``.
    """

    message: str
    code: str
    details: dict[str, Any]
    hint: str | None
    cli_hint: str | None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        hint: str | None = None,
        cli_hint: str | None = None,
    ) -> None:
        resolved = code if code is not None else getattr(type(self), "code", "")
        if not resolved:
            raise TypeError(f"{type(self).__name__} carries no code: pass code=... or declare one on the class")
        super().__init__(message)
        self.message = message
        self.code = resolved
        self.details = dict(details or {})
        self.hint = hint
        self.cli_hint = cli_hint


class OSErrorFault(Fault):
    """The host's filesystem or OS refused: a missing file, a permission, a full disk (the ``os_error`` code)."""

    code = "os_error"


class RuntimeErrorFault(Fault):
    """An unexpected failure -- the internal bucket for a bug (the ``runtime_error`` code)."""

    code = "runtime_error"


class ValueErrorFault(Fault):
    """A value does not fit: a parse, a range, a declaration (the ``value_error`` code)."""

    code = "value_error"


class OptionalDependencyMissing(Fault):
    """An optional dependency is absent; ``hint`` is the caller's install line (the ``optional.missing`` code)."""

    code = "optional.missing"


#: The generic rows: the exceptions the library itself can leak, and the stable code each maps to. The rows are
#: disjoint, so their order decides nothing; a consumer's narrower rows go in front of them in
#: :func:`register_fault_codes`'s table. A consumer's rows are its own exception classes, never a library class.
FAULT_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (OSError, "os_error"),
    (RuntimeError, "runtime_error"),
    (ValueError, "value_error"),
    (ModuleNotFoundError, "optional.missing"),
)


def register_fault_codes(
    rows: Iterable[tuple[type[BaseException], str]],
) -> tuple[tuple[type[BaseException], str], ...]:
    """The consumer's rows followed by the library's generic ones, refusing a code declared twice.

    The call *returns* the table; it registers nothing in module state, so two consumers in one process cannot
    overwrite each other's codes and a test can build its own. Order is the mapper's rule: the first row whose
    class matches an exception decides its code, so the consumer's narrow classes come before the generic
    builtins.

    Args:
        rows: The consumer's rows, narrowest first: an exception class and the stable code it reports.

    Returns:
        The consumer's rows, then :data:`FAULT_CODES`.

    Raises:
        ValueError: a code appears twice (in the consumer's rows or in a generic row), or is empty.
    """
    table = tuple(rows) + FAULT_CODES
    seen: set[str] = set()
    duplicates: list[str] = []
    for _, code in table:
        if not code:
            raise ValueError("a fault code must be a non-empty string")
        if code in seen and code not in duplicates:
            duplicates.append(code)
        seen.add(code)
    if duplicates:
        raise ValueError(f"fault code(s) declared twice: {', '.join(duplicates)}; a code names one fault")
    return table


def classify(exc: BaseException, *, mapper: Callable[[BaseException], Fault]) -> Fault:
    """The :class:`Fault` for an arbitrary exception: a deliberate fault passes through, anything else is mapped.

    Args:
        exc: The exception to classify.
        mapper: The consumer's mapping from a foreign exception to a :class:`Fault`; its rows come from
            :func:`register_fault_codes` and its codes from the table the front end reports.

    Returns:
        *exc* itself when it is already a :class:`Fault`, else ``mapper(exc)``.
    """
    if isinstance(exc, Fault):
        return exc
    return mapper(exc)


def exit_codes(rows: Mapping[str, int]) -> ExitCodeTable:
    """The consumer's code -> process-exit-code table, validated and copied.

    The library ships no exit number: the consumer's numbers are the only ones a process can return, and this
    call is where they become the library's :class:`~configkit.ports.ExitCodeTable`. A value that is not an
    ``int`` is refused here rather than reaching a process exit.

    Args:
        rows: The consumer's table: a stable fault code and the process exit number it reports.

    Returns:
        A new read-only mapping; the caller's ``rows`` is not kept.

    Raises:
        ValueError: a code is not a non-empty string, or a value is not an ``int``.
    """
    checked: dict[str, int] = {}
    for code, number in rows.items():
        if not isinstance(code, str) or not code:
            raise ValueError(f"an exit-code table is keyed by a non-empty fault code, not {code!r}")
        if not isinstance(number, int) or isinstance(number, bool):
            raise ValueError(f"the exit code of {code!r} is not an int: {number!r}")
        checked[code] = number
    return MappingProxyType(checked)


def require(module: str, *, hint: str, extra: str | None = None) -> ModuleType:
    """Import *module*, or raise the ``optional.missing`` fault carrying the caller's install line.

    The install line is a parameter because the same module belongs to a different extra in each consumer, and
    the library bakes no distribution name (the line is wrong after the move, otherwise).

    Args:
        module: The import name to load (``"yaml"``).
        hint: The caller's install line, verbatim.
        extra: The consumer's extra name, recorded in ``details`` when given.

    Returns:
        The imported module.

    Raises:
        Fault: the module is absent (or a ``sys.modules`` entry of ``None`` blocks it). ``code`` is
            ``"optional.missing"`` and ``hint`` is the caller's line. A missing *nested* dependency of an
            installed module is re-raised: that is a broken install, not an absent extra.
    """
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name != module:
            raise
        raise OptionalDependencyMissing(
            f"{module} is not installed; {hint}",
            details={"module": module, "extra": extra},
            hint=hint,
        ) from exc


class _Warning(UserWarning):
    """The record one ``warn`` call raises: the code, the record's text and the fields the caller passed."""

    def __init__(self, code: str, message: str, fields: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields: dict[str, Any] = dict(fields)


def _declared_warning_codes(record: BaseModel) -> tuple[str, ...]:
    """The warning codes *record*'s class declares, merged down the MRO (most derived declaration wins)."""
    merged: list[str] = []
    for klass in reversed(type(record).__mro__):
        declared = klass.__dict__.get(WARNING_CODES)
        if declared is None:
            continue
        merged.extend(str(code) for code in declared)
    return tuple(dict.fromkeys(merged))


def warn(record: BaseModel, code: str, **fields: Any) -> None:
    """Emit one warning for *record* under *code*, the record's own closed code set deciding what is allowed.

    The channel is the standard :mod:`warnings` one; the library installs no handler and prints nothing.

    Args:
        record: The validated record that carries the warning channel. Its class declares ``WARNING_CODES``
            (merged down the MRO, like ``IDENTITY_ROLES``); a record with no declaration accepts no code.
        code: One of the record's declared codes.
        **fields: The warning's facts, merged over the record's own dump; JSON-serialisable.

    Raises:
        ValueError: the record declares no ``WARNING_CODES``, or *code* is not one of them.
    """
    declared = _declared_warning_codes(record)
    if not declared:
        raise ValueError(
            f"{type(record).__name__} carries a warning channel but declares no {WARNING_CODES}; "
            "declare the closed set of codes it may emit"
        )
    if code not in declared:
        raise ValueError(f"unknown warning code {code!r} for {type(record).__name__}; known: {', '.join(declared)}")
    warnings.warn(_Warning(code, str(record), {**record.model_dump(), **fields}), stacklevel=2)
