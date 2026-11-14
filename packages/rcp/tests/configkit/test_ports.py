"""The host services: one protocol per call, one frozen record, and a log facade that installs nothing at import.

The library names the seam and builds no graph (emb-train 22): a consumer injects its clock, budget, log sink,
exit-code table, renderer, atomic writer and URI reader/writer. ``logger``/``configure``/``once`` are the log
facade -- the library creates loggers and installs no handler until ``configure`` is called (rcp-35).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from configkit.ports import Ports, configure, logger, once


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
    """A ``Ports`` record filled with the module's fakes."""
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
    package = logging.getLogger("configkit")
    before = list(package.handlers)
    yield
    for handler in list(package.handlers):
        if handler not in before:
            package.removeHandler(handler)


def test_ports_carries_the_seven_services_and_is_frozen() -> None:
    """rcp-3: one record naming every host service, and a consumer cannot swap one in place."""
    sink = _Sink()
    ports = _ports(sink)
    assert set(Ports.__dataclass_fields__) == {"clock", "budget", "sink", "exit_codes", "renderer", "atomic", "uri"}
    # L19: the record is exercised through the fakes, so a field that carries nothing reddens this test.
    assert ports.clock.now() == 0.0
    assert ports.budget.check() is None
    assert ports.sink is sink
    assert ports.exit_codes.get("value_error", None) is None
    assert ports.renderer(1) == "1"
    assert isinstance(ports.atomic, _Atomic)
    assert ports.uri.read("file:///x") == b""
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
    sink = _Sink()
    configure(sink, level="INFO")
    logger("configkit.ports").warning("hello")
    assert sink.lines == ["WARNING configkit.ports: hello"]


def test_configure_replaces_the_handler_it_installed() -> None:
    """A second ``configure`` leaves one handler, so a consumer can reconfigure in place."""
    first, second = _Sink(), _Sink()
    configure(first)
    configure(second)
    logger("faults").info("once")
    assert first.lines == []
    assert second.lines == ["INFO configkit.faults: once"]


def test_configure_dedups_by_key() -> None:
    """The dedup key is a parameter: a repeated key is dropped, a new one is not."""
    sink = _Sink()
    configure(sink, dedup_key=lambda record: record.getMessage())
    log = logger("configkit.ports")
    log.info("same")
    log.info("same")
    log.info("other")
    assert sink.lines == ["INFO configkit.ports: same", "INFO configkit.ports: other"]


def test_once_is_true_only_the_first_time() -> None:
    """The primitive behind the dedup: one key, one ``True``."""
    assert once("test_ports.once.first") is True
    assert once("test_ports.once.first") is False


def test_logger_prefixes_once() -> None:
    """A module name lands under the library's logger, and a name that already carries the prefix is kept."""
    assert logger("faults").name == "configkit.faults"
    assert logger("configkit.roles").name == "configkit.roles"
