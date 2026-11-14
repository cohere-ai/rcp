"""One plugin lookup, one duplicate-name policy, one load check, and a door that refuses at declaration.

A plugin seam has two halves and this module owns both. The **lookup** answers "which installed entry point
provides this name?", and it takes the entry points as a parameter: the caller fetches its own group (one
``entry_points`` call, its group constant beside the seam it serves) and passes the records in, so the library
holds no registry, no cache and no entry-point group of its own -- a test builds its own table, two consumers in
one process cannot overwrite each other's plugins, and the library reads no group it cannot resolve.

The policy is ``rcp.support.entrypoints``'s, kept whole: a name **selected** that more than one installed
distribution provides is refused, naming the providers, because the alternative -- a warning and the last one wins
-- lets a plugin publishing a built-in's name replace it silently. Two entry points that agree on distribution and
target are one provider, not a conflict, and a listing still names each name once: the ambiguity surfaces when the
ambiguous name is chosen.

The **door** is :func:`register`, and it refuses *at declaration* (emb-eval M36): a row whose target does not
import, or whose name is already taken, is refused when it is registered, not at the registry's first
``__getitem__``. That is the difference between a bootstrap failure that names the document line and a failure in
the middle of a run, far from the wrong declaration. :func:`register_from` reads the rows a document declares
(``register: list[Registration]``, emb-train 20), so a plugin is reachable without Python, and a refusal names the
field and the row's index.

:func:`validation_hooks` is the third seam (rcp-21): a registry may declare hooks that run **after** the registry
resolved, so a validation pass can see a third-party plugin that a config file cannot. The library runs no hook
itself -- it returns them, and the caller's bootstrap decides when the registry is complete.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from configkit.faults import RuntimeErrorFault, ValueErrorFault

__all__ = [
    "Provider",
    "Registry",
    "Registration",
    "ValidationHook",
    "lookup",
    "registered_names",
    "register",
    "register_from",
    "validation_hooks",
]

#: The declared field a document carries its registrations in: ``models.register`` is ``list[Registration]``
#: (emb-train 20). The name lives here so :func:`register_from` and its refusal spell it once.
_REGISTER_FIELD = "register"


class Provider(Protocol):
    """One entry-point record: the three members a lookup reads, and no other.

    ``importlib.metadata.EntryPoint`` satisfies it, and so does a consumer's own record or a test's stub -- which
    is what taking the entries as a parameter buys. A provider is *not* loaded by the lookup's caller: the lookup
    performs the load check once, so a broken plugin is refused there rather than at the seam's first use.
    """

    #: The name the plugin is registered under, the name a config file or a flag selects.
    name: str
    #: The target as declared (``"my_package.metrics:MeanAveragePrecision"``), for a message.
    value: str

    def load(self) -> Any:
        """The plugin itself: import its module and resolve its attribute."""
        ...


class ValidationHook(Protocol):
    """One validation pass over a **resolved** registry (rcp-21).

    The hook receives the registry after every plugin is registered, so it can refuse or record a plugin that a
    config file cannot see -- the third-party adapter ``rcp.inference.config`` cannot reach today. It raises the
    consumer's own fault to refuse; the library runs no hook and catches nothing here.
    """

    def __call__(self, registry: Mapping[str, Any], /) -> None:
        """Check *registry*, raising the caller's fault when it refuses."""
        ...


