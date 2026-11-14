"""Reading a YAML config: one file, optionally ``extends:`` another, plus ``key=value`` overrides.

The one config system of the package. A config is a YAML mapping; a top-level
``extends: <path>`` names a base config (relative to the extending file) whose
mapping is deep-merged under it, recursively; each override ``dotted.key=value``
then replaces one leaf, its value parsed as YAML (``k=10`` is an int,
``k=null`` is ``None``, ``k=[1, 2]`` a list). The result is a plain dict for a
pydantic model to validate, so a typo in a key fails there, not silently here: :func:`config_error` turns
that failure into a :class:`~rcp.errors.ConfigError` whose ``details.errors`` name, per problem, the field
path, the given value, the expected type, the closest known key for an unknown one, and whether the value came
from the file or from a ``--set`` override.

The reader, the ``extends`` merge and the value ladder are the library's (:mod:`configkit.document`,
:mod:`configkit.overrides`); what stays here is the product's policy: each file's relative values resolve against
the file that declares them (:func:`load_config`'s ``relative``, the two-base rule), a config that does not parse
is CONFIG (exit 3), a base that is missing is MISSING_INPUT (exit 4), and no key is dropped on the way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from configkit import document
from configkit import overrides as override_records
from configkit.faults import Fault, OSErrorFault

from rcp.errors import ConfigError
from rcp.support.resources import REDACTED, carries_a_key, looks_like_secret
from rcp.support.urls import redact_urls

EXTENDS_KEY = "extends"


def deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """``base`` with ``update`` merged in: mappings merge key by key, anything else is replaced.

    The result is a new mapping that shares no mutable value with ``base`` or ``update``. The rule is the
    library's one implementation of ``extends`` (:func:`configkit.document.deep_merge`).
    """
    return document.deep_merge(base, update)


def apply_overrides(config: Mapping[str, Any], overrides: Iterable[str]) -> dict[str, Any]:
    """Apply ``dotted.key=value`` overrides; each value is parsed as YAML.

    The value ladder is :func:`configkit.overrides.parse_value`'s, so a ``--set`` value means the same thing here
    as in the library; the walk over a plain mapping stays, because the library's ``apply`` writes into a model.

    Raises:
        ConfigError: an override without ``=``, a path through a non-mapping value, or a value that is not a YAML
            literal.
    """
    result: dict[str, Any] = deep_merge({}, config)
    for override in overrides:
        key, sep, raw = override.partition("=")
        if not sep or not key.strip():
            raise ConfigError(
                f"override {override!r} is not key=value",
                hint="write e.g. judge.concurrency=8",
                cli_hint="write e.g. --set judge.concurrency=8",
            )
        parts = key.strip().split(".")
        node: dict[str, Any] = result
        for depth, part in enumerate(parts[:-1]):
            child = node.get(part)
            if child is None:
                child = node[part] = {}
            elif not isinstance(child, dict):
                raise ConfigError(f"override {override!r}: {'.'.join(parts[: depth + 1])} is not a mapping")
            node = child
        node[parts[-1]] = _value(raw)
    return result


def _value(raw: str) -> Any:
    """One override value on the one ladder: an empty value is ``""``, every other value a YAML literal.

    A value that is not a literal is the library's refusal (:class:`configkit.faults.Fault`), reported here as
    the product's :class:`~rcp.errors.ConfigError`: a bad ``--set`` value is CONFIG (exit 3), not a bug.
    """
    try:
        return override_records.parse_value(raw)
    except Fault as fault:
        raise ConfigError(fault.message, hint=fault.hint, cli_hint=fault.cli_hint) from fault


def load_config(
    path: str | Path,
    *,
    overrides: Iterable[str] = (),
    relative: Callable[[dict[str, Any], Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read a YAML config, resolving ``extends:`` chains, then apply ``overrides``.

    Args:
        path: The config file.
        overrides: ``dotted.key=value`` overrides, applied last.
        relative: Applied to each file's own mapping with that file's directory, before it is merged: a config
            model's relative paths then resolve against the file that declares them, not the one that extends it.

    Raises:
        ConfigError: the file is not a mapping, or ``extends`` forms a cycle.
        FileNotFoundError: the file or a base it extends does not exist.
    """
    try:
        loaded = document.read(Path(path), on_file=relative)
    except OSErrorFault as fault:
        # The library wraps a base it cannot read. The product reports the host's own refusal, so a missing base
        # stays MISSING_INPUT (exit 4) instead of the library's generic ``os_error`` (INTERNAL, "report a bug").
        cause = fault.__cause__
        raise (cause if isinstance(cause, OSError) else fault) from None
    except Fault as fault:
        # A document that does not parse is the product's CONFIG (exit 3); the library's generic code is not
        # the product's exit number.
        raise ConfigError(fault.message, hint=fault.hint, cli_hint=fault.cli_hint, details=fault.details) from fault
    payload = loaded.payload
    if loaded.schema is not None:
        # A run config declares no `schema` key: it stays in the mapping so the model refuses it as an unknown
        # key, instead of the reader dropping it (nothing is cut silently).
        payload = {**payload, "schema": loaded.schema}
    return apply_overrides(payload, overrides)


