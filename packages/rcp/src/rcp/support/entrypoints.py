"""The one entry-point registry: the names in a group, and the refusal of an ambiguous one.

Every plugin seam of the product resolves its names through here -- the dataset readers and writers
(``rcp.readers``, ``rcp.writers``), the result sinks (``rcp.results``), the job runners
(``rcp.runners``), the scoring metrics (``rcp.metrics``) and the run-event subscribers
(``rcp.events``) -- so one name has one lookup and one duplicate-name policy.  The caller fetches its
own group (one ``entry_points`` call, its group constant beside the seam it serves) and passes the entries
in; this module owns what happens to them.

The policy is :mod:`configkit.plugins`'s and was this module's: a name **selected** that more than one
installed distribution provides is refused, naming the providers.  The alternative -- a warning and the last one
wins -- let a plugin publishing a built-in's name (``slurm``, ``beir``) replace it silently, which is how a wrong
reader or runner gets used without anyone seeing a message.  A listing (:func:`registered_names`) still names the
group's names once each: the ambiguity surfaces when the ambiguous name is actually chosen.  The library's
:func:`~configkit.plugins.lookup` also **loads** the chosen provider once, so a broken plugin is refused there
rather than at the caller's first use.

What stays here is the wording and the error type: a library refusal is this module's :class:`ConfigError` (exit
3, not the library's generic ``value_error``/``runtime_error``), with the message a caller of this package has
always read.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import EntryPoint
from typing import cast

from configkit import plugins
from configkit.faults import Fault, RuntimeErrorFault

from rcp.errors import ConfigError

__all__ = ["load_entry_point", "load_target", "provider_of", "registered_names"]


def registered_names(entries: Iterable[EntryPoint], group: str) -> tuple[str, ...]:
    """The names registered in *group*, sorted, each once (a duplicate name is not listed twice)."""
    del group  # the listing needs no group name; kept for the call sites that pass their own constant
    return plugins.registered_names(entries)


def provider_of(entries: Iterable[EntryPoint], group: str, name: str, *, kind: str) -> EntryPoint:
    """The entry point that provides *name* in *group*, loaded once by the lookup.

    Args:
        entries: The group's entry points (the caller's own ``entry_points`` result).
        group: The group's name, for the messages.
        name: The name to resolve.
        kind: What the name selects (``"dataset format"``, ``"result sink"``, ``"runner"``), for the message.

    Returns:
        The entry point. Its ``value`` is the plugin target; loading it is :func:`load_entry_point`'s job (or
        the caller's, when it constructs rather than instantiates a class).

    Raises:
        ConfigError: *name* is registered by more than one installed distribution (or twice by one). Two
            entry points from the same distribution and target are one provider, not a conflict. Also when the
            provider does not load, or when no installed distribution provides *name*.
    """
    try:
        return cast("EntryPoint", plugins.lookup(group, name, entries, kind=kind))
    except RuntimeErrorFault as exc:
        # The library's load check: name the entry point and the plugin's own failure in this module's wording,
        # so a caller reads one sentence whichever of the two packages refused it.
        failure = exc.__cause__
        raise ConfigError(
            f"the {name!r} entry point of {group!r} could not be loaded: {type(failure).__name__}: {failure}",
            hint=exc.hint,
            details=exc.details,
            cli_hint=exc.cli_hint,
        ) from failure
    except Fault as exc:
        raise ConfigError(exc.message, hint=exc.hint, details=exc.details, cli_hint=exc.cli_hint) from exc


def load_target(entry_point: EntryPoint, *, kind: str) -> object:
    """Load *entry_point*'s target, wrapping any import failure in a ConfigError naming the plugin.

    The load-only half of :func:`load_entry_point`, for a seam whose plugin is checked structurally (a job
    runner is a :class:`~rcp.runners.base.JobRunner` Protocol, which ``issubclass`` cannot check): the
    caller gets the loaded object and does its own check.

    Raises:
        ConfigError: The entry point does not import (the plugin's own failure, named).
    """
    del kind  # the messages name the entry point and its group, which the entry point itself carries
    try:
        return entry_point.load()
    except Exception as exc:  # noqa: BLE001 - any plugin failure is the plugin's, and is named
        raise ConfigError(
            f"the {entry_point.name!r} entry point of {entry_point.group!r} could not be loaded: "
            f"{type(exc).__name__}: {exc}",
            hint="fix or uninstall the package that declares it",
        ) from exc


def load_entry_point[T](entry_point: EntryPoint, base: type[T], *, kind: str) -> type[T]:
    """The class *entry_point* names, checked against *base*.

    Raises:
        ConfigError: The entry point does not import (the plugin's own failure, named), or what it names is
            not a subclass of *base* (the one contract every plugin of that kind must meet).
    """
    loaded = load_target(entry_point, kind=kind)
    if not (isinstance(loaded, type) and issubclass(loaded, base)):
        raise ConfigError(
            f"the {entry_point.name!r} entry point of {entry_point.group!r} names {loaded!r}, not a {base.__name__}"
        )
    return loaded
