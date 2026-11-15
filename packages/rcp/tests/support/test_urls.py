"""The product's redactor is the library's one rule, and both doors read the same corpus.

``rcp.support.urls.safe_url`` delegates to :func:`configkit.roles.safe_url` (the carve-out left the library the
rule's only body), and the library's suite cannot import a consumer: the parity rows live here, where both
implementations are importable.
"""

from __future__ import annotations

from typing import Any

import pytest
from configkit.roles import safe_url


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


#: The product's redaction corpus (``packages/rcp/tests/storage/test_storage.py``), run through both doors:
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
