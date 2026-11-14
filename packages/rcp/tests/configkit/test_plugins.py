"""The one plugin lookup, the registration door that refuses at declaration, and the validation hook.

The policy the module ships is ``rcp.support.entrypoints``'s: a name that more than one installed distribution
provides is refused, naming the providers, and the group's names list each once. The door refuses **at
declaration** (emb-eval M36): a plugin whose target does not import is refused when it is registered, not at the
registry's first ``__getitem__``, which is where the product's check sits today and therefore far from the wrong
declaration.

F1 is the other half: ``lookup`` takes its entry points as a parameter and the module holds no registry, so a test
can build its own and two consumers in one process cannot overwrite each other's plugins. The stubs below are what
that parameter buys: an entry point is any record with ``name``, ``value`` and ``load()``, which is exactly the
shape ``importlib.metadata.EntryPoint`` has.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from configkit.faults import Fault
from configkit.plugins import (
    Provider,
    Registration,
    Registry,
    lookup,
    register,
    register_from,
    registered_names,
    validation_hooks,
)


@dataclass(frozen=True)
class _Dist:
    """The distribution half of an entry point's origin."""

    name: str


@dataclass(frozen=True)
class _Entry:
    """A stub entry point: the three members :class:`~configkit.plugins.Provider` names, and nothing else."""

    name: str
    value: str
    group: str = "acme.plugins"
    loaded: Any = "the-plugin"
    broken: str = ""

    def load(self) -> Any:
        """The plugin, or the failure the ``broken`` entry declares."""
        if self.broken:
            raise ImportError(self.broken)
        return self.loaded

    @property
    def dist(self) -> Any:
        """The distribution a real entry point carries, named after the target's module so two differ."""
        return _Dist(self.value.partition(":")[0])


def _registry(*names: str) -> Registry:
    """A registry holding one object per name."""
    return Registry({name: f"plugin:{name}" for name in names})


# ---------------------------------------------------------------------------------------------------------------
# lookup: one duplicate-name policy and one load check, over the entries the caller passes
# ---------------------------------------------------------------------------------------------------------------


def test_lookup_takes_its_entries_as_a_parameter() -> None:
    """F1: the entry points are the call's argument, so two callers in one process never share a lookup."""
    alpha = _Entry("alpha", "acme.plugins.alpha:Plugin")
    beta = _Entry("beta", "acme.plugins.beta:Plugin")
    assert lookup("acme.plugins", "alpha", [alpha, beta]) is alpha
    assert lookup("acme.plugins", "beta", [alpha, beta]) is beta


def test_no_module_global_registry() -> None:
    """F1: the module holds no registry, no entry-point list and no cache a second call could see."""
    import configkit.plugins as module

    held = {
        name: value
        for name, value in vars(module).items()
        if isinstance(value, (dict, list)) and not name.startswith("__")
    }
    assert held == {}, held


def test_two_providers_for_one_name_are_refused_naming_both() -> None:
    """The duplicate-name policy: two distributions providing one name is an error, not a last-one-wins."""
    first = _Entry("alpha", "acme.plugins.alpha:Plugin")
    second = _Entry("alpha", "other.plugins.alpha:Plugin")
    with pytest.raises(Fault) as raised:
        lookup("acme.plugins", "alpha", [first, second])
    message = raised.value.message
    assert "acme.plugins.alpha:Plugin" in message and "other.plugins.alpha:Plugin" in message
    assert raised.value.code == "value_error"


def test_two_entry_points_of_one_target_are_one_provider() -> None:
    """A distribution that declares the same name and target twice is not a conflict."""
    once = _Entry("alpha", "acme.plugins.alpha:Plugin")
    twice = _Entry("alpha", "acme.plugins.alpha:Plugin")
    assert lookup("acme.plugins", "alpha", [once, twice]) is once


def test_an_unknown_name_is_refused_naming_the_group() -> None:
    """A name nobody provides names the group to look in, so the message says where to install it."""
    with pytest.raises(Fault) as raised:
        lookup("acme.plugins", "gamma", [_Entry("alpha", "acme.plugins.alpha:Plugin")])
    assert "gamma" in raised.value.message and "acme.plugins" in raised.value.message


def test_a_provider_that_does_not_load_is_refused_naming_the_plugin() -> None:
    """The load check: a broken plugin is refused at the lookup, naming the plugin and its own failure."""
    with pytest.raises(Fault) as raised:
        lookup("acme.plugins", "alpha", [_Entry("alpha", "acme.plugins.alpha:Plugin", broken="no module named acme")])
    assert "alpha" in raised.value.message and "no module named acme" in raised.value.message
    assert raised.value.code == "runtime_error"


def test_registered_names_lists_each_name_once_sorted() -> None:
    """A listing names the group's names once each: the ambiguity surfaces when a name is chosen."""
    entries = [
        _Entry("beta", "acme.plugins.beta:Plugin"),
        _Entry("alpha", "acme.plugins.alpha:Plugin"),
        _Entry("alpha", "other.plugins.alpha:Plugin"),
    ]
    assert registered_names("acme.plugins", entries) == ("alpha", "beta")


# ---------------------------------------------------------------------------------------------------------------
# the door: a registration refuses at declaration
# ---------------------------------------------------------------------------------------------------------------


def test_the_registry_is_a_mapping_of_the_resolved_plugins() -> None:
    """A registry reads as a name -> plugin mapping, so a consumer's `registry[name]` is the one lookup."""
    registry = _registry("alpha", "beta")
    assert dict(registry) == {"alpha": "plugin:alpha", "beta": "plugin:beta"}
    assert registry["alpha"] == "plugin:alpha"
    assert len(registry) == 2


