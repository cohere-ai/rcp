"""configkit: the shared configuration and command-line library.

The library imports the standard library and ``pydantic`` and nothing else. It imports no module of the
product that ships it (``rcp``, ``rcp_core``, ``rcp_vllm``), uses no relative import, reads no file and no
environment variable at import time, and imports no YAML reader until a YAML document is read. Two proofs keep
that true: ``packages/rcp/tests/configkit/test_isolation.py`` (an AST scan of every file at every scope, a
run-time ``sys.modules`` probe and an extracted-copy probe) and the per-card import-closure command in the
implementation plan.

``__all__`` is the published surface: one block per module, in the module tree's order, appended by each
module's card. At this card no module has landed, so it is empty.
"""

__all__: list[str] = []