@dataclass(frozen=True)
class Registration:
    """One declared plugin: the name it is selected by and the ``module:attribute`` target it loads.

    The target grammar is the entry-point one, so a document row and an installed entry point name the same thing
    the same way. A blank name or a target that is not ``module:attribute`` is refused at construction, because a
    row that cannot be loaded is a declaration nobody can act on.

    Attributes:
        name: The name the plugin is registered under.
        target: ``"<module>:<attribute>"`` -- the import name and the attribute the plugin is found at.
    """

    name: str
    target: str

    def __post_init__(self) -> None:
        """Refuse a blank name or a target that is not ``module:attribute``.

        Raises:
            ValueError: the name is blank, or the target has no ``:`` or an empty half.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"a Registration needs a non-empty name, got {self.name!r}")
        module, separator, attribute = self.target.partition(":") if isinstance(self.target, str) else ("", "", "")
        if not separator or not module.strip() or not attribute.strip():
            raise ValueError(
                f"{self.name}: a target is '<module>:<attribute>', got {self.target!r}; "
                "the colon is what separates the import name from the attribute"
            )


class Registry(MutableMapping[str, Any]):
    """A name -> plugin mapping with one duplicate-name policy and the hooks a bootstrap runs.

    The mapping is the resolved plugins, so a consumer's ``registry[name]`` is the one lookup. Writing a name that
    is already taken is refused -- the door's half of the policy -- and the hooks are a separate list, because a
    hook is not a plugin and a plugin name cannot address one.
    """

    def __init__(
        self,
        entries: Mapping[str, Any] | None = None,
        *,
        hooks: Iterable[ValidationHook] = (),
    ) -> None:
        """A registry holding *entries* and declaring *hooks*.

        Args:
            entries: The plugins already resolved, by name.
            hooks: The validation passes, in the order they should run.
        """
        self._plugins: dict[str, Any] = {}
        for name, plugin in (entries or {}).items():
            self[name] = plugin
        self._hooks: list[ValidationHook] = list(hooks)

    def __getitem__(self, name: str) -> Any:
        """The plugin registered under *name*.

        Raises:
            KeyError: no plugin is registered under *name*.
        """
        return self._plugins[name]

    def __setitem__(self, name: str, plugin: Any) -> None:
        """Register *plugin* under *name*, refusing a name that is already taken.

        Raises:
            Fault: *name* is registered already. ``code`` is ``"value_error"`` and ``details`` names the name.
        """
        if name in self._plugins:
            raise ValueErrorFault(
                f"the plugin name {name!r} is registered twice; one name names one plugin",
                details={"name": name},
                hint="rename one of the two registrations, or drop the second",
            )
        self._plugins[name] = plugin

    def __delitem__(self, name: str) -> None:
        """Remove the plugin registered under *name*.

        Raises:
            KeyError: no plugin is registered under *name*.
        """
        del self._plugins[name]

    def __iter__(self) -> Iterator[str]:
        """The registered names, in registration order."""
        return iter(self._plugins)

    def __len__(self) -> int:
        """How many plugins are registered."""
        return len(self._plugins)

    @property
    def hooks(self) -> tuple[ValidationHook, ...]:
        """The declared validation hooks, in declaration order."""
        return tuple(self._hooks)

    def add_hook(self, hook: ValidationHook) -> None:
        """Declare *hook*, to run after the registry resolves.

        Args:
            hook: The validation pass; it receives the resolved registry.
        """
        if not callable(hook):
            raise ValueErrorFault(f"a validation hook must be callable, got {hook!r}")
        self._hooks.append(hook)


def lookup(group: str, name: str, entries: Iterable[Provider]) -> Provider:
    """The provider of *name* in *group*, checked for ambiguity and for loading.

    Args:
        group: The entry-point group's name, for the messages (``"rcp.metrics"``).
        name: The name to resolve (the entry-point name a flag or a document selects).
        entries: The group's entry points, as the caller fetched them. This is the whole registry: the module
            holds none.

    Returns:
        The provider record. Its ``load()`` has already been exercised once by this call, so the caller's own
        ``load()`` cannot be the first to discover a broken plugin.

    Raises:
        Fault: more than one installed distribution provides *name* (``details["providers"]`` names them), no
            provider does (``details["group"]`` names the group), or the provider does not load
            (``details["provider"]`` names it and the message carries the plugin's own failure). ``code`` is
            ``"value_error"`` for the two lookups and ``"runtime_error"`` for the load.
    """
    candidates = [entry for entry in entries if entry.name == name]
    providers = sorted({_origin(entry) for entry in candidates})
    if len(providers) > 1:
        where = ", ".join(f"{distribution} ({value})" for distribution, value in providers)
        raise ValueErrorFault(
            f"the plugin name {name!r} is provided by more than one installed distribution: {where}",
            details={"name": name, "group": group, "providers": [value for _, value in providers]},
            hint=f"uninstall all but one of them, or rename one entry point in the {group!r} group",
        )
    if not candidates:
        raise ValueErrorFault(
            f"no plugin named {name!r} is installed in the {group!r} entry-point group",
            details={"name": name, "group": group},
            hint="install the package that provides it, or drop the name",
        )
    chosen = candidates[0]
    try:
        chosen.load()
    except Exception as exc:  # any plugin failure is the plugin's own, and is named rather than wrapped twice
        raise RuntimeErrorFault(
            f"the {name!r} plugin of the {group!r} entry-point group could not be loaded: {type(exc).__name__}: {exc}",
            details={"name": name, "group": group, "provider": chosen.value},
            hint="fix or uninstall the package that declares it",
        ) from exc
    return chosen


def registered_names(group: str, entries: Iterable[Provider]) -> tuple[str, ...]:
    """The names registered in *group*, sorted, each once.

    Args:
        group: The group's name. A listing needs it for nothing; the parameter keeps the call symmetric with
            :func:`lookup`, where it names the group in a refusal.
        entries: The group's entry points.

    Returns:
        The names, sorted; a name two providers declare appears once.
    """
    del group  # a listing names the group's names; only a refusal needs the group's name
    return tuple(sorted({entry.name for entry in entries}))


def register(registry: Registry, row: Registration) -> None:
    """Load *row*'s target and register it under the row's name, refusing before the registry is used.

    Args:
        registry: The registry the plugin joins.
        row: The declared plugin: its name and its ``module:attribute`` target.

    Raises:
        Fault: the target's module does not import, the module has no such attribute, or the name is taken.
            ``details`` names the row's name and target; ``code`` is ``"value_error"``.
    """
    module_name, _, attribute = row.target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # the target's own failure, named with the row that declared it
        raise ValueErrorFault(
            f"the plugin {row.name!r} names {row.target!r}, which does not import: {type(exc).__name__}: {exc}",
            details={"name": row.name, "target": row.target},
            hint="fix the target's module name, or uninstall the package that declares the row",
        ) from exc
    try:
        plugin = getattr(module, attribute)
    except AttributeError as exc:
        raise ValueErrorFault(
            f"the plugin {row.name!r} names {row.target!r}, and {module_name!r} has no {attribute!r}",
            details={"name": row.name, "target": row.target},
            hint="fix the target's attribute name",
        ) from exc
    registry[row.name] = plugin


def register_from(spec: Mapping[str, Any], *, registry: Registry) -> None:
    """Register every row the declared field ``register`` holds, in declaration order (emb-train 20).

    A document declares ``register: list[Registration]``, so a plugin is selected without Python; the refusal names
    the field and, for a malformed row, its index, so the document is fixable from the message.

    Args:
        spec: The mapping the field lives in -- the block a document declares (``models``), or any mapping with a
            ``register`` key.
        registry: The registry the rows join.

    Raises:
        Fault: the field is absent (``details["field"]``), a row is not a name and a target
            (``details["index"]``), or a row's target does not import or its name is taken (see :func:`register`).
            ``code`` is ``"value_error"``.
    """
    rows = spec.get(_REGISTER_FIELD)
    if rows is None:
        raise ValueErrorFault(
            f"the declared field {_REGISTER_FIELD!r} is absent: a document registers its plugins as "
            f"'{_REGISTER_FIELD}: [{{name: ..., target: ...}}]'",
            details={"field": _REGISTER_FIELD, "known": sorted(spec)},
            hint=f"add a {_REGISTER_FIELD!r} list to the block, or drop the registration call",
        )
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ValueErrorFault(
            f"{_REGISTER_FIELD} must be a list of {{name, target}} rows, got {type(rows).__name__}",
            details={"field": _REGISTER_FIELD},
        )
    for index, row in enumerate(rows):
        register(registry, _row(row, index=index))


def validation_hooks(store: Registry) -> tuple[ValidationHook, ...]:
    """The validation hooks *store* declares, in declaration order (rcp-21).

    The caller runs them once the registry is complete; the library runs none, so no import and no construction
    pays for a check that is not asked for.

    Args:
        store: The registry the hooks were declared on.

    Returns:
        The hooks, in declaration order; empty when the registry declares none.
    """
    return store.hooks


def _row(row: Any, *, index: int) -> Registration:
    """The :class:`Registration` *row* declares, refused with its index when it declares no name and target."""
    if isinstance(row, Registration):
        return row
    if isinstance(row, Mapping) and "name" in row and "target" in row:
        try:
            return Registration(name=str(row["name"]), target=str(row["target"]))
        except ValueError as exc:
            raise ValueErrorFault(
                f"{_REGISTER_FIELD}[{index}] is malformed: {exc}",
                details={"field": _REGISTER_FIELD, "index": index},
            ) from exc
    raise ValueErrorFault(
        f"{_REGISTER_FIELD}[{index}] is not a plugin row: expected {{name: ..., target: ...}}, got {row!r}",
        details={"field": _REGISTER_FIELD, "index": index},
        hint="write each row as 'name: <the name to select>; target: <module>:<attribute>'",
    )


def _origin(entry: Provider) -> tuple[str, str]:
    """``(distribution, target)`` an entry point comes from; a distribution-less one is named ``<unknown>``."""
    distribution = getattr(getattr(entry, "dist", None), "name", None)
    return (distribution or "<unknown>", entry.value)
