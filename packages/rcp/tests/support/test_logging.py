"""Tests for shared logger naming and configuration, and the program's one brand.

The logger naming and the log root are :mod:`configkit.ports`'s facade (``LogState`` over a
:class:`~configkit.spec.Brand`), so the root and the environment prefix are one declaration: a module that
reaches for ``"rcp"`` or ``"RCP_NDCG_"`` a second time is what this file pins out.
"""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from rcp.errors import ConfigError
from rcp.support.logging import BASE_LOGGER_NAME, configure_logging, get_logger


def test_get_logger_does_not_duplicate_package_prefix() -> None:
    assert get_logger("rcp.calibration.calibrate").name == "rcp.calibration.calibrate"


def test_get_logger_prefixes_short_names() -> None:
    assert get_logger("worker").name == "rcp.worker"


def test_an_unknown_level_is_a_config_error_naming_the_known_ones() -> None:
    """paths.log_level() raises ConfigError for a bad env var; the direct call raised a bare
    ValueError from three frames inside logging -- two entry points, one typed, one not."""
    with pytest.raises(ConfigError, match="SPAM"):
        configure_logging("SPAM")


class TestBrand:
    """The program's brand: the log root, the environment prefix and the paths they name."""

    def test_the_log_root_and_the_environment_prefix_are_one_brand(self) -> None:
        from rcp.support.paths import BRAND, CACHE_DIR_ENV, LOG_LEVEL_ENV, RUNS_DIR_ENV

        assert BASE_LOGGER_NAME == BRAND.name == "rcp"
        assert (CACHE_DIR_ENV, RUNS_DIR_ENV, LOG_LEVEL_ENV) == (
            f"{BRAND.prefix}_CACHE_DIR",
            f"{BRAND.prefix}_RUNS_DIR",
            f"{BRAND.prefix}_LOG_LEVEL",
        )

    def test_the_schema_root_is_the_envelope_the_cli_prints(self) -> None:
        from rcp.cli.output import CLI_SCHEMA
        from rcp.support.paths import BRAND

        assert BRAND.schema_root == CLI_SCHEMA

    def test_importing_the_module_installs_no_handler(self) -> None:
        """rcp-35: an import changes nothing about the host's logging. The CLI installs its handler through
        :func:`configure_logging`, and only there."""
        script = (
            "import logging\n"
            "before = (len(logging.getLogger().handlers), len(logging.getLogger('rcp').handlers))\n"
            "import rcp.support.logging\n"
            "after = (len(logging.getLogger().handlers), len(logging.getLogger('rcp').handlers))\n"
            "print(after == before == (0, 0))\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "True", result.stdout + result.stderr

    def test_configure_installs_one_handler_and_replaces_it(self) -> None:
        logger = logging.getLogger(BASE_LOGGER_NAME)

        def installed() -> list[logging.Handler]:
            return [handler for handler in logger.handlers if getattr(handler, "_rcp", False)]

        try:
            configure_logging("INFO")
            first = installed()
            assert len(first) == 1, "one sink, one handler"
            configure_logging("DEBUG")
            assert installed() != first and len(installed()) == 1, "a second call replaces it"
        finally:
            for handler in installed():
                logger.removeHandler(handler)
                handler.close()

    def test_the_cache_and_runs_directories_follow_the_brand_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rcp.support.paths import CACHE_DIR_ENV, RUNS_DIR_ENV, cache_dir, runs_dir

        monkeypatch.setenv(CACHE_DIR_ENV, str(tmp_path / "cache"))
        monkeypatch.setenv(RUNS_DIR_ENV, str(tmp_path / "runs"))
        assert cache_dir() == tmp_path / "cache"
        assert runs_dir() == tmp_path / "runs"

        monkeypatch.delenv(CACHE_DIR_ENV)
        monkeypatch.delenv(RUNS_DIR_ENV)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert cache_dir() == Path.home() / ".cache" / "rcp"
        assert runs_dir() == Path("runs")