def validation_problems(
    exc: Any,
    *,
    model: Any = None,
    source: str = "config",
    overrides: Sequence[str] = (),
    prefix: str = "",
) -> list[dict[str, Any]]:
    """One entry per problem of a pydantic ``ValidationError`` — the one shape of ``details.errors``.

    Each entry carries ``field`` (the dotted path), ``problem``, ``input`` (the given value with secrets
    redacted; ``null`` for a missing field), ``expected`` (the type or the allowed values, when known),
    ``did_you_mean`` (the closest known key, for an unknown one) and ``source`` (``"--set"`` when an
    override set the field, else *source*). Config validation (:func:`config_error`) and the CLI's argument
    validation raise with this shape.  ``exc`` that is not a pydantic ``ValidationError`` has no such problems
    to read: it is re-raised as itself, so a validator that raised something else surfaces as what it is
    instead of an ``AttributeError`` on it.

    The walk is :func:`configkit.document.config_error`'s: the field path a document has (a union member's tag
    dropped), the expected type from the schema node it reaches, the closest known key and the ``--set``
    attribution. What stays here is the product's policy: the redaction (the credential shapes this package
    knows -- :mod:`rcp.support.resources`, the ``env`` convention, a credential flag's value in an argv list)
    and the shape, which has no ``known`` key.
    """
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        raise exc
    if model is None:
        return _problems_without_a_model(exc, source=source, overrides=overrides, prefix=prefix)
    fault = document.config_error(exc, model=model, source=source, overrides=overrides, prefix=prefix, do_redact=False)
    return [
        _product_problem(problem, error, prefix)
        for problem, error in zip(fault.details["errors"], exc.errors(include_url=False), strict=True)
    ]


def _problems_without_a_model(exc: Any, *, source: str, overrides: Sequence[str], prefix: str) -> list[dict[str, Any]]:
    """The problems of a ``ValidationError`` whose model the caller did not name: what pydantic itself says.

    There is no schema to walk, so the field path is pydantic's own (a union member's tag included), the expected
    value is only what pydantic's context names, and no ``did_you_mean`` can be computed. The shape is the same
    as every other problem's.
    """
    set_keys = [override.partition("=")[0].strip() for override in overrides]
    problems = []
    for error in exc.errors(include_url=False):
        loc = [str(part) for part in error["loc"]]
        field = ".".join([prefix, *loc] if prefix else loc) or "<config>"
        problem: dict[str, Any] = {
            "field": field,
            "problem": str(error["msg"]).removeprefix("Value error, "),
            "input": None if error["type"] == "missing" else _redacted_input(_plain(error.get("input")), loc),
        }
        expected = (error.get("ctx") or {}).get("expected")
        if expected is not None:
            problem["expected"] = str(expected)
        by_set = any(field == key or field.startswith(f"{key}.") or key.startswith(f"{field}.") for key in set_keys)
        problem["source"] = "--set" if by_set else source
        problems.append(problem)
    return problems