def test_register_loads_the_target_at_declaration() -> None:
    """The door imports the target when it is registered, so the plugin is reachable without Python."""
    registry = Registry()
    register(registry, Registration(name="brand", target="configkit.spec:Brand"))
    from configkit.spec import Brand

    assert registry["brand"] is Brand


def test_register_refuses_a_name_twice_at_declaration() -> None:
    """One name names one plugin: a second registration of the name is refused, naming it."""
    registry = Registry()
    register(registry, Registration(name="brand", target="configkit.spec:Brand"))
    with pytest.raises(Fault) as raised:
        register(registry, Registration(name="brand", target="configkit.spec:Brand"))
    assert "brand" in raised.value.message
    assert raised.value.code == "value_error"


def test_register_refuses_a_target_that_does_not_import() -> None:
    """A target naming an absent module is refused at the registration, not at the registry's first read."""
    with pytest.raises(Fault) as raised:
        register(Registry(), Registration(name="ghost", target="definitely_absent_module:Plugin"))
    assert "ghost" in raised.value.message and "definitely_absent_module" in raised.value.message


def test_register_refuses_a_target_that_names_no_attribute() -> None:
    """A target whose attribute is absent is refused too, naming the row's target."""
    with pytest.raises(Fault) as raised:
        register(Registry(), Registration(name="brand", target="configkit.spec:NoSuchName"))
    assert "configkit.spec:NoSuchName" in raised.value.message


def test_a_registration_refuses_a_target_without_an_attribute() -> None:
    """``module:attribute`` is the one target grammar an entry point and a document both use."""
    with pytest.raises(ValueError) as raised:
        Registration(name="brand", target="configkit.spec")
    assert "configkit.spec" in str(raised.value)


# ---------------------------------------------------------------------------------------------------------------
# the declared field: register_from, reachable without Python (emb-train 20)
# ---------------------------------------------------------------------------------------------------------------


def test_register_from_reads_the_declared_field() -> None:
    """The document field ``register`` is a list of rows, and every row is registered in order."""
    registry = Registry()
    register_from(
        {"register": [{"name": "brand", "target": "configkit.spec:Brand"}]},
        registry=registry,
    )
    from configkit.spec import Brand

    assert registry["brand"] is Brand


def test_register_from_accepts_a_registration_record() -> None:
    """A caller that already parsed the rows passes the records themselves."""
    registry = Registry()
    register_from({"register": [Registration(name="brand", target="configkit.spec:Brand")]}, registry=registry)
    assert "brand" in registry


def test_register_from_refuses_a_missing_field_naming_it() -> None:
    """A document that declares no ``register`` field is refused, naming the field it read."""
    with pytest.raises(Fault) as raised:
        register_from({}, registry=Registry())
    assert "register" in raised.value.message
    assert raised.value.details["field"] == "register"


def test_register_from_refuses_a_row_that_is_not_a_name_and_a_target() -> None:
    """A row that is not a mapping (or lacks ``name``/``target``) names its index, so the document is fixable."""
    with pytest.raises(Fault) as raised:
        register_from({"register": [{"name": "brand"}]}, registry=Registry())
    assert "register[0]" in raised.value.message
    assert raised.value.details["index"] == 0


# ---------------------------------------------------------------------------------------------------------------
# the hook: a validation pass over the resolved registry (rcp-21)
# ---------------------------------------------------------------------------------------------------------------


def test_validation_hooks_returns_the_declared_hooks_in_order() -> None:
    """The hooks a registry declares are returned in declaration order, and the registry holds no other list."""
    seen: list[str] = []

    def first(registry: Mapping[str, Any], /) -> None:
        seen.append(f"first:{sorted(registry)}")

    def second(registry: Mapping[str, Any], /) -> None:
        seen.append(f"second:{sorted(registry)}")

    registry = Registry()
    registry.add_hook(first)
    registry.add_hook(second)
    register(registry, Registration(name="brand", target="configkit.spec:Brand"))
    hooks = validation_hooks(registry)
    assert hooks == (first, second)
    for hook in hooks:  # a hook runs after the registry resolved, so it sees the plugin
        hook(registry)
    assert seen == ["first:['brand']", "second:['brand']"]


def test_a_registry_starts_with_no_hook() -> None:
    """An empty registry declares no hook, so a caller's loop runs nothing."""
    assert validation_hooks(Registry()) == ()


def test_a_hook_is_a_public_callable() -> None:
    """The hook seam is the public protocol, so a consumer's own function is one without an import."""

    def hook(registry: Mapping[str, Any], /) -> None:
        del registry

    registry = Registry(hooks=(hook,))
    assert validation_hooks(registry) == (hook,)


def test_a_registry_keeps_the_order_it_was_given() -> None:
    """A registry built from a mapping iterates in the mapping's order, so a listing is stable."""
    assert list(_registry("beta", "alpha")) == ["beta", "alpha"]


def test_a_registry_refuses_a_name_through_setitem_too() -> None:
    """The duplicate-name policy is the registry's, so a direct ``registry[name] = ...`` obeys it as well."""
    registry = _registry("alpha")
    with pytest.raises(Fault) as raised:
        registry["alpha"] = "another"
    assert "alpha" in raised.value.message


def test_a_registry_deletes_a_name() -> None:
    """A name can be removed, and the listing follows."""
    registry = _registry("alpha", "beta")
    del registry["alpha"]
    assert list(registry) == ["beta"]


def test_a_provider_protocol_is_satisfied_by_an_entry_point_shape() -> None:
    """The protocol names three members, so a consumer's own record is a provider without inheriting one."""
    entry: Provider = _Entry("alpha", "acme.plugins.alpha:Plugin")
    assert entry.load() == "the-plugin"
