"""configkit: the shared configuration and command-line library.

The library imports the standard library and ``pydantic`` and nothing else. It imports no module of the
product that ships it (``rcp``, ``rcp_core``, ``rcp_vllm``), uses no relative import, reads no file and no
environment variable at import time, and imports no YAML reader until a YAML document is read. Two proofs keep
that true: ``packages/rcp/tests/configkit/test_isolation.py`` (an AST scan of every file at every scope, a
run-time ``sys.modules`` probe and an extracted-copy probe) and the per-card import-closure command in the
implementation plan.

``__all__`` is the published surface: one block per module, in the module tree's order, added by each module's
card and never reordered. ``rcp`` imports ``from configkit.<module>``; it re-exports none of these names.
"""

from configkit.faults import (
    FAULT_CODES,
    WARNING_CODES,
    Fault,
    classify,
    exit_codes,
    register_fault_codes,
    require,
    warn,
)
from configkit.ports import (
    AtomicWriter,
    Budget,
    Clock,
    ExitCodeTable,
    Ports,
    Renderer,
    Sink,
    Tensor,
    UriIO,
    configure,
    logger,
    once,
)
from configkit.roles import Role, identity_of, local_inputs, path_fields, redact, roles_of
from configkit.spec import Brand, FrozenSpec, OperationOptions, OptionSchema, frozen_mapping, frozen_sequence

__all__ = [
    # faults.py
    "Fault",
    "FAULT_CODES",
    "register_fault_codes",
    "exit_codes",
    "WARNING_CODES",
    "warn",
    "classify",
    "require",
    # ports.py
    "Clock",
    "Budget",
    "Sink",
    "ExitCodeTable",
    "Renderer",
    "AtomicWriter",
    "UriIO",
    "Tensor",
    "Ports",
    "logger",
    "configure",
    "once",
    # roles.py
    "Role",
    "roles_of",
    "identity_of",
    "path_fields",
    "redact",
    "local_inputs",
    # spec.py
    "Brand",
    "FrozenSpec",
    "frozen_mapping",
    "frozen_sequence",
    "OptionSchema",
    "OperationOptions",
]
