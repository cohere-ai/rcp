"""Where the package reads and writes by default, resolved lazily from the environment.

Nothing here runs at import and nothing creates a directory: callers create what they write into. The
variables, all named from :data:`BRAND` (the program's one :class:`~configkit.spec.Brand`, so the log root in
:mod:`rcp.support.logging` and these names cannot drift apart):

* ``RCP_NDCG_CACHE_DIR``: caches (media, remote objects, synced trees). Default ``$XDG_CACHE_HOME/rcp``,
  else ``~/.cache/rcp``.
* ``RCP_NDCG_RUNS_DIR``: where run directories live. Default ``runs`` in the working directory.
* ``RCP_NDCG_LOG_LEVEL``: the stderr log level when neither ``-v`` nor ``-q`` is given. Default ``INFO``.
"""

from __future__ import annotations

import os
from pathlib import Path

from configkit.spec import Brand
from configkit.storage import RunsRoot

from rcp.errors import ConfigError

#: The program's brand: the log root (``rcp``), the environment prefix (``RCP_NDCG``) and the schema roots the
#: ``--json`` envelope and the exported schemas sit under. One declaration, read by this module, by
#: :mod:`rcp.support.logging` and by the schema modules.
BRAND = Brand(name="rcp", prefix="RCP_NDCG", schema_root="rcp.cli.v1", output_schema_prefix="rcp.run")

CACHE_DIR_ENV = f"{BRAND.prefix}_CACHE_DIR"
RUNS_DIR_ENV = f"{BRAND.prefix}_RUNS_DIR"
LOG_LEVEL_ENV = f"{BRAND.prefix}_LOG_LEVEL"

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def cache_dir() -> Path:
    """The cache root: ``$RCP_NDCG_CACHE_DIR``, else the user cache directory (not created)."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "rcp"


def runs_root() -> RunsRoot:
    """The runs root as the library's layout: ``$RCP_NDCG_RUNS_DIR``, else ``runs`` in the working directory.

    The root is not created. :meth:`~configkit.storage.RunsRoot.run_dir` and ``step_dir`` are the one join of a
    run id (and a step) onto it, so a writer and a reader of a run directory cannot spell ``<root>/<run id>``
    differently.
    """
    return RunsRoot(Path(os.environ.get(RUNS_DIR_ENV) or "runs").expanduser())


def runs_dir() -> Path:
    """The runs root's directory: :func:`runs_root`'s ``root`` (not created)."""
    return runs_root().root


def log_level() -> str:
    """The default stderr log level: ``$RCP_NDCG_LOG_LEVEL`` (a logging level name), else ``INFO``.

    Raises:
        ConfigError: The variable names no logging level.
    """
    level = (os.environ.get(LOG_LEVEL_ENV) or "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"${LOG_LEVEL_ENV}={level!r} is not a log level", hint=f"use one of {', '.join(_LOG_LEVELS)}")
    return level


def env_int(name: str, default: int) -> int:
    """An integer environment override: ``$<name>`` when set, else *default*.

    The one parser for the package's integer environment variables, so a malformed override is a typed
    :class:`~rcp.errors.ConfigError` naming the variable, never a raw ``ValueError`` (a module-level
    ``int(os.environ[...])`` used to make ``import rcp`` fail, every CLI command included).

    Args:
        name: The environment variable's name.
        default: The value when the variable is unset (or blank).

    Returns:
        The integer value.

    Raises:
        ConfigError: The value is not an integer.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"${name} must be an integer, got {raw!r}",
            hint=f"set {name}=<integer> (e.g. {name}={default}), or unset it",
        ) from None


__all__ = [
    "BRAND",
    "CACHE_DIR_ENV",
    "LOG_LEVEL_ENV",
    "RUNS_DIR_ENV",
    "cache_dir",
    "env_int",
    "log_level",
    "runs_dir",
    "runs_root",
]
