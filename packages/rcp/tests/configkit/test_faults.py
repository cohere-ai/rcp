"""The refusal base, the generic code table, the exit-code seam and the warning channel.

Every deliberate refusal in the library is one :class:`Fault` carrying the five fields the coverage names
(rcp-32), a stable ``code`` from a data-driven table and no exit number: the number is the consumer's, and
:func:`exit_codes` is where it enters. :func:`require` names the caller's install line, never a distribution of
the library's own, and :func:`warn` refuses a code the record did not declare (rcp-31).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import configkit.faults as faults
import pytest
from configkit.faults import (
    FAULT_CODES,
    ConfigWarning,
    Fault,
    ValueErrorFault,
    classify,
    exit_codes,
    register_fault_codes,
    require,
    warn,
)
from pydantic import BaseModel


def test_five_fields() -> None:
    """rcp-32: the record carries exactly the five fields the coverage names."""
    assert set(Fault.__annotations__) == {"message", "code", "details", "hint", "cli_hint"}


def test_a_raise_carries_code_and_message_and_no_exit_code() -> None:
    """D3: the code is declared at the raise; the exit number is resolved by the front end, never baked here."""
    fault = Fault("the file is not UTF-8", code="value_error", details={"path": "a.yaml"}, hint="save it as UTF-8")
    assert fault.code == "value_error"
    assert fault.message == "the file is not UTF-8"
    assert fault.details == {"path": "a.yaml"}
    assert fault.hint == "save it as UTF-8"
    assert fault.cli_hint is None
    assert not hasattr(fault, "exit_code")
    assert "exit_code" not in Fault.__annotations__


def test_the_generic_table_is_the_four_generic_rows() -> None:
    """L2.3: the library ships the generic rows only, and no consumer code."""
    assert [(cls, code, owner) for cls, code, owner in FAULT_CODES] == [
        (OSError, "os_error", "configkit"),
        (RuntimeError, "runtime_error", "configkit"),
        (ValueError, "value_error", "configkit"),
        (ModuleNotFoundError, "optional.missing", "configkit"),
    ]


def test_every_subclass_is_in_the_table() -> None:
    """D2: a ``Fault`` subclass carries a code the table declares (the mutation adds one whose code it does not)."""
    codes = {code for _, code, _owner in FAULT_CODES}
    subclasses = {
        name: cls
        for name, cls in vars(faults).items()
        if isinstance(cls, type) and issubclass(cls, Fault) and cls is not Fault
    }
    assert subclasses, "the check is vacuous with no Fault subclass"
    assert sorted(name for name, cls in subclasses.items() if cls.code not in codes) == []


def test_a_duplicated_code_is_refused() -> None:
    """D1: a code declared twice names two faults, so the table refuses it."""
    with pytest.raises(ValueErrorFault, match="declared twice"):
        register_fault_codes([(KeyError, "value_error", "rcp")])
    with pytest.raises(ValueErrorFault, match="declared twice"):
        register_fault_codes([(KeyError, "config_invalid", "rcp"), (LookupError, "config_invalid", "rcp")])


def test_a_row_names_the_owner_that_declares_its_code() -> None:
    """L4: plan §4.4's `ROWS` is `(fault class, stable code, owner)`, and R1's rows are `owner="rcp"`.

    A code with no owner cannot be attributed when two consumers in one process register their tables.
    """
    table = register_fault_codes([(KeyError, "config_invalid", "rcp")])
    assert table[0] == (KeyError, "config_invalid", "rcp")
    with pytest.raises(ValueErrorFault, match="owner"):
        register_fault_codes([(KeyError, "config_invalid", "")])  # type: ignore[list-item]
    with pytest.raises(ValueErrorFault, match="row"):
        register_fault_codes([(KeyError, "config_invalid")])  # type: ignore[list-item]


def test_register_returns_the_consumer_rows_then_the_generic_ones() -> None:
    """The table is the caller's value: the consumer's narrow rows first, the generic builtins last."""
    table = register_fault_codes([(KeyError, "config_invalid", "rcp")])
    assert table[0] == (KeyError, "config_invalid", "rcp")
    assert table[1:] == FAULT_CODES
    assert register_fault_codes(()) == FAULT_CODES


def test_classify_passes_a_fault_through_and_defers_otherwise() -> None:
    """One ``classify``: a deliberate fault is itself, anything else is the consumer's mapper's decision."""
    fault = Fault("boom", code="value_error")
    assert classify(fault, mapper=lambda exc: Fault("mapped", code="runtime_error")) is fault
    mapped = classify(KeyError("k"), mapper=lambda exc: Fault(str(exc), code="config_invalid"))
    assert mapped.code == "config_invalid"
    assert mapped.message == "'k'"


def test_exit_codes_are_the_consumers_integers() -> None:
    """rcp-5: the library ships no exit number; the consumer's table becomes the library's typed seam."""
    table = exit_codes({"value_error": 3, "os_error": 4})
    assert table["value_error"] == 3
    assert table.get("missing", None) is None
    with pytest.raises(ValueErrorFault, match="not an int"):
        exit_codes({"value_error": "3"})


def test_require_returns_an_installed_module() -> None:
    """The happy path: an installed module is imported and returned."""
    module = require("json", hint="the standard library always ships it")
    assert module.loads('{"a": 1}') == {"a": 1}


def test_require_names_the_callers_hint() -> None:
    """L2.4: the fault's code is ``optional.missing`` and the message carries the caller's line verbatim."""
    with pytest.raises(Fault) as caught:
        require("definitely_absent_module", hint="<the-callers-extra>", extra="data")
    fault = caught.value
    assert fault.code == "optional.missing"
    assert "<the-callers-extra>" in fault.message
    assert fault.hint == "<the-callers-extra>"
    assert fault.details == {"module": "definitely_absent_module", "extra": "data"}


def test_the_library_bakes_no_install_line() -> None:
    """The install line is the caller's: ``faults.py`` carries no distribution name of its own."""
    source = Path(str(faults.__file__)).read_text(encoding="utf-8")
    assert "pip install" not in source


