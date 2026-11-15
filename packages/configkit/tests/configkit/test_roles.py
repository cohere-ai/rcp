"""The role vocabulary and the four behaviours that derive from it.

The declaration mechanism is the one ``rcp`` ships today: ``IDENTITY_ROLES`` on the model, merged down the MRO,
with a plain ``str`` coerced to a :class:`Role`. The identity payload keeps its four rules (nested models
recurse, ``None`` is omitted, ``extra="allow"`` keys are CONTENT, the declarations are checked at every level),
and each rule has one pinned payload here, because a flat CONTENT walk would move every consumer's key.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from configkit.faults import Fault
from configkit.roles import Role, identity_of, local_inputs, path_fields, redact, roles_of, safe_url
from pydantic import BaseModel, ConfigDict, Field

from rcp.support.resources import REDACTED


class _EveryRole(BaseModel):
    """One field per role: the vocabulary's seven members, all declared."""

    content: str
    runtime: int
    path: str
    uri: str
    secret: str
    env: str
    output: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "content": Role.CONTENT,
        "runtime": Role.RUNTIME,
        "path": Role.PATH,
        "uri": Role.URI,
        "secret": Role.SECRET,
        "env": Role.ENV,
        "output": Role.OUTPUT,
    }


class _Inner(BaseModel):
    """A nested model: one CONTENT field, one RUNTIME field."""

    weight: int
    endpoint: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"weight": Role.CONTENT, "endpoint": Role.RUNTIME}


class _Outer(BaseModel):
    """A model that nests another model as CONTENT."""

    name: str
    inner: _Inner

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"name": Role.CONTENT, "inner": Role.CONTENT}


class _Optional(BaseModel):
    """A CONTENT field and an unset optional CONTENT field."""

    present: str
    absent: str | None = None

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"present": Role.CONTENT, "absent": Role.CONTENT}


class _Open(BaseModel):
    """A model that receives keys it does not declare."""

    model_config = ConfigDict(extra="allow")

    name: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"name": Role.CONTENT}


class _BrokenInner(BaseModel):
    """A nested model with a field nobody classified."""

    classified: str
    unclassified: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"classified": Role.CONTENT}


class _OuterWithBrokenInner(BaseModel):
    """A complete outer declaration around an incomplete inner one."""

    inner: _BrokenInner

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"inner": Role.CONTENT}


class _MissingRole(BaseModel):
    """A model with one field and no declaration for a second."""

    declared: str
    undeclared: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"declared": Role.CONTENT}


class _StaleRole(BaseModel):
    """A model whose declaration names a field it no longer has."""

    only: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"only": Role.CONTENT, "gone": Role.RUNTIME}


class _Bare(BaseModel):
    """A model with no declaration at all."""

    value: int


class _StringRole(BaseModel):
    """A declaration written as a plain string, the spelling ``rcp.data.templates`` uses."""

    fixed: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"fixed": "content"}


class _BaseSpec(BaseModel):
    """The base of a declaration hierarchy."""

    a: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"a": Role.CONTENT}


class _ChildSpec(_BaseSpec):
    """A subclass that adds a field and declares only that one."""

    b: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"b": Role.RUNTIME}


class _RedeclaredSpec(_BaseSpec):
    """A subclass that re-declares an inherited field."""

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"a": Role.RUNTIME}


class _PathInner(BaseModel):
    """A nested model with a PATH field and a RUNTIME one."""

    checkpoint: str
    note: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"checkpoint": Role.PATH, "note": Role.RUNTIME}


class _Paths(BaseModel):
    """Two PATH leaves, one URI field and a nested PATH leaf."""

    source: str
    output_dir: str
    remote: str
    nested: _PathInner

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "source": Role.PATH,
        "output_dir": Role.PATH,
        "remote": Role.URI,
        "nested": Role.CONTENT,
    }


class _RecordedInner(BaseModel):
    """A nested model with a URI field, so redaction is proved to recurse."""

    endpoint: str
    checkpoint: str

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {"endpoint": Role.URI, "checkpoint": Role.PATH}


class _Recorded(BaseModel):
    """One field per redacted role, an alias, and a nested model."""

    name: str
    token: str = Field(alias="token_key")
    base_url: str
    inner: _RecordedInner

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "name": Role.CONTENT,
        "token": Role.SECRET,
        "base_url": Role.URI,
        "inner": Role.CONTENT,
    }


