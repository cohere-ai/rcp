"""The host services: one protocol per call, one frozen record, and a log facade that installs nothing at import.

The library names the seam and builds no graph (emb-train 22): a consumer injects its clock, budget, log sink,
exit-code table, renderer, atomic writer and URI reader/writer. ``logger``/``configure``/``once`` are the log
facade -- the library creates loggers and installs no handler until ``configure`` is called (rcp-35) -- and the
state they read is a ``LogState`` the record carries (M2), so two records never share a handler or a dedup key.
The root that state writes under is the consumer's ``Brand.name`` (M3): ``configkit`` by default, never another
program's root.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from configkit.ports import LogState, Ports, configure, logger, once
from configkit.spec import Brand

#: The logger roots this module configures: the library's default, and the brand a test passes.
_ROOTS = ("configkit", "acme")


class _Clock:
    """A clock fixed at zero."""

    def now(self) -> float:
        return 0.0


class _Budget:
    """A budget that never runs out."""

    def check(self) -> None:
        return None


class _Sink:
    """A sink that keeps the lines it is handed."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


class _Renderer:
    """A renderer that formats a result with ``str``."""

    def __call__(self, result: object) -> str:
        return str(result)


class _Atomic:
    """An atomic writer that writes the file directly (the test's own storage)."""

    def write(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)

    def swap(self, path: Path, staged: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(path)


class _Uri:
    """A URI reader/writer with no bytes to offer."""

    def read(self, uri: str) -> bytes:
        return b""

    def write(self, uri: str, data: bytes) -> None:
        return None


def _ports(sink: _Sink) -> Ports:
    """A ``Ports`` record filled with the module's fakes, and a fresh log state of its own."""
    return Ports(
        clock=_Clock(),
        budget=_Budget(),
        sink=sink,
        exit_codes={},
        renderer=_Renderer(),
        atomic=_Atomic(),
        uri=_Uri(),
    )


@pytest.fixture(autouse=True)
def _restore_handlers() -> Iterator[None]:
    """Undo whatever ``configure`` installed, so one test's sink never sees another's records."""
    before = {name: list(logging.getLogger(name).handlers) for name in _ROOTS}
    yield
    for name, installed in before.items():
        package = logging.getLogger(name)
        for handler in list(package.handlers):
            if handler not in installed:
                package.removeHandler(handler)


def test_ports_carries_every_service_and_the_log_state_and_is_frozen() -> None:
    """rcp-3, M2: one record naming every host service and the facade's state; nothing can be swapped in place."""
    sink = _Sink()
    ports = _ports(sink)
    assert set(Ports.__dataclass_fields__) == {
        "clock",
        "budget",
        "sink",
        "exit_codes",
        "renderer",
        "atomic",
        "uri",
        "log",
    }
    # L19: the record is exercised through the fakes, so a field that carries nothing reddens this test.
    assert ports.clock.now() == 0.0
    assert ports.budget.check() is None
    assert ports.sink is sink
    assert ports.exit_codes.get("value_error", None) is None
    assert ports.renderer(1) == "1"
    assert isinstance(ports.atomic, _Atomic)
    assert ports.uri.read("file:///x") == b""
    assert isinstance(ports.log, LogState)
    assert once(ports.log, "test_ports.field") is True
    with pytest.raises(FrozenInstanceError):
        ports.clock = _Clock()


def test_importing_the_module_installs_no_handler(tmp_path: Path) -> None:
    """rcp-35: an import changes nothing about the host's logging -- the root logger's handlers and the package's.

    Both are counted, because ``logging.basicConfig()`` at module level would touch the root logger while leaving
    ``configkit``'s handler list empty: a check of the package logger alone cannot see that defect.
    """
    code = (
        "import logging\n"
        "before = len(logging.getLogger().handlers)\n"
        "import configkit.ports\n"
        "print(before, len(logging.getLogger().handlers), len(logging.getLogger('configkit').handlers))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["0", "0", "0"]


def test_configure_sends_the_librarys_records_to_the_sink() -> None:
    """One configured sink, and the library's own records reach it."""
    sink, state = _Sink(), LogState()
    configure(state, sink, level="INFO")
    logger(state, "configkit.ports").warning("hello")
    assert sink.lines == ["WARNING configkit.ports: hello"]


def test_configure_replaces_the_handler_it_installed() -> None:
    """A second ``configure`` leaves one handler, so a consumer can reconfigure in place."""
    state = LogState()
    first, second = _Sink(), _Sink()
    configure(state, first)
    configure(state, second)
    logger(state, "faults").info("once")
    assert first.lines == []
    assert second.lines == ["INFO configkit.faults: once"]


def test_configure_dedups_by_key() -> None:
    """The dedup key is a parameter: a repeated key is dropped, a new one is not."""
    sink, state = _Sink(), LogState()
    configure(state, sink, dedup_key=lambda record: record.getMessage())
    log = logger(state, "configkit.ports")
    log.info("same")
    log.info("same")
    log.info("other")
    assert sink.lines == ["INFO configkit.ports: same", "INFO configkit.ports: other"]


def test_once_is_true_only_the_first_time() -> None:
    """The primitive behind the dedup: one key, one ``True``."""
    state = LogState()
    assert once(state, "test_ports.once.first") is True
    assert once(state, "test_ports.once.first") is False


def test_logger_prefixes_once() -> None:
    """A module name lands under the facade's root, and a name that already carries the prefix is kept."""
    state = LogState()
    assert logger(state, "faults").name == "configkit.faults"
    assert logger(state, "configkit.roles").name == "configkit.roles"


def test_two_records_do_not_share_the_facades_state() -> None:
    """M2: the state is the record's, not the process's -- two ``Ports`` carry two states and two key sets.

    Red on the module globals: ``once`` read one process-wide set, so the second record's key was already seen.
    """
    first, second = _ports(_Sink()), _ports(_Sink())
    assert first.log is not second.log
    assert once(first.log, "test_ports.shared") is True
    assert once(first.log, "test_ports.shared") is False
    assert once(second.log, "test_ports.shared") is True


def test_a_second_records_configure_leaves_the_firsts_handler_installed() -> None:
    """M2: each record remembers the handler it installed, so a second record's ``configure`` removes nothing.

    Red on the module global ``_HANDLER``: the second call removed the first record's handler, so the first
    record's sink received nothing. Both states share the default root here, and the logging module's logger for
    a root is process-wide -- what the facade owns per record is the handler it installed on it.
    """
    first_sink, second_sink = _Sink(), _Sink()
    first, second = _ports(first_sink), _ports(second_sink)
    configure(first.log, first_sink)
    configure(second.log, second_sink)
    logger(first.log, "configkit.ports").warning("first")
    assert first_sink.lines == ["WARNING configkit.ports: first"]
    assert second_sink.lines == ["WARNING configkit.ports: first"]  # one root, one logger: both handlers see it


def test_the_log_root_is_the_brands_name() -> None:
    """M3: the root is the consumer's ``Brand.name`` -- ``configkit`` by default, and no other program's root.

    Red before the brand: the root was a fixed string, so a consumer that runs the library under its own name
    had every library line written under ``configkit`` and its handler installed on ``configkit`` only.
    """
    assert LogState().root == "configkit"
    assert logger(LogState(), "faults").name == "configkit.faults"
    acme = Brand(name="acme", prefix="ACME", schema_root="acme.cli.v1", output_schema_prefix="acme.run")
    state = LogState(brand=acme)
    assert state.root == "acme"
    assert logger(state, "faults").name == "acme.faults"
    assert logger(state, "acme.faults").name == "acme.faults"
    sink = _Sink()
    configure(state, sink)
    logger(state, "faults").warning("hello")
    assert sink.lines == ["WARNING acme.faults: hello"]