def _product_problem(problem: Mapping[str, Any], error: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """One library problem as the product's, in the documented key order.

    The library's ``known`` key is dropped (``details.errors`` is the shape ``docs/reference/cli.md`` documents)
    and its ``input`` is not used: the value comes from pydantic's own error, redacted by this package's rule.
    The library redacts by the model's declared roles, so a model that declares none loses every string.
    """
    field = str(problem["field"])
    loc = field.split(".")[len(prefix.split(".")) :] if prefix else field.split(".")
    kept: dict[str, Any] = {
        "field": field,
        "problem": problem["problem"],
        "input": None if error["type"] == "missing" else _redacted_input(_plain(error.get("input")), loc),
    }
    for key in ("expected", "did_you_mean"):
        if key in problem:
            kept[key] = problem[key]
    kept["source"] = problem["source"]
    return kept


def config_error(
    exc: Any,
    *,
    model: Any = None,
    source: str = "config",
    overrides: Sequence[str] = (),
    prefix: str = "",
    hint: str | None = None,
) -> ConfigError:
    """A pydantic ``ValidationError`` of a config as a :class:`~rcp.errors.ConfigError` a caller can act on.

    ``details.errors`` holds one entry per problem, in the shape of :func:`validation_problems`. The message
    names the first problem and where it came from.

    Args:
        exc: The ``pydantic.ValidationError``.
        model: The model class (or ``TypeAdapter``) validated, for the expected types and the known keys.
        source: What was validated (a file path, ``"run config"``), blamed for problems no override explains.
        overrides: The ``dotted.key=value`` overrides applied before validating.
        prefix: The dotted path of the validated value inside the overrides' namespace (``"judge"`` when
            ``--set judge.x=...`` is validated as a judge config).
        hint: The hint, instead of the default one (fix the field in the file, or the ``--set`` value).
    """
    problems = validation_problems(exc, model=model, source=source, overrides=overrides, prefix=prefix)
    first = problems[0]
    where = f"--set {first['field']}" if first["source"] == "--set" else f"{source}: {first['field']}"
    guess = f"; did you mean {first['did_you_mean']!r}?" if "did_you_mean" in first else ""
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    cli_hint = None
    if hint is not None:
        pass
    elif first["source"] == "--set":
        hint = "fix the override; values are YAML literals (k=10, k=null, 'k=[1, 2]')"
        cli_hint = "fix the --set value; values are YAML literals (--set k=10, --set k=null, --set 'k=[1, 2]')"
    elif first["field"] == "<config>":
        hint = f"fix {source}"
    else:
        hint = f"fix {first['field']} in {source}, or override it (overrides=['{first['field']}=...'])"
        cli_hint = f"fix {first['field']} in {source}, or override it with --set {first['field']}=..."
    return ConfigError(
        f"{where}: {first['problem']}{guess}{more}", hint=hint, cli_hint=cli_hint, details={"errors": problems}
    )


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return repr(value)


def _credential_flag(word: str) -> bool:
    """Whether an argv word names a credential-bearing flag (``--api-key``, ``--hf-token``, ...).

    The flag's own name, with ``-`` read as ``_``, is judged by
    :func:`~rcp.support.resources.looks_like_secret`: ``--max-num-batched-tokens`` is a budget and
    ``--tokenizer`` names a model, so neither is a credential.
    """
    if not word.startswith("--"):
        return False
    flag = word.partition("=")[0].removeprefix("--").replace("-", "_")
    return looks_like_secret(flag)


def _redacted_word(word: str) -> str:
    """One argv word: a value that carries a credential by shape, or an inline ``--flag=value`` credential's
    value, becomes REDACTED; every other string is passed through
    :func:`~rcp.support.urls.redact_urls` so a credential-bearing URL cannot ride out either."""
    if carries_a_key(word):
        return REDACTED
    flag, separator, _ = word.partition("=")
    if separator and _credential_flag(flag):
        return f"{flag}={REDACTED}"
    return redact_urls(word)


def _redacted_words(words: Sequence[Any], loc: Sequence[str]) -> list[Any]:
    """An argv list: a credential flag's separated value (``--api-key sk-...``) becomes REDACTED too."""
    redacted: list[Any] = []
    credential_next = False
    for word in words:
        if isinstance(word, str):
            if credential_next and not word.startswith("-"):
                redacted.append(REDACTED)
                credential_next = False
                continue
            redacted.append(_redacted_word(word))
            credential_next = _credential_flag(word) and "=" not in word
        else:
            redacted.append(_redacted_input(word, loc))
            credential_next = False
    return redacted


def _redacted_input(value: Any, loc: Sequence[str]) -> Any:
    """``value`` as the ``input`` of one problem, with secrets redacted.

    The details reach ``--json`` output, CI logs and MCP tool results, so a secret must not ride out in them:
    a value under a secret-looking key (or under an ``env`` mapping, whose values are credentials by
    convention) becomes :data:`~rcp.support.resources.REDACTED`, a value that carries a credential by
    shape (:func:`~rcp.support.resources.carries_a_key`) becomes it too, a credential flag's value in an
    argv list (``--api-key sk-...``/``--api-key=sk-...``) is replaced, and every other string is passed
    through :func:`~rcp.support.urls.redact_urls` so a credential-bearing URL cannot either. ``loc`` is
    the problem's location; nested mapping keys are appended as the walk goes.
    """
    if any(looks_like_secret(part) for part in loc):
        return REDACTED
    if "env" in loc:
        # An env mapping's values are credentials by convention, whatever the variable is called; the names
        # stay (they are what the message is about) and every value becomes REDACTED.
        return {str(key): REDACTED for key in value} if isinstance(value, Mapping) else REDACTED
    if isinstance(value, Mapping):
        return {str(key): _redacted_input(item, (*loc, str(key))) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return _redacted_words(value, loc)
    if isinstance(value, str):
        return _redacted_word(value)
    return value


__all__ = ["EXTENDS_KEY", "apply_overrides", "config_error", "deep_merge", "load_config", "validation_problems"]