class _Inputs(BaseModel):
    """A PATH input, a local URI, a remote URI and an environment variable."""

    data: str
    mirror: str
    remote: str
    token_env: str
    nested: _PathInner

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "data": Role.PATH,
        "mirror": Role.URI,
        "remote": Role.URI,
        "token_env": Role.ENV,
        "nested": Role.CONTENT,
    }


def test_the_vocabulary_is_the_seven_members_in_order() -> None:
    """L3.5: the seven members, and their order is the published order."""
    assert [role.value for role in Role] == ["content", "runtime", "path", "uri", "secret", "env", "output"]


def test_a_string_declaration_is_coerced() -> None:
    """The declaration mechanism rcp ships: a plain ``str`` value is a role."""
    assert roles_of(_StringRole) == {"fixed": Role.CONTENT}


def test_declarations_merge_down_the_mro() -> None:
    """A subclass adds fields and may re-declare an inherited one; the most derived declaration wins."""
    assert roles_of(_ChildSpec) == {"a": Role.CONTENT, "b": Role.RUNTIME}
    assert roles_of(_RedeclaredSpec) == {"a": Role.RUNTIME}


def test_undeclared_field_raises() -> None:
    """A2: a field nobody classified is refused, and the message names it."""
    with pytest.raises(Fault) as caught:
        roles_of(_MissingRole)
    assert caught.value.code == "value_error"
    assert "undeclared" in caught.value.message
    assert "IDENTITY_ROLES" in caught.value.message


def test_a_stale_declaration_raises() -> None:
    """A declaration for a field the model no longer has is a record of a model that no longer exists."""
    with pytest.raises(Fault, match="gone"):
        roles_of(_StaleRole)


def test_a_model_with_no_declaration_raises() -> None:
    """A model that feeds an identity must declare one role per field."""
    with pytest.raises(Fault, match="declares no IDENTITY_ROLES"):
        roles_of(_Bare)


def test_identity_payload_is_content_only() -> None:
    """A3: the payload is CONTENT and none of the other six roles."""
    model = _EveryRole(content="c", runtime=1, path="/p", uri="u", secret="s", env="E", output="/o")
    payload = identity_of(model)
    assert payload == {"content": "c"}
    for absent in ("runtime", "path", "uri", "secret", "env", "output"):
        assert absent not in payload


def test_identity_nested_model_recurses() -> None:
    """Rule 1: a nested model is walked by the same rule, so a runtime knob cannot reach a key by being nested."""
    payload = identity_of(_Outer(name="n", inner=_Inner(weight=2, endpoint="e")))
    assert payload == {"name": "n", "inner": {"weight": 2}}


def test_identity_none_is_omitted() -> None:
    """Rule 2: an unset optional CONTENT field does not re-key a run that does not set it."""
    assert identity_of(_Optional(present="p")) == {"present": "p"}
    assert identity_of(_Optional(present="p", absent="a")) == {"present": "p", "absent": "a"}


def test_identity_extra_keys_are_content() -> None:
    """Rule 3: a key an ``extra="allow"`` model received is CONTENT."""
    assert identity_of(_Open(name="n", surprise="s")) == {"name": "n", "surprise": "s"}


def test_check_declarations_runs_at_each_level() -> None:
    """Rule 4: an undeclared field inside a nested model is refused too."""
    with pytest.raises(Fault, match="unclassified"):
        identity_of(_OuterWithBrokenInner(inner=_BrokenInner(classified="c", unclassified="u")))


def test_path_fields_are_the_path_leaves_nested_included() -> None:
    """The PATH enumeration the loader resolves, with a nested model's PATH leaf in it."""
    assert path_fields(_Paths) == (("source",), ("output_dir",), ("nested", "checkpoint"))


def test_redact_hides_secrets_and_url_credentials() -> None:
    """A SECRET becomes the recorded marker; a URI keeps its authority and path and loses the rest."""
    payload = {
        "name": "n",
        "token_key": "s3cr3t",
        "base_url": "https://user:pw@host:8080/v1?key=1#frag",
        "inner": {"endpoint": "https://host/v1", "checkpoint": "/checkpoints/x"},
    }
    redacted = redact(payload, model=_Recorded)
    assert redacted == {
        "name": "n",
        "token_key": REDACTED,
        "base_url": "https://host:8080/v1",
        "inner": {"endpoint": "https://host/v1", "checkpoint": "/checkpoints/x"},
    }
    assert payload["token_key"] == "s3cr3t"


