"""Redacting URLs for anything that leaves the process: a log line, an error message, a record.

The product's typed doors onto the library's one rule: :func:`safe_url` for one URL (delegating to
:func:`configkit.roles.safe_url`), :func:`redact_urls` for free text that may carry some (an exception's
message).
"""

from __future__ import annotations

import re

from configkit import roles


def safe_url(url: str) -> str:
    """The form of *url* that may reach a log, an error or a record: userinfo, query and fragment stripped.

    A user may embed credentials in a URL (a documented httpx idiom, ``http://user:key@host``), and a query
    string or a fragment can carry a key too; none of them reaches a log line, an exception message or a run
    manifest (the request itself still uses the full URL). The authority and the path stay as written -- a
    bucket name is case-sensitive and an IPv6 host keeps its brackets. The rule is
    :func:`configkit.roles.safe_url`'s, and the recorded form reads the same one once ``roles.redact`` is wired
    in: the product holds no second body of it.

    Args:
        url: A URL or storage URI; a string without ``://`` (a local path) is returned unchanged.

    Returns:
        ``<scheme>://<host[:port]>[/<path>]``.
    """
    return roles.safe_url(url)


#: A URL inside free text (an exception's message): a scheme, ``://``, then everything up to whitespace, a
#: quote or a comma (a comma-joined URL list is several URLs, each redacted on its own).
_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>,]+")


def redact_urls(text: str) -> str:
    """*text* with every URL in it passed through :func:`safe_url`: the form of an exception's message (an
    httpx error names the request's full URL) that may reach a log, a record or a traceback.

    Args:
        text: Free text that may carry URLs.

    Returns:
        The text, each URL's userinfo, query and fragment stripped.
    """
    return _URL_IN_TEXT.sub(lambda match: safe_url(match.group(0)), text)


__all__ = ["redact_urls", "safe_url"]
