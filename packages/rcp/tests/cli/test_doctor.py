"""``rcp doctor``: the credential variables it reports are the ones the shipped configs and backends read."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from click.testing import CliRunner

from rcp.cli.command import execute
from rcp.cli.doctor import _facts, doctor_cmd
from rcp.cli.introspect import command_specs

ROOT = Path(__file__).resolve().parents[4]


def _reported_variables() -> set[str]:
    result = CliRunner().invoke(doctor_cmd, ["--json"])
    assert result.exit_code == 0, result.output
    checks = json.loads(result.stdout)["data"]["checks"]
    return {check["name"][1:] for check in checks if check["name"].startswith("$")}


def test_doctor_reports_the_variables_the_shipped_configs_read() -> None:
    from rcp.inference.adapters.embeddings import (
        CohereEmbeddings,
        GeminiEmbeddings,
        OpenAIEmbeddings,
        VoyageEmbeddings,
    )

    shipped = [
        *ROOT.joinpath("configs").rglob("*.yaml"),
        *ROOT.joinpath("packages/rcp/src/rcp/judging").joinpath("judges").glob("*.yaml"),
    ]
    read = {m for path in shipped for m in re.findall(r"api_key_env:\s*([A-Z_]+)", path.read_text(encoding="utf-8"))}
    read |= {cls.API_KEY_ENV[0] for cls in (OpenAIEmbeddings, CohereEmbeddings, VoyageEmbeddings, GeminiEmbeddings)}
    assert "CO_API_KEY" in read
    reported = _reported_variables()
    assert read <= reported, read - reported
    assert "JINA_API_KEY" not in reported


def test_doctor_checks_every_module_the_error_map_names_under_its_extra() -> None:
    """doctor builds its extras table from the one map, and the served-only package needs no engine extra.

    The `vllm` extra is declared (the sibling `rcp-vllm` the recipes live in) but nothing in the product requires
    it: doctor reports its row as an install hint, like every other optional extra.
    """
    from rcp.cli.doctor import _EXTRAS
    from rcp.errors import EXTRA_FOR_MODULE

    assert {(module, extra) for extra, modules in _EXTRAS.items() for module in modules} == set(
        EXTRA_FOR_MODULE.items()
    )
    assert set(_EXTRAS["hf"]) == {"huggingface_hub", "tokenizers"}
    assert _EXTRAS["vllm"] == ("rcp_vllm",)


def test_doctor_never_echoes_url_credentials(monkeypatch) -> None:
    """The probed URL's userinfo and query stay out of the check detail: the report reaches --json output and
    gets pasted into issues."""
    import httpx

    def refused(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refused)
    result = CliRunner().invoke(doctor_cmd, ["--endpoint", "http://user:SECRET@127.0.0.1:9/v1?key=QKEY", "--json"])
    assert result.exit_code == 0, result.output
    assert "SECRET" not in result.stdout
    assert "QKEY" not in result.stdout


def _endpoint_check(result) -> dict:
    report = json.loads(result.stdout)["data"]
    return next(check for check in report["checks"] if check["name"] == "endpoint")


def test_doctor_sends_a_profiles_key_to_its_own_host(monkeypatch) -> None:
    """The probe sent no Authorization header, so a correctly keyed OpenAI endpoint always reported fail."""
    import httpx

    seen: list[httpx.Request] = []

    def get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url, headers=kwargs.get("headers"))  # type: ignore[arg-type]
        seen.append(request)
        if request.headers.get("authorization") != "Bearer sk-test":
            return httpx.Response(401, json={"error": {"message": "no key"}})
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "get", get)
    result = CliRunner().invoke(doctor_cmd, ["--endpoint", "https://api.openai.com/v1", "--json"])
    check = _endpoint_check(result)
    assert check["status"] == "ok", check


def test_doctor_joins_models_onto_the_path_and_keeps_the_query(monkeypatch) -> None:
    """Appending ``/models`` to the string put it after the query, mangling a signed gateway URL."""
    import httpx

    seen: list[str] = []

    def get(url: str, **kwargs: object) -> httpx.Response:
        seen.append(url)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(httpx, "get", get)
    result = CliRunner().invoke(doctor_cmd, ["--endpoint", "http://h/v1?sig=x", "--json"])
    assert result.exit_code == 0, result.output
    assert seen == ["http://h/v1/models?sig=x"]


def test_a_malformed_endpoint_is_a_usage_error() -> None:
    """A URL no HTTP client can parse is a bad argument (USAGE, exit 2), never 'INTERNAL: this is a bug'.

    ``urlsplit`` refuses an unmatched IPv6 bracket with a bare ``ValueError`` and ``httpx.URL`` an invalid port
    with ``httpx.InvalidURL`` -- not an ``HTTPError``, so the probe's handler missed it; either escaped the probe
    and the command exited 1. One form suffices: the other takes the same path.
    """
    result = CliRunner().invoke(doctor_cmd, ["--endpoint", "http://[::1", "--json"])

    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "USAGE"
    assert "--endpoint" in error["message"]


def test_doctor_reports_a_keyless_401_as_reachable(monkeypatch) -> None:
    """A 401 with no key sent is 'reachable, needs a key', not a failed endpoint: the hint itself assumed a
    key had been sent."""
    import httpx

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(401, json={"error": {}}))
    result = CliRunner().invoke(doctor_cmd, ["--endpoint", "https://api.openai.com/v1", "--json"])
    check = _endpoint_check(result)
    assert check["status"] != "fail", check
    assert "key" in check["detail"], check


def test_doctor_reports_the_alternative_key_variables() -> None:
    """The credential check listed one variable per vendor while the adapters accept an alternative (a
    working setup showed 'missing')."""
    from rcp.inference.adapters.embeddings import CohereEmbeddings, GeminiEmbeddings

    reported = _reported_variables()
    assert set(CohereEmbeddings.API_KEY_ENV) <= reported
    assert set(GeminiEmbeddings.API_KEY_ENV) <= reported


def test_the_report_is_built_from_the_librarys_doctor_facts() -> None:
    """The facts are one record (:class:`configkit.commands.DoctorFacts`): the report names every distribution it
    asked for (a missing one as ``None``), the installed versions come from the record, and the directories and
    credential variables are the record's."""
    facts = _facts()
    report = execute(command_specs()["doctor"], {}).result

    assert report.cache_dir == str(facts.cache_dir) and report.runs_dir == str(facts.runs_dir)
    assert report.versions == {dist: facts.distributions.get(dist) for dist in report.versions}
    reported = [check.name.removeprefix("$") for check in report.checks if check.name.startswith("$")]
    assert reported == list(facts.credential_variables)
    assert facts.pinned_sibling is None  # the product compares the installed sibling, it does not read the pin


def test_the_report_names_every_exactly_pinned_sibling() -> None:
    """``cohere-rcp`` pins the core and the library exactly, and the report names both.

    A broken install of either then shows in ``rcp doctor`` -- the command a person runs before a long job finds
    out the hard way -- instead of at the first import.
    """
    manifest = tomllib.loads((ROOT / "packages/rcp/pyproject.toml").read_text(encoding="utf-8"))
    pinned = {
        requirement.split("==", 1)[0]
        for requirement in manifest["project"]["dependencies"]
        if requirement.startswith("cohere-") and "==" in requirement
    }
    report = execute(command_specs()["doctor"], {}).result
    assert pinned <= set(report.versions), sorted(pinned - set(report.versions))