def test_redact_redacts_a_key_the_model_does_not_declare() -> None:
    """H8: an ``extra="allow"`` key is CONTENT to :func:`identity_of`, so it may hold a secret and is redacted.

    The walk kept every undeclared key unchanged ("its role is unknown"), which rendered a secret the model
    received through ``extra="allow"`` -- the same keys ``identity_of`` treats as part of the identity.
    """
    payload = {"name": "n", "token_key": "s", "base_url": "u", "inner": {}, "extra": "s3cr3t"}
    assert redact(payload, model=_Recorded)["extra"] == REDACTED
    assert redact(payload, model=_Recorded)["name"] == "n"


def test_safe_url_is_public_and_strips_a_credentials_and_query() -> None:
    """H6: the rule was byte-identical to the product's ``safe_url`` but private, so the product kept a copy."""
    from configkit import roles

    assert "safe_url" in roles.__all__
    assert safe_url("https://user:token@host.example.com/a/b?x=1#f") == "https://host.example.com/a/b"
    assert safe_url("https://host.example.com") == "https://host.example.com"
    assert safe_url("/local/path") == "/local/path"
    assert safe_url(["https://u:p@h/x?q=1"]) == ["https://h/x"]


def test_the_products_safe_url_is_the_librarys_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """CP-2: the product's redactor delegates, so the library holds the rule's only body.

    The two bodies were byte-identical and no test pinned one against the other: a change to either alone was
    silent. The product's call must reach :func:`configkit.roles.safe_url` -- the rule the recorded form will
    read once ``roles.redact`` is wired in -- and this is the test that reddens if the copy comes back.
    """
    from configkit import roles

    from rcp.support import urls

    calls: list[Any] = []

    def recording(value: Any) -> Any:
        calls.append(value)
        return "delegated"

    monkeypatch.setattr(roles, "safe_url", recording)
    assert urls.safe_url("https://u:p@h/x?q=1#f") == "delegated"
    assert calls == ["https://u:p@h/x?q=1#f"]


#: The product's redaction corpus (``packages/rcp/tests/storage/test_storage.py``), run through both bodies:
#: userinfo, a query key, a fragment, an IPv6 authority, a case-sensitive bucket, a fake scheme and a local path.
_SAFE_URL_CORPUS = [
    ("http://user:sekrit-value@judge.test/v1", "http://judge.test/v1"),
    ("http://user:sekrit-value@judge.test/v1/models?api-key=sekrit-value", "http://judge.test/v1/models"),
    ("https://host.test/v1#access_token=sekrit-value", "https://host.test/v1"),
    ("http://[::1]:8000/v1?key=sekrit-value", "http://[::1]:8000/v1"),
    ("gs://My-Bucket/Key.jsonl", "gs://My-Bucket/Key.jsonl"),
    ("fake://seed/1", "fake://seed/1"),
    ("/a/local/path?x", "/a/local/path?x"),
]


@pytest.mark.parametrize(("url", "safe"), _SAFE_URL_CORPUS)
def test_the_product_and_the_library_strip_the_same_corpus(url: str, safe: str) -> None:
    """CP-2: one corpus, both doors: a change to either body alone reddens this row."""
    from rcp.support.urls import safe_url as product_safe_url

    assert safe_url(url) == product_safe_url(url) == safe


def test_local_inputs_lists_paths_local_uris_and_environment_names() -> None:
    """A PATH row, a local URI row, the variable an ENV field names, and no remote URI."""
    model = _Inputs(
        data="/data/corpus",
        mirror="file:///data/mirror",
        remote="hf://org/repo",
        token_env="HF_TOKEN",
        nested=_PathInner(checkpoint="/checkpoints/x", note="n"),
    )
    assert local_inputs(model) == (
        "data: /data/corpus",
        "mirror: file:///data/mirror",
        "token_env: $HF_TOKEN",
        "nested.checkpoint: /checkpoints/x",
    )
