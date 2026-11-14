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

from configkit.commands import (
    Command,
    CommandTree,
    DoctorFacts,
    OptionSpec,
    Step,
    StepResults,
    Verb,
    build_parser,
    customise,
    describe,
    envelope,
    index,
    option_specs,
    run_steps,
    tool,
)
from configkit.document import (
    Applied,
    Document,
    FieldTypes,
    config_error,
    deep_merge,
    leaf_paths,
    load,
    read,
    register_field_type,
    resolve,
    resolve_reference,
    validate,
)
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
from configkit.identity import (
    SHORT_ID_LENGTH,
    Identity,
    canonical_bytes,
    combine_digests,
    freeze,
    hash_payload,
    identity_chain,
    short,
)
from configkit.overrides import (
    VALUE_ERRORS,
    Override,
    apply,
    from_env,
    merge_layers,
    parse,
    parse_value,
    pipeline,
    unset,
)
from configkit.plugins import (
    Provider,
    Registration,
    Registry,
    ValidationHook,
    lookup,
    register,
    register_from,
    registered_names,
    validation_hooks,
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
from configkit.schemas import (
    ArtifactHome,
    DocumentId,
    SchemaRegistry,
    SchemaTagged,
    accepted_schema_ids,
    home_of,
    registry,
)
from configkit.spec import Brand, FrozenSpec, OperationOptions, OptionSchema, frozen_mapping, frozen_sequence
from configkit.storage import RunLayout, lock, publish, read_uri, write_uri

__all__ = [
    # commands.py
    "Command",
    "Verb",
    "CommandTree",
    "DoctorFacts",
    "Step",
    "StepResults",
    "OptionSpec",
    "option_specs",
    "build_parser",
    "index",
    "describe",
    "tool",
    "envelope",
    "run_steps",
    "customise",
    # document.py
    "Document",
    "read",
    "resolve",
    "validate",
    "load",
    "deep_merge",
    "Applied",
    "config_error",
    "FieldTypes",
    "register_field_type",
    "leaf_paths",
    "resolve_reference",
    # faults.py
    "Fault",
    "FAULT_CODES",
    "register_fault_codes",
    "exit_codes",
    "WARNING_CODES",
    "warn",
    "classify",
    "require",
    # identity.py
    "SHORT_ID_LENGTH",
    "Identity",
    "canonical_bytes",
    "hash_payload",
    "short",
    "combine_digests",
    "identity_chain",
    "freeze",
    # overrides.py
    "Override",
    "parse",
    "parse_value",
    "from_env",
    "apply",
    "pipeline",
    "merge_layers",
    "unset",
    "VALUE_ERRORS",
    # plugins.py
    "Provider",
    "Registry",
    "Registration",
    "ValidationHook",
    "lookup",
    "registered_names",
    "register",
    "register_from",
    "validation_hooks",
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
    # schemas.py
    "SchemaTagged",
    "DocumentId",
    "SchemaRegistry",
    "registry",
    "accepted_schema_ids",
    "ArtifactHome",
    "home_of",
    # spec.py
    "Brand",
    "FrozenSpec",
    "frozen_mapping",
    "frozen_sequence",
    "OptionSchema",
    "OperationOptions",
    # storage.py
    "publish",
    "lock",
    "read_uri",
    "write_uri",
    "RunLayout",
]
