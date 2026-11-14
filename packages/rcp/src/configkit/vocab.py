"""The words the consumers must agree on, and the numbers the library owns -- and no consumer's word.

A vocabulary belongs here when two consumers must mean the same thing by it for an identity to be comparable: a
capability (:class:`Capability`), a task kind (:class:`TaskKind`), a metric's name and depth
(:class:`MetricKey`), a world size (:class:`WorldSize`), a digest pin (:class:`DigestPin`), a dataset reference
(:class:`DatasetRef`), the keys of an extensible mapping (:class:`KeyVocabulary`), and the key of a budget or a
gains table (:class:`BudgetKey`, :class:`GainsKey`).

A vocabulary a **consumer** declares is absent, and its absence is a rule (F5, ee-S24, et-7): ``SERVE_KEYS`` and
``DECISION_SCALE`` were module constants here, and they are gone. A library constant would make two consumers
agree by accident -- the very defect this library exists to remove -- so the consumer supplies them through its own
model, and a test asserts this module has no such name.

The numbers are the library's because the consumers treat them as library-defined (rcp-47): the **cut**, in tokens
of the shape's own tokenizer, is 32768 for every request shape the product reads (``rcp.data.text_policy``'s
``DEFAULT_MAX_TOKENS``, 2**15), and :func:`cut_for` is its one accessor. ``test_vocab.py`` pins the number: numerics
do not move by accident.

Two shapes are deliberately open. :class:`TaskKind` is a ``str`` with no member set -- requirement 7's negative
half is "it must not assume a closed task set" -- so a consumer subclasses it or names its words through its own
:class:`KeyVocabulary`. :class:`MetricKey` is a ``str`` too, so a name a caller already holds is one, and the
grammar is applied at construction rather than by every reader.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Final

from pydantic_core import core_schema

from configkit.faults import ValueErrorFault
from configkit.roles import Role
from configkit.spec import FrozenSpec

__all__ = [
    "Capability",
    "TaskKind",
    "MetricKey",
    "WorldSize",
    "DigestPin",
    "DatasetRef",
    "KeyVocabulary",
    "BudgetKey",
    "GainsKey",
    "cut_for",
]

#: The request shapes a cut is declared for: the three the product's templates render (``query``, ``document``,
#: ``pair``). A shape outside the table is refused, so a new shape is a declared decision.
_CUT_TOKENS: Final[Mapping[str, int]] = MappingProxyType({"query": 32768, "document": 32768, "pair": 32768})

#: The separator of a namespaced query id, ``<dataset>||<query_id>``: the product's ``QUERY_ID_SEP``, kept so a
#: gains key written here reads the same in a calibration's store.
_GAINS_SEPARATOR: Final[str] = "||"

#: A full SHA-256 digest, lowercase: 64 hex characters, and nothing else (et-9).
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Capability(StrEnum):
    """The strategies a model adapter declares support for (emb-train 6). The three members are fixed."""

    GRAD_CACHE = "grad_cache"
    EP = "ep"
    MULTIMODAL = "multimodal"


class TaskKind(str):
    """A task's kind: an open vocabulary, because a consumer's task set is the consumer's (et-7).

    There is no member set on purpose. ``TaskKind("retrieval")`` is a task kind, and so is any word a consumer
    invents; the words a *mapping* accepts are declared by the consumer's :class:`KeyVocabulary`, which is where a
    closed set belongs.
    """

    def __new__(cls, word: str) -> TaskKind:
        """*word* as a task kind.

        Args:
            word: The task's word (``"retrieval"``, ``"rerank"``, a consumer's own).

        Returns:
            The word, stripped, as a :class:`TaskKind`.

        Raises:
            Fault: *word* is not a non-blank string. ``code`` is ``"value_error"``.
        """
        if not isinstance(word, str) or not word.strip():
            raise ValueErrorFault(f"a task kind is a non-empty word, got {word!r}")
        return super().__new__(cls, word.strip())


class MetricKey(str):
    """A metric's name, ``name`` or ``name@k``, canonicalised once (emb-eval S17/M4).

    The four owners of a metric name in emb-eval call one grammar: the name is lowercased, the depth is an
    ``int``, and ``@0`` is the same as no cutoff -- the product's metrics read ``k=0`` as "no cutoff", so the two
    spellings are one key. Leading zeros are dropped (``@010`` is ``@10``), so a name written by two call sites
    compares equal.

    Attributes:
        name: The metric's name, lowercased, with every prefix it carries (``"compressed_1536d_ndcg"``).
        k: The cutoff, a positive ``int``, or ``None`` when the name declares no cutoff.
    """

    name: str
    k: int | None

    def __new__(cls, text: str) -> MetricKey:
        """*text* as a canonical metric key.

        Args:
            text: A metric name, ``name`` or ``name@k``.

        Returns:
            The canonical key: ``name`` or ``name@k``, lowercased, with the depth as a plain integer.

        Raises:
            Fault: *text* is not ``name`` or ``name@k`` with a non-negative integer depth and a non-blank name.
                ``code`` is ``"value_error"`` and the message states the grammar.
        """
        canonical, name, depth = _split_metric(text)
        key = super().__new__(cls, canonical)
        key.name = name
        key.k = depth
        return key

    @classmethod
    def parse(cls, text: str) -> MetricKey:
        """The canonical key for *text*; the same rule as construction, named for a call site that parses.

        Args:
            text: A metric name, ``name`` or ``name@k``.

        Returns:
            The canonical :class:`MetricKey`.

        Raises:
            Fault: *text* is not a metric name (see :meth:`__new__`).
        """
        return cls(text)


class WorldSize(int):
    """A distributed world size: the number of ranks, at least one (et-5).

    The validator is the type's, so a model that declares ``world_size: WorldSize`` cannot hold ``0`` or a
    negative rank count, and the refusal reaches the reader as the library's own fault.
    """

    def __new__(cls, size: int) -> WorldSize:
        """*size* as a world size.

        Args:
            size: The number of ranks, an ``int`` of at least one.

        Returns:
            The size, as a :class:`WorldSize`.

        Raises:
            Fault: *size* is not an ``int``, or is below one. ``code`` is ``"value_error"``.
        """
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueErrorFault(f"a world size is an int, got {type(size).__name__}: {size!r}")
        if size < 1:
            raise ValueErrorFault(f"a world size is at least 1, got {size}")
        return super().__new__(cls, size)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        """The pydantic schema of the type: an int, validated by this class (so a model field is typed)."""
        del source_type, handler
        return core_schema.no_info_after_validator_function(cls, core_schema.int_schema())


class DigestPin(str):
    """A pinned digest: a full SHA-256 digest, 64 lowercase hex characters (et-9).

    The type exists so a pin cannot be a prefix, a truncated id or an upper-case spelling -- each of which reads
    as a pin and matches nothing.
    """

    def __new__(cls, digest: str) -> DigestPin:
        """*digest* as a pin.

        Args:
            digest: A full SHA-256 digest, 64 lowercase hex characters.

        Returns:
            The digest, as a :class:`DigestPin`.

        Raises:
            Fault: *digest* is not 64 lowercase hex characters. ``code`` is ``"value_error"``.
        """
        if not isinstance(digest, str) or not _DIGEST.match(digest):
            raise ValueErrorFault(
                f"a digest pin is 64 lowercase hex characters (a full SHA-256 digest), got {digest!r}"
            )
        return super().__new__(cls, digest)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        """The pydantic schema of the type: a string, validated by this class (so a model field is typed)."""
        del source_type, handler
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


class DatasetRef(FrozenSpec):
    """One dataset reference: scheme, repository, subset and revision (rcp-25).

    Five independent parses of a dataset URI exist in the product (``data/dataset.py``, ``data/io/hub.py``,
    ``data/io/mteb_task.py``, ``data/revisions.py``); this is the one record they produce. A Hub reference is
    ``hf://<owner>/<repo>[/<subset>][@<revision>]``, and a reader scheme's is ``<scheme>:<name>``.

    Attributes:
        scheme: The URI's scheme (``"hf"``, ``"beir"``, ``"mteb"``), lower case.
        repo: The repository (``"owner/repo"``) or the named dataset.
        subset: The subset (a Hub config), or ``None``.
        revision: The revision (a commit, tag or branch), or ``None`` when the source names none.
    """

    IDENTITY_ROLES: ClassVar[dict[str, Any]] = {
        "scheme": Role.CONTENT,
        "repo": Role.CONTENT,
        "subset": Role.CONTENT,
        "revision": Role.CONTENT,
    }

    scheme: str
    repo: str
    subset: str | None = None
    revision: str | None = None

    @classmethod
    def parse(cls, uri: str) -> DatasetRef:
        """The reference *uri* names.

        Args:
            uri: A dataset URI: ``hf://<owner>/<repo>[/<subset>][@<revision>]`` or ``<scheme>:<name>``.

        Returns:
            The :class:`DatasetRef` the URI names.

        Raises:
            Fault: the URI has no scheme, a Hub URI has one path part or three-plus, or a non-Hub URI carries
                ``//``. ``code`` is ``"value_error"``.
        """
        scheme, separator, rest = uri.partition(":") if isinstance(uri, str) else ("", "", "")
        if not separator or not rest.strip():
            raise ValueErrorFault(
                f"a dataset reference is '<scheme>:<name>', got {uri!r}",
                hint="write 'hf://owner/repo', 'beir:scifact' or another scheme the reader declares",
            )
        if scheme != "hf":
            if rest.startswith("//"):
                raise ValueErrorFault(f"{uri!r}: only an 'hf://' reference names a repository path")
            return cls(scheme=scheme, repo=rest)
        if not rest.startswith("//"):
            raise ValueErrorFault(f"a Hub dataset reference starts with 'hf://', got {uri!r}")
        path, _, revision = rest[2:].partition("@")
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) not in (2, 3):
            raise ValueErrorFault(f"a Hub dataset is 'hf://<owner>/<repo>[/<subset>]', got {uri!r}")
        return cls(
            scheme=scheme,
            repo="/".join(parts[:2]),
            subset=parts[2] if len(parts) == 3 else None,
            revision=revision or None,
        )

    def as_uri(self) -> str:
        """The URI this reference is written as.

        Returns:
            ``hf://<repo>[/<subset>][@<revision>]`` for a Hub reference, ``<scheme>:<repo>`` otherwise.

        Raises:
            Fault: a non-Hub reference carries a subset or a revision, which its scheme cannot spell.
        """
        if self.scheme == "hf":
            path = self.repo if self.subset is None else f"{self.repo}/{self.subset}"
            return f"hf://{path}" + (f"@{self.revision}" if self.revision else "")
        if self.subset is not None or self.revision is not None:
            raise ValueErrorFault(f"a {self.scheme!r} reference takes no subset and no revision")
        return f"{self.scheme}:{self.repo}"


@dataclass(frozen=True)
class KeyVocabulary:
    """The keys one extensible mapping declares, and the refusal of an unknown one (emb-train 4).

    ``ModelSpec.config``, ``RuntimeSpec.extra``, ``DataSpec.filters`` and the objective knobs are four mappings
    that accept free-form keys; each declares its vocabulary here, so an unknown key is refused with the mapping
    named and the known keys listed, instead of being carried into an identity nobody can read.

    Attributes:
        name: The mapping the vocabulary is declared for (``"model.config"``), for the messages.
        keys: The accepted keys, in declaration order, each once.
    """

    name: str
    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse a declaration nobody can check: a blank name, no key, a blank key, or a key twice.

        Raises:
            Fault: the declaration is malformed. ``code`` is ``"value_error"`` and the message names the field.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueErrorFault(f"a KeyVocabulary needs a non-empty name, got {self.name!r}")
        keys = tuple(self.keys)
        if not keys:
            raise ValueErrorFault(f"{self.name}: a KeyVocabulary declares at least one key")
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ValueErrorFault(f"{self.name}: every key is a non-empty string, got {keys!r}")
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueErrorFault(f"{self.name}: key(s) declared twice: {repeated}")
        object.__setattr__(self, "keys", keys)

    def check(self, mapping: Mapping[str, Any], *, where: str) -> None:
        """Refuse *mapping* when it holds a key this vocabulary does not declare.

        Args:
            mapping: The mapping a document declared.
            where: What is being checked, for the message (a field path such as ``"model.config"``).

        Raises:
            Fault: the mapping holds an undeclared key. ``code`` is ``"value_error"``; ``details`` carries
                ``where``, ``unknown`` and ``known``.
        """
        unknown = sorted(set(mapping) - set(self.keys))
        if unknown:
            raise ValueErrorFault(
                f"{where}: {self.name} does not declare {', '.join(repr(key) for key in unknown)}; "
                f"known keys: {', '.join(sorted(self.keys))}",
                details={"where": where, "unknown": unknown, "known": sorted(self.keys)},
                hint=f"rename the key, or add it to the {self.name} vocabulary",
            )


@dataclass(frozen=True)
class BudgetKey:
    """The key of one budget: a request shape and a role (rcp-18).

    A role's budget is per shape -- a query's cap and a document's are two numbers -- so the table is keyed by the
    pair, and a role that declares no budget for a shape has no entry rather than a default.

    Attributes:
        shape: The request shape the budget applies to (``"query"``, ``"document"``, ``"pair"``).
        role: The role the budget belongs to (a judge, a reranker, an embedder).
    """

    shape: str
    role: str

    def __post_init__(self) -> None:
        """Refuse a blank shape or role, which names no entry in any table.

        Raises:
            Fault: either half is blank. ``code`` is ``"value_error"``.
        """
        for field, value in (("shape", self.shape), ("role", self.role)):
            if not isinstance(value, str) or not value.strip():
                raise ValueErrorFault(f"a BudgetKey needs a non-empty {field}, got {value!r}")


class GainsKey(str):
    """The key of a gains table: a query id, namespaced ``<dataset>||<query_id>`` when the suite's parts share
    ids (rcp-47).

    The gains of one dataset scored alone need no name -- the bare query id is the key. A suite whose parts share
    query ids needs one, so the dataset is prefixed with the product's ``||`` separator and two datasets' ``q1``
    cannot collide in one fit.

    Attributes:
        dataset: The dataset the query belongs to, or ``None`` for the bare form.
        query: The query's id, as the dataset spells it.
    """

    dataset: str | None
    query: str

    def __new__(cls, text: str) -> GainsKey:
        """*text* as a gains key.

        Args:
            text: A bare query id, or ``<dataset>||<query_id>``.

        Returns:
            The key, with its two halves parsed.

        Raises:
            Fault: *text* is blank, names no query, names no dataset, or carries two separators. ``code`` is
                ``"value_error"``.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueErrorFault(f"a gains key is a query id, got {text!r}")
        parts = text.split(_GAINS_SEPARATOR)
        if len(parts) > 2:
            raise ValueErrorFault(
                f"a gains key is '<query_id>' or '<dataset>{_GAINS_SEPARATOR}<query_id>', got {text!r}"
            )
        dataset, query = (None, parts[0]) if len(parts) == 1 else (parts[0], parts[1])
        if not query.strip():
            raise ValueErrorFault(f"a gains key names a query, got {text!r}")
        if dataset is not None and not dataset.strip():
            raise ValueErrorFault(f"a namespaced gains key names a dataset, got {text!r}")
        key = super().__new__(cls, text if dataset is None else f"{dataset}{_GAINS_SEPARATOR}{query}")
        key.dataset = dataset
        key.query = query
        return key

    @classmethod
    def of(cls, dataset: str | None, query: str) -> GainsKey:
        """The key for *query*, namespaced by *dataset* when one is given.

        Args:
            dataset: The dataset the query belongs to, or ``None`` for the bare form.
            query: The query's id.

        Returns:
            The :class:`GainsKey`.

        Raises:
            Fault: *query* is blank, or *dataset* is given and blank.
        """
        return cls(query if dataset is None else f"{dataset}{_GAINS_SEPARATOR}{query}")