class _Noticed(BaseModel):
    """A record that carries a warning channel: its closed set is declared on the record (rcp-31)."""

    WARNING_CODES: ClassVar[tuple[str, ...]] = ("UNPINNED_REVISION",)

    model: str
    revision: str


class _Silent(BaseModel):
    """A record with no warning channel declared."""

    model: str


def test_warn_emits_one_warning_keyed_by_the_records_code() -> None:
    """rcp-34: one channel, and the code is one the record declared."""
    record = _Noticed(model="m", revision="main")
    with pytest.warns(UserWarning) as caught:
        warn(record, "UNPINNED_REVISION", revision="main")
    assert len(caught) == 1
    warning = caught[0].message
    assert warning.code == "UNPINNED_REVISION"
    assert warning.fields["model"] == "m"
    assert warning.fields["revision"] == "main"


def test_warn_refuses_a_code_outside_the_records_set() -> None:
    """rcp-31: the set is the record's, so an undeclared code is refused here and not at the consumer."""
    with pytest.raises(ValueErrorFault, match="UNPINNED_REVISION"):
        warn(_Noticed(model="m", revision="main"), "OTHER")
    with pytest.raises(ValueErrorFault, match="WARNING_CODES"):
        warn(_Silent(model="m"), "UNPINNED_REVISION")


def test_the_facade_publishes_every_fault_class() -> None:
    """L18: four of ``faults.__all__``'s names were unreachable from ``configkit``, though four modules import them."""
    import configkit

    assert set(faults.__all__) <= set(configkit.__all__)
    for name in faults.__all__:
        assert getattr(configkit, name) is getattr(faults, name)


def test_every_public_door_raises_the_one_fault() -> None:
    """H9: a bare ``ValueError`` is what the product's ``classify`` reports as an unexpected failure (exit 1).

    Five modules refused a declaration with one, against this module's own rule ("every deliberate refusal in the
    library is a :class:`Fault` ... a caller catches this once"). Each door now raises the fault the consumer's
    code table knows.
    """
    from configkit.commands import Command, Step, customise, index
    from configkit.plugins import Registration
    from configkit.schemas import DocumentId

    class _Empty(BaseModel):
        """A request model with no field."""

    command = Command(name="run list", request=_Empty, result=None, run=lambda request: _Empty())
    doors = (
        lambda: DocumentId(name=" ", accepted_ids=("p.x.v1",), removable="p.x.v1"),
        lambda: Registration(name=" ", target="acme.plugin:a"),
        lambda: Command(name=" ", request=_Empty, result=None, run=lambda request: _Empty()),
        lambda: Step("", "field", lambda values: 1),
        lambda: index([command, command]),
        lambda: customise(command, "--nope", lambda: "x"),
    )
    for door in doors:
        with pytest.raises(ValueErrorFault):
            door()


def test_the_warning_class_is_public_and_matches_the_envelope() -> None:
    """H5: the class was private and absent from the facade, so the product's envelope dropped every warning.

    ``rcp``'s envelope asks ``isinstance(record.message, RcpNdcgWarning)`` to decide which warnings its
    ``--json`` document carries; a private class made the answer "none of the library's".
    """
    import configkit

    assert "ConfigWarning" in faults.__all__
    assert "ConfigWarning" in configkit.__all__
    assert configkit.ConfigWarning is ConfigWarning
    with pytest.warns(ConfigWarning):
        warn(_Noticed(model="m", revision="main"), "UNPINNED_REVISION")


def test_the_warning_channel_merges_a_base_declaration() -> None:
    """M7: the channel's merge and the audit's A7 read one walk, so a base class's code is not invisible."""

    class _Base(BaseModel):
        WARNING_CODES: ClassVar[tuple[str, ...]] = ("BASE",)

        model: str

    class _Child(_Base):
        WARNING_CODES: ClassVar[tuple[str, ...]] = ("CHILD",)

    with pytest.warns(ConfigWarning):
        warn(_Child(model="m"), "BASE")
    with pytest.warns(ConfigWarning):
        warn(_Child(model="m"), "CHILD")