def cut_for(shape: str) -> int:
    """The default cut of one request shape, in tokens of that shape's tokenizer (rcp-47).

    Args:
        shape: The request shape (``"query"``, ``"document"`` or ``"pair"``).

    Returns:
        The cut, in tokens: 32768 for every shape the product reads (2**15, ``rcp.data.text_policy``'s
        ``DEFAULT_MAX_TOKENS``).

    Raises:
        Fault: *shape* is not a declared shape. ``code`` is ``"value_error"`` and ``details["shapes"]`` lists the
            declared ones, so a new shape is a declared decision rather than a silent default.
    """
    try:
        return _CUT_TOKENS[shape]
    except KeyError:
        raise ValueErrorFault(
            f"no cut is declared for the request shape {shape!r}; shapes: {sorted(_CUT_TOKENS)}",
            details={"shape": shape, "shapes": sorted(_CUT_TOKENS)},
        ) from None


def _split_metric(text: str) -> tuple[str, str, int | None]:
    """``(canonical, name, depth)`` for a metric name, refusing anything but ``name`` or ``name@k``.

    Raises:
        Fault: the text is blank, names no metric, carries two ``@``, or carries a depth that is not a
            non-negative integer. ``code`` is ``"value_error"``.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueErrorFault(f"a metric name is 'name' or 'name@k', got {text!r}")
    if text.count("@") > 1:
        raise ValueErrorFault(f"a metric name is 'name' or 'name@k', with one '@', got {text!r}")
    name, _, depth_text = text.partition("@")
    name = name.strip().lower()
    if not name:
        raise ValueErrorFault(f"a metric name is 'name' or 'name@k': the name before '@' is blank in {text!r}")
    if not depth_text and "@" not in text:
        return name, name, None
    if not depth_text.isdigit():
        raise ValueErrorFault(
            f"a metric name is 'name' or 'name@k', and k is a non-negative integer: got {depth_text!r} in {text!r}"
        )
    depth = int(depth_text)
    if depth == 0:  # the product's metrics read k=0 as "no cutoff", so the two spellings are one key
        return name, name, None
    return f"{name}@{depth}", name, depth
