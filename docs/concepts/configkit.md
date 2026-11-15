# PROPOSAL — one configuration and command library, its own distribution, extractable to `emb-utils`

Status: **proposal, revision r2 of round 2.** Nothing implemented. Round 3 of review follows.

## What changed in revision r1

| Change | Why |
|---|---|
| The library is its own distribution, `packages/configkit/`, import name `configkit`. It is no longer a subpackage of `rcp`. | `rcp/__init__.py:51-53` is eager, and `import rcp` pulls numpy, yaml and rcp_core (measured, §Placement). A subpackage can never satisfy C1. |
| Fifteen modules, not seven. Each new module is named by a numbered requirement. | 59 of the 102 requirement rows had no home (review 1, §1–3). §Coverage maps every row. |
| One pipeline: read → resolve → apply → validate → identity, as one call. | Round 1 said `apply` runs "after `load` and before any validation", and `load` validated. Both reviewers failed it. |
| The four isolation proofs are rewritten and each is runnable, with measured expected output. | Three of the four round-1 commands could not fail, or could not pass. |
| `commands.py` carries the result model, the output schema, `prog`, `brand`, the envelope schema id and the exit-code set as parameters. | R6 asks for request/**result** models; round 1 declared the request only. |
| A click adapter lives in `rcp`, not in the library. | rcp is click (`cli/command.py:32`); `click` is third-party, so C1 forbids it inside the library. |

## What changed in revision r2

| Change | Why |
|---|---|
| C1 is restated as an import-time condition: stdlib plus `pydantic` at import, PyYAML a lazy optional extra. | Round 2 found a lazy `import yaml` inside the distribution that C1 as written forbade (review 1, M1). |
| The manifest-level C2 argument is deleted. C2 is proved at run time and in a subprocess whose `sys.path` excludes the rcp checkout. | The workspace installs every member editable into one venv, so `import rcp` succeeds inside `configkit` (review 1, M2; measured). |
| `audit.py` carries two functions: `audit(*roots)` for the model graph, `scan(path)` for source facts. | Six coverage rows named an `audit` check that a model walk cannot make (review 1, M12), and two rows had no mechanism at all (review 1, B3). |
| P3 records every `open` with no root filter, and records environment reads with an `os.environ` proxy and an `os.getenv` wrapper. | No audit event exists for an environment read; the round-2 hook saw only `os.putenv` (review 1, B1; review 2, F1; measured). |
| P4 copies `src/configkit`, imports from `/tmp`, and adds a negative control. | A copy of the distribution directory imports as a namespace package, so `__file__` is `None` and the code never runs (review 1, B2; review 2, F2; measured). |
| `WARNING_CODES` is declared on the record that carries it. | rcp requirement 31 puts the set on the record, not on the consumer (review 1, M3). |
| `TaskKind` is open; a consumer registers its tasks. | emb-train 7's negative half forbids a closed task set (review 1, M4). |
| `Command.human_lines`, `build_parser(render=, exit_help=)`, `DoctorFacts`, `MetricKey` and `SERVE_KEYS` are in the APIs. | Three coverage rows named a symbol the module did not contain, and two signatures lacked a parameter the requirement names (review 1, M6, M7, m1). |
| The absolute-path rule matches the current loader: an absolute path is kept. | The round-2 rule refused every emb-train CLI fixture (review 1, M8; `emb_train_cli/config.py:111`). |
| The value parser is `yaml.safe_load` on the stripped text, exactly as `support/config.py:69`. | The round-2 restricted ladder changed `key=` and `yes` (review 1, M10). |
| `ports.py` names `AtomicWriter` and `UriIO`, and `storage.py` takes `ports`. | The library's own first consumer of the seam could not build against it (review 2, F4). |
| `validation_hooks(store)`, `registry(documents)` and `accepted_schema_ids(document)` take a parameter. | A parameterless lookup implies the module global the module table forbids (review 2, F5). |
| P5 and P6 move to the consumer suites, with one shared contract test. | A library test double cannot fail because a consumer projects (review 1, M9). |
| The digest names its successor; emb-eval X1 joins the coverage table; two numbers are corrected. | Review 2, F7, F8, F9; review 1, m7. |
| Fourteen minor citations, symbols and counts are corrected. | Review 1, m1–m10; review 2, F6–F9. |

## The goal, in the operator's words

> *"(a) in a way that it can live in `rcp` but easily be factored out to `/root/repos/emb-utils/` if we ever wanted.
> It should be extremely well isolated while catering to all requirements carefully."*

So: **one library, three consumers, and an extraction path that is a directory move.**

## The requirements, gathered and not asserted

```
rcp        157 findings kept · 50 distinct requirements · 9 cross-folder seams
emb-train   33 distinct requirements
emb-eval    25 structural findings · 45 major · 147 minor; two config doors that do not connect
```

Sources: `see the review reportsrcp-merged.md` (§The command framework, §Configuration,
§Identity and schemas, §Errors, logging, storage, plugins, §What L must not assume),
`quality5/emb-train-merged.md` (§REQUIREMENTS FOR THE SHARED LIBRARY),
the structural review reports (see the programme record).
Every row below carries its requirement number and its source document.

### The six requirements all three repositories produced, in their own words

**R1. One declared field-role vocabulary, on the model, with derived behaviour.**
`CONTENT`, `RUNTIME`, `PATH`, `URI`, `SECRET`, `ENV`, `OUTPUT`. Identity, relative-path resolution, the
local-input listing and redaction all derive from it.
- **rcp**: *"it still walks the same document by hand four times"* — `runs/config.py` walks `local_inputs:700`,
  `_relative_to:942`, `_redact_uris:854` plus seven `redact_*_payload` helpers, **although `support/identity.py:69`
  already declares `IDENTITY_ROLES` per field.**
- **emb-train**: the hard-coded `_PATH_FIELDS` tuple at `emb_train_cli/config.py:36-40` holds three rows
  (`data.sources`, `data.target_uri`, `runtime.output_dir`). `ModelSpec.checkpoint` (`emb_train_core/schema.py:137`)
  is a path and sits in no row, so the miss is invisible.

**R2. One loader that ships with the schema.** `load(path, model) -> model`.
- **emb-train**: *"the model lives in `emb_train_core/schema.py` and the reader in `emb_train_cli/config.py`. The
  shared library must ship one `load(path) -> model` so the three repositories cannot split them three ways."*
- **rcp**: *"one config grammar: extends + dotted `--set` + one value parser + declared per-field redaction."*

**R3. Overrides as structured records, parsed once.** `Override(path, value, source)`.
- **rcp**: *"three modules here re-parse the override strings to recover provenance and precedence."*
- **emb-eval**: `--compute`/`--score` change the payload **without entering the identity** (M2, `run_flags.py:408,573`),
  so an override reuses the old cache.

**R4. A completeness check that fails a test, not a run.** An audit that refuses an undeclared or stale field.
- **rcp**: *"the failure it prevents is a field added without a decision, and this repository ships that check
  today"* (`check_declarations`).

**R5. A declared fault-class → stable-code mapping**, with CLI hints resolved at output time.
- **rcp**: *"a typed error/exit-code taxonomy whose CLI hints are resolved at output time, not baked into library
  raises."*
- **emb-train**: *"a declared, order-checked fault-class→stable-code mapping with per-package ownership."*
- **emb-eval**: nine refusal families with no base; *"a caller cannot catch 'refused' once."*

**R6. A command layer driven by one spec.** One declaration → CLI flags **and** the MCP tool.
- **rcp**: *"a declarative command layer (request/result models → CLI flags + MCP tool from one spec)"*; *"today
  `--json` is declared twice and `rcp --json run list` reports `"command": "rcp"`."*

### The constraints, and every one is a measured fact

**C1. Standard library and `pydantic` at import time; PyYAML only as a lazy optional extra.** The condition is
about import time. `pydantic` is the only hard dependency. `PyYAML` is an optional extra (`configkit[yaml]`),
imported inside `parse_value` and inside `document.read` and nowhere else. A caller that passes no YAML file and
no flow collection never imports it. Measured in `/root/repos/emb-train/.venv`:

```bash
.venv/bin/python -c "import sys, emb_train_core; print(len(sys.modules))"        # 236
.venv/bin/python -c "import time,sys;t=time.perf_counter();import torch;print(len(sys.modules), round(time.perf_counter()-t,2))"
# 1231 2.62
```

One sibling import costs 236 modules and 0.26 s. One heavy import costs 1,231 modules. The module count is the
load-bearing number; the wall-clock time is one sample (2.35 s, 2.52 s and 2.62 s on three runs).
**This is the isolation condition and the extraction condition at once.**
(The round-1 figure "1,402 modules and 2.9 s" was not reproducible and is withdrawn. See F14.)

**C2. No `rcp` import anywhere in the library.** **rcp requirement 43**, verbatim. **This is the operator's condition.**

**C3. No import-time I/O and no import-time environment read.** **rcp requirement 41**: `data/dataset.py:62` reads
four YAML files at import. Measured control: an audit hook on that import reports
`rcp/data/suites/{bright,nanobeir,trecdl,vidore}.yaml`. The proof in §Isolation proof uses the same hook.

**C4. No numpy, pandas or PyYAML on the config import path.** **rcp requirement 42.** **So the YAML reader is a lazy
import inside the function that needs it.**

**C5. A seam between two packages is a public name, never an underscore path.** **rcp requirement 44**, eleven such
edges today. `audit.scan` checks the library's own tree; each consumer ships the test for its own edges (§P5).

**C6. The payload a front end prints is the library's model.** **rcp requirement 45**: the front end formats, it does
not project. Each consumer ships a renderer test (§P6).

## The extraction hazard, measured

**The library cannot be written where the code is today.** Three facts:

1. **`rcp` is an eager root.** `packages/rcp/src/rcp/__init__.py:51-54` imports `rcp.calibration`, `rcp.data`, `rcp.data.text_policy` and
   `rcp.eval`. Measured:

   ```bash
   uv run python -c "import sys, rcp; print(sorted({m.split('.')[0] for m in sys.modules} & {'numpy','yaml','torch','pandas','rcp_core'}))"
   # ['numpy', 'rcp_core', 'yaml']
   ```

   So `import rcp.<anything>` pays numpy and PyYAML before the subpackage runs one line of its own code.

2. **One module-level edge and one function-local back edge.** `support/config.py:24` imports
   `ConfigError` from `rcp.errors` at module level. `errors.py:497` imports `config_error` from
   `rcp.support.config` inside `classify`, and `errors.py`'s module-level imports are stdlib only (`:31-37`).
   This is **not an import cycle**: the back edge is function-local. Config can move first, and `classify` can
   take the mapper as an argument.

3. **Four homes with cross-imports.** *"the shared-library core already exists but is split across four homes
   (`cli/command.py`, `cli/output.py`, `support/config.py`, `errors.py`) with cross-imports, so the move to
   emb-utils only works if gathered in one pass."* The four modules import five repo names with no declared seam.

## The placement, and why it is one directory

```
packages/configkit/                     <- one distribution, its own manifest
    pyproject.toml                      name = "cohere-configkit" 0.0.1; dependencies: pydantic only
    src/configkit/                      <- import name `configkit`
        __init__.py  roles.py  document.py  overrides.py  identity.py  schemas.py
        faults.py  commands.py  ports.py  plugins.py  storage.py  vocab.py
        spec.py  audit.py
    tests/                              one test module per library module
```

It lives in the `rcp` repository, in the `rcp` workspace, under the `rcp` lock. The workspace already collects
`packages/*` (`/root/repos/rcp/pyproject.toml`: `[tool.uv.workspace] members = ["packages/*"]`), and
`[tool.uv.sources]` already names sibling distributions (`cohere-rcp-core = { workspace = true }`). rcp adds one
dependency line and one source line. The library is a sibling of `cohere-rcp-core`, which is the shape the
repository already uses.

**Three conditions follow from the placement, and each is mechanical:**

- C1 holds without a lazy `rcp/__init__.py`, because nothing imports the parent package.
- C2 is **not** a manifest fact, and the round-2 claim that it is is withdrawn. The workspace installs every
  member editable into one venv, so `import rcp` succeeds inside `configkit` with no declared dependency:

  ```bash
  ls .venv/lib/python3.12/site-packages/__editable__.cohere_rcp-0.0.1.pth
  .venv/bin/python -c "import rcp; print(rcp.__file__)"
  # /root/repos/rcp/packages/rcp/src/rcp/__init__.py
  ```

  C2 is therefore proved at run time (§P2), and by a subprocess whose `sys.path` excludes the rcp checkout. The
  manifest declares `pydantic` and no sibling, which is a second check, not the proof.
- The extraction is `git mv packages/configkit <target>/configkit` plus the target's manifest. No import line
  changes, because the import name is already `configkit`.

**The rejected alternative:** make `rcp/__init__.py` lazy. It touches the file whose job is to publish the product
surface, it leaves `import rcp.configkit` dependent on a re-export table, and it does not stop the library from
importing `rcp` elsewhere. It is a second change with its own risk, and it buys nothing the placement does not.

## The modules

| module | requirements | what it must not do |
|---|---|---|
| `roles.py` | R1; rcp 16, 27, 28, 29 | know a field name. It reads what the model declares. |
| `document.py` | R2; rcp 10, 11, 14, 19, 20, 21, 24, 25; emb-train 1, 2, 3, 4, 5, 9 | import YAML at module level, or read the environment |
| `overrides.py` | R3; rcp 12, 13, 17, 23; emb-train 2 | keep the raw string beyond the message |
| `identity.py` | rcp 27, 28, 29, 30; emb-train 13, 14 | invent a canonical form the consumers do not already produce |
| `schemas.py` | rcp 26, 30, 46; emb-train 3, 25 | own a consumer's document |
| `faults.py` | R5; rcp 5, 31, 32, 33, 34; emb-train 10, 11, 12 | bake a CLI hint or an install line into a raise |
| `commands.py` | R6; rcp 1, 2, 4, 5, 6, 7, 9; emb-train 15, 16, 17, 18 | know a verb name. It reads a spec. |
| `ports.py` | rcp 3, 39; emb-train 19, 22, 29 | build a graph, or import torch |
| `plugins.py` | rcp 21, 22, 40; emb-train 20, 21 | hold a module-global registry that a test cannot replace |
| `storage.py` | rcp 36, 37, 38; emb-train 24; emb-eval M6 | assume the caller's storage |
| `vocab.py` | rcp 18, 25, 47; emb-train 4, 5, 6, 7, 8, 9 | ship a benchmark name or a data pin |
| `spec.py` | rcp 6, 15, 50; emb-train 30; emb-eval M13 | import a service to define a type |
| `audit.py` | R4; rcp 8, 20, 44, 48; emb-eval S18, S25, X2 | run at import, or at a run. A test calls it. It reads models and source, never the network. |
| `__init__.py` | — | import anything but the thirteen above |

### `roles.py` — the one vocabulary

```python
class Role(StrEnum):
    CONTENT = "content"   # part of the identity; a change makes a different document
    RUNTIME = "runtime"   # affects how it runs, not what it is
    PATH    = "path"      # resolved against the document's directory
    URI     = "uri"       # redacted in a report
    SECRET  = "secret"    # redacted, and never written to a log
    ENV     = "env"       # a name, not a value
    OUTPUT  = "output"    # written, never read

def roles_of(model: type[BaseModel]) -> Mapping[str, Role]:
    """Roles declared as `IDENTITY_ROLES` on the class, merged down the MRO. A str value is coerced."""

def identity_of(model: BaseModel) -> Mapping[str, Any]:
    """The CONTENT payload. One definition of identity for three repositories."""

def path_fields(model: type[BaseModel]) -> tuple[tuple[str, ...], ...]
def redact(payload: Any) -> Any
def local_inputs(model: BaseModel) -> tuple[str, ...]
```

**The declaration mechanism is the one rcp ships today**, because R1 says the vocabulary lives on the model.
`declared_roles` (`support/identity.py:67-85`) merges `IDENTITY_ROLES` down the MRO and coerces a `str` value
(`data/templates.py:90` declares strings). Eighteen class-level declarations exist across thirteen modules
(measured; `rcp-merged.md` requirement 27 still says "nine", which is stale). The library keeps that shape and
widens the enum from two roles to seven.

**The identity payload keeps four rules** (`support/identity.py:126-152`, verified in the file):

1. a nested model recurses through the same role rule, so a runtime knob cannot reach a key by being nested;
2. a field whose value is `None` is omitted, so a new optional CONTENT field does not re-key every run;
3. keys an `extra="allow"` model received are CONTENT;
4. `check_declarations` runs before the walk.

A flat CONTENT walk changes every `unit_key`, every `cfg8`, every calibration fingerprint and every extension
anchor. So the library states the four rules, and it pins one known payload per rule in a test (§Tests).

**`identity_of` recurses and checks every nested model.** Round 1 moved the check to `audit(models)` alone, and a
nested model that no test listed escaped it. The walk calls `check_declarations` at each level, as the code does
today, and `audit` additionally walks the graph from a root (§`audit.py`).

**What derives from the role**, and each is a bug today: identity (`identity_of`, replacing rcp's `IDENTITY_ROLES`
walk and emb-eval's unit key); path resolution (`path_fields`, replacing `_PATH_FIELDS` at
`emb_train_cli/config.py:36-40` and its miss at `schema.py:137`); redaction (`redact`, replacing rcp's seven
`redact_*_payload` helpers); the local-input listing (`local_inputs`, replacing `local_inputs:700`).

### `document.py` — one loader, and the validation-error mapper

```python
def read(path: Path, *, schema_id: str | None = None, extends: bool = True) -> Document
def resolve(document: Document, model: type[ModelT], *,
            path_fields: Sequence[Sequence[str]] = ()) -> ModelT          # unvalidated draft
def validate(draft: ModelT) -> ModelT
def load(path: Path, model: type[ModelT], *, schema_id: str | None = None,
         path_fields: Sequence[Sequence[str]] = (), validate: bool = True) -> ModelT
def config_error(exc: ValidationError, *, model: type[BaseModel], source: str,
                 redact: bool = True) -> Fault
class ReaderOptions(BaseModel)      # the read side validates before a run starts (rcp 24)
class WriterOptions(BaseModel)      # the write side

def register_field_type(registry: FieldTypes, model_type: type[BaseModel],
                        validator: Callable[[Any], Any]) -> None      # rcp 19; no module global
class FieldTypes:                                                  # caller-owned, replaceable in a test
    def schema_hook(self, model_type: type[BaseModel]) -> Callable[..., Any]   # L composes the schema

def leaf_paths(model: type[BaseModel]) -> tuple[str, ...]
def resolve_reference(text: str, *, attempts: list[str]) -> Mapping[str, Any]
```

**The five v0 rules, from emb-train `config.py:5-18`, and what happens to each:**

| rule | in the library |
|---|---|
| UTF-8 | kept. `encoding="utf-8"`, and a decode failure is a `ConfigUnreadable` fault naming the file. |
| unique keys | kept. A duplicate key fails, with the key and the file. |
| a required `schema` equal to a declared id | kept, and the id is a **parameter** (`schema_id`). The model must not default it: `emb_train_core/schema.py:253` defaults `schema_id` today, and `audit` refuses a defaulted schema field. |
| resolve against the file's directory | kept, **and** the second base is stated (§Two-base rule). |
| resolve before hashing | kept, and enforced by the pipeline order (§The pipeline). |

**Two rules change, and the proposal says so.** Rule 1 also says "v0 reads no YAML … and no inheritance". The
library reads YAML (lazily) and honours `extends`. So the word "verbatim" is withdrawn: the library keeps five
rules and changes two. The loader still reads no environment; an environment override is an explicit call
(`overrides.from_env`), never a loader behaviour.

**Two-base path rule.** A document's relative paths resolve against the document's directory. An override's paths
resolve against the working directory. `path_fields` is derived from `Role.PATH` by default, and a caller may pass
extra rows. `audit` refuses a `Role.PATH` field that `path_fields` does not return. Both bases have a test.

**Absolute-path rule** (emb-train 2). A relative `Role.PATH` value resolves against the document's directory. An
absolute value is **kept unchanged**, because that is the rule the loader ships today
(`emb_train_cli/config.py:111`: `value if path.is_absolute() else str((base / path).resolve())`), and every
emb-train CLI fixture writes an absolute `runtime.output_dir`. A model that wants a refusal declares
`absolute_paths = False`, and `audit` reports the declaration. This is a **third** change to the five v0 rules,
and it is counted as one here. Round 2 made the refusal the default and would have refused every fixture.

**Purity rule** (rcp 20). A validator reads no file, no package data and no network. The library states the rule,
and `audit.pure(validator)` enforces it: the check runs the validator under an audit hook that refuses `open`,
`socket.*` and `subprocess.*` events. A validator that opens a file fails the check. `tests/test_audit.py`
carries one passing validator and one failing control.

**`config_error`** (rcp 14). It maps a `ValidationError` to a `Fault` carrying the message, the field path, the
expected value, the known keys of the mapping and the redacted source. `support/config.py:166` is the model for it.
Redaction calls `roles.redact`, so a `SECRET` field never reaches the message.

**`leaf_paths`** (emb-eval M22). One derived enumeration of a model's dotted leaves, replacing the five hand-typed
leaf sets at `config/resolve.py:97,121,157,178`, `config/suite.py:346`, `config/discover.py:218` and
`config/cli.py:337`. A new contract field becomes expressible with no edit.

**`resolve_reference`** (rcp 22). One resolver from a name, a path, a recipe id or a shorthand, reporting the
attempts it made. `cli/judge.py:59` and `inference/recipes.py:424` are the four callers today.

### `overrides.py` — one grammar, one ladder, one error table, one precedence

```python
@dataclass(frozen=True)
class Override:
    path: str          # dotted, the model's own leaf path
    value: object      # already parsed, by one documented parser
    source: str        # "--set", "env:<PREFIX>_METRICS", "file:dev.yaml"

def parse(pairs: Sequence[str]) -> tuple[Override, ...]
def parse_value(text: str) -> object
def from_env(environ: Mapping[str, str], *, prefix: str, model: type[BaseModel]) -> tuple[Override, ...]
def apply(draft: ModelT, overrides: Sequence[Override], *, base: Path) -> ModelT
def merge_layers(base: Mapping, overlay: Mapping, *, roles: Mapping[str, Role]) -> Mapping
def unset(draft: ModelT, path: str) -> ModelT
```

**Two things `apply` must do that round 3 found it could not.**

1. **It cannot mutate, and it does not try.** emb-train's specs are `frozen=True`
   (`packages/emb-train-core/src/emb_train_core/schema.py:132`, verified). `apply` **returns a new model** through
   `model_copy(update=...)`, and `unset` does the same. A consumer's frozen model is not a consumer's mistake; it is
   the shape a config should have.
2. **It resolves a `PATH` override against a declared base, not against whatever the process happened to use.** The
   `base` parameter is required and the caller names it: the document's directory for a document-layer override, the
   cwd only when the consumer says so. **A default would be the ambiguity that produced two `cfg8` values for one
   run.**

**One value parser, and it is the grammar rcp ships today.** `cli/run.py:188-190` publishes it: *"VALUE is a YAML
literal: 5, true, [a, b], {k: v}"*. The current code is one line, `support/config.py:69`:

```python
node[parts[-1]] = yaml.safe_load(raw) if raw.strip() else ""
```

`parse_value` does exactly that, so no existing rcp config changes meaning:

1. an empty or whitespace-only text → `""` (not `None`);
2. every other text → `yaml.safe_load(text)`, which gives `null`/`~` → `None`, `5` → `int`, `5.0` → `float`,
   `true` → `bool`, `yes`/`no`/`on`/`off` → `bool` (YAML 1.1), `[a, b]` and `{k: v}` → `list`/`dict`, and any
   other word → `str`.

`yaml` is imported inside `parse_value` (C4), and only a value that is not empty needs it. A caller that passes no
non-empty override never imports it. Round 2's restricted ladder changed two behaviours: `key=` became `None`
instead of `""`, and `yes` stayed the string `"yes"` instead of `True`. Both are withdrawn. One caller does
differ: `cli/data.py:324 _scalar` keeps `yes` as a string, so that caller changes when it adopts the library, and
`VALUE_ERRORS` records the change. `tests/test_overrides.py` pins `key=` → `""`, `yes` → `True`, `[a, b]` →
`list`, `{k: v}` → `dict`, and `5` → `int`.

**One published error table** (rcp 12): `VALUE_ERRORS` names each refusal — a dotted path that is not a leaf, a
value the ladder cannot place, a repeated path with two values, an unset path that does not exist — with its
message template and its code. The table is data, so `audit` can check it against the fault codes.

**One precedence table** (rcp 17): `file < env < flag`. `from_env` reads only names under one prefix, and the prefix
is a parameter (`Brand.prefix`). `apply` takes the merged sequence in that order, so the last write wins and the
`source` of the winning value is on the record. Round 1 named an example, `env:EMB_EVAL_METRICS`, with no order
and with a consumer's name.

**One layered merge** (rcp 23): `merge_layers` refuses a CONTENT field that one layer sets and another sets
differently, and allows a RUNTIME field to be overridden. A malformed declaration is an error, not "no declaration".

**`unset`** is a library decision, not a cited requirement. Round 1 justified `--unset` with "because rcp asked for
one", and no requirement document or rcp source uses the word (F9, review 2). The token stays because a removal is
the only structured edit the grammar cannot express; the false citation goes.

### `identity.py` — one digest, one chain

```python
SHORT_ID_LENGTH = 16          # rcp's length today; a caller passes its own
def canonical_bytes(payload: Any) -> bytes
def hash_payload(payload: Any) -> str
def short(digest: str, length: int) -> str
def combine_digests(*digests: str) -> str
def identity_chain(*, config: Mapping, data: str, model: str, run: str) -> Identity
def freeze(value: Any) -> Any
```

**The canonical form is not invented. Two repositories already produce it, and they agree byte for byte:**
`rcp_core/_hashing.py:104-113` and `emb_eval_contracts/identity.py:425-431` both call `json.dumps(..., sort_keys=True,
separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`. The library moves that function, and a
test pins it: for a fixture corpus, `hash_payload` must equal the consumer's own function, recorded before the move.

**The dependency question, answered.** The library does **not** depend on `cohere-rcp-core`, and it does not import
`rcp_core._hashing` (an underscore module of a separate distribution, `packages/rcp-core/pyproject.toml:6`). It
implements SHA-256 with `hashlib`. No stored digest changes, because the bytes are identical and the test proves it.
The short length is a parameter with a named default: rcp passes `16` at eight call sites (`calibration/fit.py:225`,
`:805`, `calibration/extend.py:140`, `:159`, `data/text_policy.py:518`, `judging/judging.py:604`, `:1576`,
`judging/reparse.py:196`); emb-eval passes `12` (`emb_eval_contracts/identity.py:631`) and `8` (`:641`) until it
migrates.

**The successor is stated, so three copies do not outlive the move** (rcp 27). After the extraction, `rcp_core`
re-exports `configkit.identity.canonical_bytes` and `hash_payload` and keeps no copy; `emb_eval_contracts.identity`
does the same. Until that re-export lands, the fixture test is the guard, and the duplication is recorded as a
test-guarded exclusion rather than left silent.

**The chain is one call** (emb-train 13): canonical JSON, SHA-256, the short length as a named constant, the config
hash, the data fingerprint, the model identity and the run id. `emb_train_core/ids.py:68` and
`record.py:48-95` are the shape.

**A run id composes tags as a named operation** (emb-train 14), and **an identity field is derived, never defaulted**
(rcp 28): `audit` refuses a `CONTENT` field with a default.

**`freeze` deep-freezes an identity artifact** (rcp 29): a mapping inside a list inside a model is frozen too.
`calibration/fit.py:166-177` freezes the top level only.

### `schemas.py` — schema tags, the registry, an artifact's home

```python
class SchemaTagged(BaseModel):
    schema_id: str

@dataclass(frozen=True)
class DocumentId:
    name: str
    accepted_ids: tuple[str, ...]
    removable: str               # the spelling a consumer may delete

def accepted_schema_ids(document: DocumentId) -> tuple[str, ...]
def registry(documents: Iterable[DocumentId]) -> SchemaRegistry
def home_of(document: DocumentId) -> ArtifactHome        # directory, file name, schema id
```

**`accepted_schema_ids` and `SchemaTagged`** (rcp 26) are typed once. `rcp_core/schemas.py:45` already computes the
pair, and nine sites hand-write it (`calibration/fit.py:128,154`, `coverage.py:127`, `diagnostics.py:96`,
`extend.py:78,121`, `judging/store.py:116`, `judging/cost.py:91`, `eval/evaluate.py:166`).

**One exported constant per document id** (emb-train 3): `DocumentId` is a frozen record of `name`,
`accepted_ids` and `removable`, and `document.load` takes one. The library owns no repository's name; the consumer
declares its own. Both `registry` and `accepted_schema_ids` take the consumer's records as a parameter, so no
consumer name lands in library module state.

**The alias list states which spelling is removable** (rcp 30): `DocumentId.removable` names it, so a consumer can
delete one spelling, and `audit.scan` reports the source sites that still write it. Round 2 had no removable
marker and no call-site report.

**One schema registry** (emb-train 25) keeps a record schema-agnostic, and each consumer projects its own manifest.

**A cross-command artifact has a declared home and a schema id** (rcp 46). `cli/calibration.py:224` writes
`OpponentPlan` and `cli/judge.py:235` reads it, and no module states the path or the id.

### `faults.py` — one base, two tables, one warning channel

```python
class Fault(Exception):
    message: str
    code: str | None          # stable string, declared at the raise site
    details: Mapping[str, Any] | None
    hint: str | None          # resolved at output time, never baked
    cli_hint: str | None

def classify(exc: BaseException, *, mapper: Callable[[BaseException], Fault]) -> Fault
def register_fault_codes(rows: Iterable[tuple[type[BaseException], str]]) -> None   # explicit bootstrap call
def exit_codes(rows: Mapping[str, int]) -> ExitCodeTable                            # consumer-supplied
def warn(record: BaseModel, code: str, **fields: Any) -> None
def require(module: str, *, hint: str, extra: str | None = None) -> ModuleType        # the install line is the caller's
```

**Two tables, not one.** Round 1 declared `CODES: tuple[tuple[type[Fault], int], ...]`, which maps a class to an
integer and cannot serve a consumer. emb-train maps a class to a **stable string** and derives the exit code from
it (`emb_train_cli/errors.py:106-121`, `ERROR_CODES` at `:124`). rcp asks for the exit-code set and the warning-code
set **as consumer data** (rcp 5), and rcp spells the set as a library `Literal` today (`cli/output.py:32`,
`errors.py:286`).

- `FAULT_CODES` — class → stable string. The library ships the generic rows only (`OSError`, `ValueError`,
  `RuntimeError`, `ModuleNotFoundError`), as emb-train requirement 10 asks. A consumer adds its rows through
  `register_fault_codes`, an explicit call from `emb_eval.bootstrap`-style code, never at import.
- `EXIT_CODES` — code → integer. The consumer passes the table. The library ships no exit number.
- `WARNING_CODES` — a closed set **on the record that carries it**, declared as `WARNING_CODES` on the model and
  merged down the MRO exactly as `IDENTITY_ROLES` is. `warn(record, code, **fields)` refuses a code outside that
  record's set. A consumer-level set is the repository-wide enum one level out, which is the harm rcp requirement
  31 names. `audit` checks that a record with a warning channel declares the set. Requirement 34 asks for the
  channel; requirement 31 asks for the placement.

**One error contract** (rcp 32): message, hint, `cli_hint`, details, code and one `classify`. The hint is resolved at
output time (R5), so a library raise carries a class, a code and a message, and never a suggestion.

**Per-call-site ownership** (emb-train 12): the code is a parameter of the raise, so a catch block never decides a
code. `emb_train_cli/checks.py:170-195` shows the defect: the class decides.

**The install line is a parameter.** `faults.require("yaml", hint="pip install <extra>")` takes the hint from the
caller, defaulted by the consumer. Round 1's `document.py` baked `pip install cohere-rcp[yaml]`, which is the wrong
distribution after the move (review 2, F6). `emb_train_models/hf.py:17,25,30-31` and `precision.py:33` are two
hand-written versions of it; `document.py` uses it for `yaml`, and the consumer supplies the line.

### `commands.py` — one spec, two front ends, the result included

```python
@dataclass(frozen=True)
class Command:
    name: str
    request: type[BaseModel]
    result: type[BaseModel] | None
    run: Callable[[Any], BaseModel]
    human_lines: Callable[[BaseModel], Sequence[str]] | None = None   # emb-train 18
    services: Callable[[], Ports] | None = None
    add_arguments: Callable[[Any], None] | None = None

@dataclass(frozen=True)
class DoctorFacts:
    distributions: Mapping[str, str]
    pinned_sibling: tuple[str, str] | None
    extras: Mapping[str, str]
    credential_variables: tuple[str, ...]
    cache_dir: Path
    runs_dir: Path

@dataclass(frozen=True)
class Step:
    name: str
    field: str
    run: Callable[[Any], Any]

def build_parser(prog: str, verbs: Sequence[Command], *, brand: Brand,
                 envelope_schema_id: str, exit_codes: ExitCodeTable,
                 render: Callable[[BaseModel], Any] | None = None,
                 exit_help: Sequence[tuple[str, str]] = ()) -> argparse.ArgumentParser
def index(verbs: Sequence[Command]) -> tuple[str, ...]
def describe(verbs: Sequence[Command]) -> CommandTree
def option_specs(request: type[BaseModel]) -> tuple[OptionSpec, ...]
def tool(command: Command) -> dict[str, Any]
def envelope(command: Command, result: BaseModel, *, schema_id: str) -> dict[str, Any]
def run_steps(steps: Sequence[Step], *, stop_on_first: bool = True) -> StepResults
def customise(command: Command, option: str, help_at_print_time: Callable[[], str]) -> Command
```

**Every parameter requirement 2 names arrives as a parameter**: the program name (`prog`), the envelope schema id,
the result-rendering hook (`render=`), and the command table. `brand` fills schema ids, output-schema prefixes and
env names (rcp 6). `exit_codes` is the consumer's table (rcp 5), and `exit_help` is the consumer's help rows
derived from it (emb-train 15). No literal `"rcp"` survives: `cli/introspect.py:107` prefixes every path with it
today.

**The human lines are the verb's, not the library's** (emb-train 18). `Command.human_lines` takes the result model
and returns the lines the verb prints in human mode. `envelope()` takes the same callable, so one declaration
serves `--json`, the human render and the MCP tool. Round 2's prose named `render=` and no signature had it, and
no route existed at all for the verb's human lines.

**The spec has a result half.** Round 1 declared `Command(name, request, run)` and no result, so the MCP tool and the
output schema could not come from one declaration. rcp's own machinery already carries both
(`cli/schema.py:76-79`: `result=None, output_schema=...`). `tool()` reads the request model; `envelope()` reads the
result model; `describe()` reads both.

**`--json` and `--traceback` are declared once, here**, on the root parser `build_parser` returns. rcp knows the
flag in two places today: it declares it at `cli/command.py:308` and it sniffs it out of `argv` at
`cli/main.py:107`. Both go, and the envelope decision comes from the parsed namespace.

**A post-declaration hook** (rcp 4): `customise` replaces one option's help with a callable evaluated when help
prints. `cli/eval.py:133` (`_MetricOption.get_help_record`) is the need: a metric plugin registers after the command
is built.

**`doctor` takes its facts as data** (rcp 7): `DoctorFacts` (distributions, the pinned sibling, the extras table, the
credential variables, the cache and runs directories) is a `commands.py` record and a parameter of the `doctor`
command group. No default directory (`cli/docs.py:22`, `:26`, `:35`).

**`Step` and `run_steps`** (emb-train 16) own the results list, the stop-on-first-failure and the skip handling.

**A usage error raises and is caught** (emb-train 17), so an argparse fault gets the same envelope and code as any
other fault.

**The front end is a description, not a framework.** `option_specs(request)` returns a front-end-neutral tuple
(`name`, `type`, `default`, `help`, `repeatable`, `choices`, `required`). The library ships the **argparse** adapter
(stdlib). rcp is **click** (`cli/command.py:32`, `cli/main.py:29,84`, `cli/introspect.py:89` types
`walk(root: click.Command)`), and `click` is third-party, so C1 keeps it out of the library. rcp therefore ships a
small click adapter (`rcp/cli/_click_adapter.py`) that renders the same `option_specs` into click options. **rcp does
not rewrite eleven groups to argparse**, and the one declaration still produces both surfaces.

### `ports.py` — the host services, named once

```python
class Clock(Protocol): ...
class Budget(Protocol):          # a step count or a wall-clock deadline (rcp 39)
class Sink(Protocol): ...
class ExitCodeTable(Protocol): ...
class Renderer(Protocol): ...
class AtomicWriter(Protocol): ...  # write(path, data) as one atomic swap (rcp 36)
class UriIO(Protocol): ...         # read(uri) / write(uri, data), routed by scheme (rcp 38)
class Tensor(Protocol): ...      # typing-only; imports nothing (emb-train 29)

@dataclass
class LogState:                  # the facade's state, one per record (M2)
    brand: Brand                 # the log root is brand.name (M3): configkit unless the consumer passes its own

@dataclass(frozen=True)
class Ports:
    clock: Clock; budget: Budget; sink: Sink; exit_codes: ExitCodeTable
    renderer: Renderer; atomic: AtomicWriter; uri: UriIO; log: LogState

def logger(log: LogState, name: str) -> logging.Logger                               # the one log facade
def configure(log: LogState, sink: Sink, *, level: str, dedup_key: Callable[[Any], str] | None = None) -> None
def once(log: LogState, key: str) -> bool
```

rcp requirement 3 asks for **one ports module that names every host service the framework needs**. Five rcp names
are imported by four modules with no file naming the set (`cli/command.py:35`, `cli/output.py:26`, `cli/main.py:31`,
`cli/introspect.py:21`). `Ports` is a frozen record of protocols, one protocol per call (emb-train 19), never
`Callable[..., Any]`. The record carries **every** service a library module needs: `storage.publish` takes
`Ports.atomic`, and `read_uri`/`write_uri` take `ports` and route through `Ports.uri`. Round 2 declared the seam
and then omitted the two services its own storage module calls.

**No graph.** The library names the seam and builds nothing (emb-train 22). A consumer injects the assembly.

**A log facade, and no `print`** (rcp 35; emb-train 31). Round 1 mis-stated rcp 35 as a hint rule; it is about a
logger. `logger(log, name)`, `configure(log, sink, *, level, dedup_key)` and `once(log, key)` live here. They read
`Ports.log`, one state per record, so two records share no handler and no dedup key (M2). The root is the
consumer's `Brand.name` (M3), so the library writes under no other program's root. One configurable sink,
no handler installed at import, and dedup as a parameter. `support/logging.py` is the model; it installs no
handler at import except the run-log exception, and that exception moves to `storage`. `annotation/annotate.py:51`
calls `print` today.

### `plugins.py` — one lookup, one registration door that can refuse

```python
def lookup(group: str, name: str, entries: Iterable[EntryPoint]) -> Provider
def registered_names(group: str, entries: Iterable[EntryPoint]) -> tuple[str, ...]
def register(registry: Registry, row: Registration) -> None      # refuses at declaration
def register_from(spec: Mapping[str, Any], *, registry: Registry) -> None   # emb-train 20
def validation_hooks(store: Registry) -> tuple[ValidationHook, ...]        # run after the registry resolves
```

**One lookup, one duplicate-name policy, one load check, the group name as a parameter** (rcp 40).
`support/entrypoints.py` is the model: it refuses an ambiguous name and names the providers.

**The door refuses at declaration** (emb-eval M36). `scoring/registry.py:470-475` is the lazy `import_module`
inside `__getitem__`; `:432` is `METRIC_REGISTRY`. The check sits at the first `import_module`, far from the wrong
declaration.

**A validation hook runs after the registry resolves** (rcp 21): `inference/config.py:201` cannot see a third-party
adapter today.

**A model-registration hook reachable without Python** (emb-train 20): a declared document field
`models.register: list[Registration]`, read by `register_from(spec, registry=...)` at bootstrap, and the refusal
error names the field. A Python call alone is not reachable without Python, and `deps.py:102-112` has a docstring
and no statement. A **task mapping injected by the caller** (emb-train 21), not a module global.

### `storage.py` — the bytes, and the three seams

```python
def publish(uri: str, write: Callable[[BinaryIO], None], *, atomic: AtomicWriter) -> None
def lock(path: Path) -> ContextManager[None]        # POSIX requirement documented
def read_uri(uri: str, *, ports: Ports) -> bytes
def write_uri(uri: str, data: bytes, *, ports: Ports) -> None
class RunsRoot:                                      # one exported run and step directory layout
    def run_dir(self, run_id: str) -> Path
    def step_dir(self, run_id: str, step: str) -> Path
```

**The atomic-write seam is an argument** (rcp 36), so the library never assumes the caller's storage.
`storage/core.py:267 publish` and `calibration/fit.py:546 _swap_in` are two implementations of one idea.

**One advisory-lock helper** (rcp 37) with a documented POSIX requirement. `judging/store.py:265`,
`storage/core.py:254` and `storage/census.py:76` are three.

**A storage-URI abstraction for local, `gs://` and `s3://`** (rcp 38). `save` and `load` take a URI.
`cli/calibration.py:43` refuses a remote artifact today, and emb-eval M6 has four local-or-remote path layers with
incompatible rules. The scheme handlers are **host services**: `read_uri(uri, ports=)` and
`write_uri(uri, data, ports=)` route through `Ports.uri`, and the library ships the `file://` handler only. A
`gs://` handler is a consumer adapter, so the library needs no cloud SDK (C1). This row is **partial by design**:
the library owns the seam and one scheme, not the three schemes the requirement names.

**One run and step directory layout** (emb-train 24), read by a CLI and a store. `main.py:61-67,392` and
`checkpoint.py:42-43,200-205` are two readers of one layout.

### `vocab.py` — the words and the numbers

```python
class Capability(StrEnum): ...                       # emb-train 6
class TaskKind(str): ...                             # emb-train 7: open; a consumer registers its tasks
class MetricKey(str): ...                            # emb-eval S17/M4: parse and canonicalise `metric@k`
# DECISION_SCALE and SERVE_KEYS were module constants here. They are CONSUMER data and they are GONE.
# emb-train supplies its decision scale through its own model; emb-eval supplies its serve keys the same way.
# A library constant would make two consumers agree BY ACCIDENT, which is the defect this library exists to remove.
class WorldSize(int): ...                            # emb-train 5, one validator
class DigestPin(str): ...                            # emb-train 9, 64 hex, one validator
class DatasetRef(BaseModel): scheme, repo, subset, revision      # rcp 25
class KeyVocabulary:                                 # emb-train 4
    def check(self, mapping: Mapping[str, Any], *, where: str) -> None
class BudgetKey:                                     # rcp 18, rcp 47
    shape: str; role: str
class GainsKey(str): ...                             # rcp 47
def cut_for(shape: str) -> int                       # rcp 47
```

**The task vocabulary is open** (emb-train 7). Requirement 7's negative half is explicit: *"It must not assume a
closed task set"* (`emb-train-merged.md:438`). `TaskKind` is a `str` subclass with no closed member set, and a
consumer registers its tasks through a `KeyVocabulary`. `DECISION_SCALE` is a consumer-supplied value **and it is not a library constant**, not a
library constant. A `StrEnum` would publish a training-strategy list the library does not own.

**Metric names have one grammar** (emb-eval S17/M4). `MetricKey` parses, canonicalises and applies the depth rule
for a `metric@k` name, so the four owners of a metric name in emb-eval call one function.

**The serve vocabulary is one value the consumer declares** (emb-eval S24/X5). It is **not a library constant**;
the library ships no serve key, because a serve key is a consumer's word.

**Every extensible mapping has a declared key vocabulary with an unknown-key refusal** (emb-train 4):
`ModelSpec.config` (`schema.py:143`), `RuntimeSpec.extra` (`:165`), `DataSpec.filters` (`:225`) and the objective
knobs (`tasks.py:89,142`).

**The library ships the numeric fits it owns** (rcp 47): the cut, the budget and the gains-key grammar. Consumers
treat those numbers as library-defined. `data/text_policy.py:182,210` and `eval/evaluate.py:472` are two copies.

**One dataset-reference value object** (rcp 25): scheme, repo, subset, revision. Five independent parses exist
(`data/dataset.py:396,569`, `data/io/hub.py:159`, `data/io/mteb_task.py:61`, `data/revisions.py:307`).

**No benchmark name, no data pin, no organisation name** (rcp 48). `audit.scan` refuses a shipped pin.

### `spec.py` — the types, and the frozen containers

```python
class FrozenSpec(BaseModel): ...                     # emb-train 30
def frozen_mapping(...) -> MappingProxyType: ...
def frozen_sequence(...) -> tuple: ...
class OperationOptions(FrozenSpec): ...              # rcp 15: defaults and bounds live next to the operation

@dataclass(frozen=True)
class Brand:                                        # rcp 6: one parameter, three uses
    name: str                                       # the program name, e.g. "rcp"
    prefix: str                                     # the env-var prefix, e.g. "RCP"
    schema_root: str                                # the schema-id root, e.g. "<brand>.cli.v1"
    output_schema_prefix: str                       # the generated output-schema prefix
```

**A record type comes from a types module, not a service** (rcp 50). Every record the library exports (`Override`,
`Fault`, `Command`, `Step`, `Ports`, `Brand`, `DocumentId`) is defined in a module that imports nothing but the
standard library, `pydantic` and another library module. `audit` checks it.

**One `Brand` parameter** (rcp 6) fills schema ids, generated output-schema prefixes and env-var names. `commands`,
`overrides` and `faults` take it. `cli/output.py:79` (`CLI_SCHEMA = "rcp.cli.v1"`), `cli/command.py:337`,
`support/paths.py:19-21`, `data/media.py:46` and `judging/_parsing/schema.py:69` are the five literals it replaces.

**The operation's option model lives next to the operation** (rcp 15). The library ships the base and the rule;
the consumer puts the model beside the operation. `calibration/fit.py:578` versus `cli/calibration.py:140` versus
`runs/config.py:236` is three homes for one set of bounds.

**`FrozenSpec`, `frozen_mapping` and `frozen_sequence` are public** (emb-train 30), and the typed contract has a
consumer (emb-eval M13): `emb_eval_contracts/recipes.py` validates `MrlSpec.range` and no consumer calls it.

### `audit.py` — the checks that fail a test

```python
def audit(*roots: type[BaseModel], brand: Brand | None = None) -> list[str]:
    """Model-graph facts only: a field with no role, a role on a field that does not exist,
    a CONTENT field with a default, a PATH field the resolver misses, a code with no fault,
    a schema field with a default, a warning channel with no declared code set, a record type
    defined in a service module."""

def scan(
    *paths: Path,
    roots: Sequence[type[BaseModel]] = (),
    seam_prefix: str = "_",
    api_globs: Sequence[str] = ("**/*.py",),
    consumer_imports: Sequence[str] = (),
) -> list[str]:
    """Source facts only: a repository path literal, a command group named after a repository,
    a shipped data pin or benchmark name, an import of a consumer, an underscore seam,
    an upward import, a module whose __all__ misses a public name, a call site that still
    writes a removable schema spelling."""

def pure(validator: Callable[[Any], Any]) -> list[str]:
    """Runs one validator under an audit hook that refuses open, socket.* and subprocess.*."""
```

**Two functions, because two kinds of fact exist.** Round 2 declared one function that took model roots and then
claimed eight source checks on its behalf. A model walk cannot see a path literal, a command group name, an import
edge, an `__all__` or a call site. `audit` keeps the model-graph checks; `scan` owns the source checks; `pure` owns
rcp 20.

**Functions, not hooks.** No import-time run (C3), no run-time cost. **A test in each repository calls them.**

**`audit` walks the model graph from a root**, not a flat list. Round 1 took `models: Iterable[type[BaseModel]]`, and
a nested model that no test listed escaped. It now recurses through every `BaseModel` field, and `identity_of` keeps
its own per-level check as the second guard.

**`scan` reads source, and it reads no network.** It parses the tree with `ast`, so a path literal inside a string
constant and an import inside a function are both visible. A repository path in library source fails rcp 8, and an
`import rcp` in library source fails C2 a second time, at the text level.

### Which table wins, and the rule that decides it

Round 3 found the module table and §Coverage homing the same requirement in different modules, **with no rule saying
which wins.** Both cannot be the implementation map.

**The rule: §Coverage is authoritative. The module table is a summary.**

- **§Coverage names exactly one module per requirement**, or one named exclusion. That row is the contract.
- **The module table lists the requirements a module is *most* responsible for**, and it may name a requirement
  that §Coverage homes elsewhere — because a requirement can need two modules and only one can own it.
- **A disagreement is a defect in the module table**, and the fix is to correct the summary, never the coverage.

**Why this direction**: coverage is what a reviewer walks requirement by requirement, and a reviewer who finds two
answers cannot tell which was decided. **The summary exists to be read; the coverage exists to be checked.**

## The pipeline, and it is one call

```
read -> resolve -> apply -> validate -> identity
```

```python
@dataclass(frozen=True)
class Applied:
    model: BaseModel          # validated, after the overrides
    overrides: tuple[Override, ...]
    payload: Mapping[str, Any]  # identity_of(model)

def pipeline(path: Path, model: type[ModelT], *,
             overrides: Sequence[Override] = (), schema_id: str | None = None,
             path_fields: Sequence[Sequence[str]] = ()) -> Applied
```

Round 1 said `load` validates and `apply` "runs after `load` and before any validation". Both cannot hold. The
pipeline fixes the order:

- `read` parses, checks unique keys and the schema id, merges `extends`, and returns an unvalidated `Document`;
- `resolve` builds the model with `model_construct`, resolves every `PATH` field, and returns a **draft**;
- `apply` writes the overrides into the draft, and re-validates nothing;
- `validate` builds the model, so one `ValidationError` produces one `config_error` fault;
- `identity` calls `roles.identity_of` on the validated model.

`load(...)` is `read + resolve + validate` for a caller with no overrides. **`pipeline` is the only path a run
uses**, so the cache bug closes by construction only when the consumer calls it. **The consumer rule:** the run path
hashes `Applied.payload`, never a document it read before `apply`. The library cannot force that; it can make the
wrong order the harder one to write, and the consumer's own test reports a run path that hashes a document it did
not get from `pipeline` (one line, in that consumer's suite).

## Coverage: every requirement has a home or an exclusion

`L` = the library. `not in L` names the reason.

### rcp, requirements 1–50

| # | requirement (short) | home |
|---|---|---|
| 1 | declaration, envelope, option builder, self-description | `commands.py` (`Command.run` = `execute`, `describe` = `describe_commands`, `index` = `command_index`, `option_specs` = `command_specs`) |
| 2 | table, `prog`, envelope schema id, render hook as parameters | `commands.py` (`build_parser`) |
| 3 | one ports module naming every host service | `ports.py` |
| 4 | post-declaration hook to customise one option | `commands.py` (`customise`) |
| 5 | exit-code set and warning-code set as consumer data | `faults.py` (`exit_codes`, `WARNING_CODES`) |
| 6 | one `brand` parameter | `spec.py` (`Brand`), used by `commands.py`, `overrides.py`, `faults.py` |
| 7 | `doctor` takes its facts as data | `commands.py` (`DoctorFacts`) |
| 8 | no repository path | `audit.scan` (a path literal in library source) + C2 |
| 9 | ships no product command group | **not in L** — a consumer declares its groups; `audit.scan` refuses a group named after a repository |
| 10 | one loader: YAML, `extends`, `--set`, typed error naming file and field | `document.py` |
| 11 | two-base path rule, documented with tests | `document.py` |
| 12 | one `KEY=VALUE` grammar, one ladder, one error table | `overrides.py` (`parse_value`, `VALUE_ERRORS`) |
| 13 | structured config edits | `overrides.py` (`Override`) |
| 14 | `config_error(exc, model=, source=)` with field path, expected, known keys, redaction | `document.py` |
| 15 | option model next to the operation | `spec.py` (`OperationOptions`) |
| 16 | every knob a declared field with a role | `roles.py` (`Role.ENV` + `audit`) |
| 17 | env namespace and one precedence table, per-repo prefix | `overrides.py` (`from_env`, `prefix`) |
| 18 | per-shape and per-role budget schemas | `vocab.py` (`BudgetKey`) |
| 19 | pluggable field types | `document.py` (`register_field_type`, `FieldTypes` registry and schema hook) |
| 20 | validation is pure | `document.py` purity rule + `audit.pure(validator)` |
| 21 | validation hook after the registry resolves | `plugins.py` |
| 22 | one resolver from name, path, recipe id or shorthand, reporting attempts | `document.py` (`resolve_reference`) |
| 23 | layered merge with CONTENT/RUNTIME conflict refusal | `overrides.py` (`merge_layers`) |
| 24 | declared reader and writer option schemas | `spec.py` (`OptionSchema` base) + the consumer's own model + `audit.scan` (refuses `**options: Any` in a declared surface) |
| 25 | one dataset-reference value object | `vocab.py` (`DatasetRef`) |
| 26 | `accepted_schema_ids` + `SchemaTagged` | `schemas.py` |
| 27 | one identity mechanism, no bypass | `roles.py` + `identity.py` |
| 28 | an identity field is derived, never defaulted | `identity.py` + `audit` |
| 29 | deep freeze of an identity artifact | `identity.py` (`freeze`) |
| 30 | the alias list states which spelling is removable | `schemas.py` (`DocumentId.removable`) + `audit.scan` (the call sites) |
| 31 | closed warning-code set on the record | `faults.py` |
| 32 | one error contract: message, hint, `cli_hint`, details, code, `classify` | `faults.py` |
| 33 | L owns the taxonomy; `rcp.errors` aliases it | `faults.py` (open question 2, revised) |
| 34 | one warning channel keyed by a closed set | `faults.py` (`warn`) |
| 35 | one logging facade: no print, one sink, dedup as a parameter | `ports.py` (`logger`, `configure`; folded in from the planned `log.py`) |
| 36 | one atomic-write seam, taken as an argument | `storage.py` |
| 37 | one advisory-lock helper | `storage.py` |
| 38 | storage-URI abstraction (`gs://`, `s3://`) | **partial in L** — `storage.py` owns the seam and the `file://` handler; a `gs://` or `s3://` handler is a consumer adapter on `Ports.uri` (C1) |
| 39 | a step or wall-clock budget hook | `ports.py` (`Budget`) |
| 40 | one plugin-registry lookup | `plugins.py` |
| 41 | no import-time I/O or env read | C3 + isolation proof P3 |
| 42 | no numpy, pandas, PyYAML on the config import path | C4 + P1 |
| 43 | no product module, no rcp import | C2 + P2 |
| 44 | a seam is a public name | C5 + `audit.scan` (no underscore seam, no consumer import) + one test in each consumer |
| 45 | the payload is L's model | C6 + `commands.py` (`Command.result`) + one renderer test in each consumer |
| 46 | a cross-command artifact has a home and a schema id | `schemas.py` (`home_of`) |
| 47 | L ships the numeric fits it owns | `vocab.py` (`cut_for`, `GainsKey`, `BudgetKey`) |
| 48 | no organisation data pins | C2 + `audit.scan` |
| 49 | third-party internals behind versioned adapters | **not in L** — L imports nothing third-party but `pydantic`, so it has no third-party internals to wrap; the consumer's adapters stay |
| 50 | a record type comes from a types module | `spec.py` rule + `audit` |

### emb-train, requirements 1–33

| # | requirement (short) | home |
|---|---|---|
| 1 | parameterised loader: `schema_id`, `path_fields`, `validate` | `document.py` (`load`) |
| 2 | path resolution on the field, plus an absolute-path rule | `roles.py` (`Role.PATH`) + `document.py` |
| 3 | one exported constant per document id, taken as a parameter | `schemas.py` (`DocumentId`) + `load(schema_id=)` |
| 4 | one key vocabulary per extensible mapping, with refusal | `vocab.py` (`KeyVocabulary`) |
| 5 | typed `world_size` and one validator | `vocab.py` (`WorldSize`) |
| 6 | one capability vocabulary constant | `vocab.py` (`Capability`) |
| 7 | one task vocabulary and one decision scale | `vocab.py` (`TaskKind`, open; `DECISION_SCALE`, consumer-supplied) |
| 8 | per-objective knob declaration | `vocab.py` (`KeyVocabulary` per objective) |
| 9 | a digest-pin type | `vocab.py` (`DigestPin`) |
| 10 | a fault table the consumer builds, generic rows only in L | `faults.py` (`register_fault_codes`) |
| 11 | one library error base | `faults.py` (`Fault`) |
| 12 | per-call-site mapping owned by the raiser | `faults.py` (`code=` at the raise) |
| 13 | one identity chain as one call | `identity.py` (`identity_chain`) |
| 14 | a run-id grammar with tags as a named operation | `identity.py` |
| 15 | `Verb` record + `build_parser(prog, verbs, exit_help)` | `commands.py` (`Command`, `build_parser(..., exit_help=)`) |
| 16 | `Step` record + `run_steps` | `commands.py` |
| 17 | a usage-error path that raises and is caught | `commands.py` |
| 18 | envelope with a parameter schema id and verb-supplied lines | `commands.py` (`envelope`, `Command.human_lines`) |
| 19 | a typed seam, one protocol per call | `ports.py` |
| 20 | a model-registration hook reachable without Python | `plugins.py` (`register_from` reads `models.register` from the document) |
| 21 | a task mapping injected by the caller | `plugins.py` |
| 22 | an injected run-assembly seam | **not in L** — L builds no optimizer, scheduler or store; `ports.py` names the seam |
| 23 | one `preflight` that is the only data gate | **not in L** — preflight is a data-domain gate; L has no data. A consumer owns it, and `audit.scan` refuses a second gate in L's source |
| 24 | one exported run and step directory layout | `storage.py` (`RunsRoot`) |
| 25 | a schema registry | `schemas.py` |
| 26 | a sealed-artifact contract, or an explicit exclusion | **not in L**, explicitly: the artifact schema stays out; L owns the reading and the layout, not the fields |
| 27 | no import-time side effects | C3 + P3 |
| 28 | a lazy optional-dependency helper, hint as a parameter | `faults.py` (`require`; folded in from the planned `optional.py`) |
| 29 | a torch typing hook | `ports.py` (`Tensor`, typing-only, imports nothing) |
| 30 | public frozen-container factories and a spec base | `spec.py` |
| 31 | a log stream L writes to; no `print` | `ports.py` (`logger`, `configure`; folded in from the planned `log.py`) |
| 32 | one published surface per module | `__init__.py` + `__all__` + `audit.scan` (an `__all__` that misses a public name) |
| 33 | L carries its own tests and test folder | `tests/`, one module per library module |

### emb-eval, the rows a shared library must answer

| finding | requirement it produces | home |
|---|---|---|
| S1 | one document for one run | `document.py`; the consumer migration closes it |
| S2 | the override channel on the verb that runs | `overrides.py`; the consumer wires it |
| S3 | three artefacts in three formats | **not in L** — artefact formats are consumer data; L owns no format |
| S5, X7 | the manifest records the document actually used | `document.py` (`Applied`) + the consumer rule |
| S16, X7 | four run-id builders, one rule | `identity.py` (`identity_chain`) |
| S17, M4 | metric names have four owners | `vocab.py` (`MetricKey`) |
| S18 | no package has a surface its tree uses | `audit.scan` |
| S23 | seven exit-code tables | `faults.py` (`exit_codes`) |
| S24, X5 | the serve vocabulary declared four times | `vocab.py` (`SERVE_KEYS`, declared by the consumer) |
| S25, X2 | the import graph is not layered | C5 + `audit.scan` (an upward import in L is a failure) |
| M2 | `--compute`/`--score` do not enter the identity | `overrides.py` + the pipeline order |
| M5 | no common refusal base | `faults.py` |
| M6 | four local-or-remote path layers | `storage.py` |
| M13, X4 | a typed contract with no consumer | `spec.py` |
| M22 | five hand-typed leaf sets | `document.py` (`leaf_paths`) |
| M23 | four YAML-mapping readers, four refusals | `document.py` (one reader) |
| M36 | the registration door cannot refuse at declaration | `plugins.py` (`register`) |
| X1 | one question, "change this run's metric", has five answers | `overrides.py` + `commands.py` (one command spec); the home is S2's |
| X3 | four documented verbs the dispatcher lacks | `commands.py` (the dispatcher is built from the spec) |
| DEBT 207 | a corrupt `units.json` raises `ValueError`, not the tree's error | `faults.py` gives `ValueError` a generic **label**; the tree's own error is the consumer's change, which raises `IdentityError` at the read site |

**Counts.** rcp: 50 rows, 47 with a home, 1 partial by design (38), 2 explicit exclusions (9, 49). emb-train: 33
rows, 30 with a home, 3 explicit exclusions (22, 23, 26). emb-eval: 20 rows, 19 with a home, 1 explicit exclusion
(S3). No row is silent, and no row names a symbol the module does not export: `MetricKey`, `SERVE_KEYS`,
`DoctorFacts`, `human_lines`, `render`, `exit_help`, `DocumentId.removable`, `AtomicWriter` and `UriIO` are all in
the API blocks above.

## What is deliberately not in it

**Not a dependency-injection container.** The library names the seam (`ports.py`) and builds no graph.
**Not a logging framework.** The library owns a facade (`ports.logger`, `ports.configure`): one sink, one dedup key,
no handler at import.
This corrects round 1, which excluded logging and mis-stated rcp requirement 35.
**Not a retry, cache or concurrency layer.** Every repository already has one and they differ on purpose.
**Not a schema for any repository's document.** The library owns the *reading*, not the *fields*.
**Not a data gate.** `preflight` stays in the consumer.
**Not a home for a repository's command groups.** The consumer declares them.

## The isolation proof, corrected, and how a reviewer checks it

Each proof runs in under a minute, reads only, and has an expected result that the round-1 form could not produce.

### P1 — C1: standard library and `pydantic` at import time

```bash
uv run --no-sync python -c "
import importlib, pathlib, sys
import pydantic, configkit
# EVERY module file, not what __init__ happens to export. A module __init__ omits would otherwise escape all four
# proofs, which is exactly what round 3 found (review 1, blocker 2).
pkg = pathlib.Path(configkit.__file__).parent
mods = sorted(f.stem for f in pkg.glob('*.py') if f.stem != '__init__')
for name in mods:
    importlib.import_module(f'configkit.{name}')
allowed = set(sys.stdlib_module_names) | {'configkit', '__main__'}
roots = sorted({n.split('.')[0] for n in sys.modules
                if n.split('.')[0] not in allowed and not n.startswith('_')})
print(len(mods), 'modules'); print(roots)"
# expected: the module count, then the pydantic closure and nothing else
```

**Every proof in this section imports the same way.** P2, P3 and P4 enumerate `pkg.glob('*.py')` too, so a module
that `__init__.py` omits is still imported, still traced and still checked. **The count is printed, so a package that
grows a module without a proof shows it.**

The allow-list is the **`pydantic` closure, computed at test time**: the check asserts that every observed root is
a distribution `pydantic` declares, and that no name outside the closure appears. A fixed five-name list false-fails
on a lock that does not resolve `annotated_types` and false-passes on one that adds a name, so the assertion is
`set(roots) <= closure(pydantic)` and `set(roots) >= {'pydantic'}`. **A closed set, not a blocklist**, so
`requests`, `httpx`, `google.cloud.storage` or `rcp_core` cannot pass (review 1, F2). Round 1's five-name blocklist
contained `rcp`, which `import rcp.configkit` always puts in `sys.modules`, so its `[]` was unreachable (review 1,
F1).

**P1 proves the import-time half of C1 only.** A lazy `import yaml` is invisible here, which is why C1 is restated
as an import-time condition (review 1, M1). The lazy half has its own check, and it can fail:

```bash
uv run --no-sync python -c "
import sys, configkit
sys.modules['yaml'] = None
try:
    configkit.overrides.parse_value('{k: v}')
except configkit.faults.Fault as exc:
    print('refused:', exc.code)"
# expected: refused: optional.missing
```

That is the `faults.require("yaml", ...)` fault, with the consumer's install hint. `tests/test_faults.py` runs
the same call with `sys.modules['yaml'] = None`, so the optional extra is a tested path and not a claim.

### P2 — C2: no `rcp` import, proved at run time

```bash
uv run --no-sync python -c "
import sys
import configkit
print([n for n in sys.modules if n.split('.')[0] in {'rcp', 'rcp_core'}])"
# expected: []
```

A run-time check is the primary proof, because a grep proves the absence of a text pattern, not of an import
(review 1, F3). The second check is a subprocess whose `sys.path` excludes the rcp checkout, so a successful
`import rcp` cannot hide behind the workspace's editable installs:

```bash
uv run --no-sync python -I -c "
import sys
sys.path = [p for p in sys.path if 'repos/rcp' not in p]
sys.path.insert(0, 'packages/configkit/src')
import configkit, rcp" 2>&1 | tail -1
# expected: ModuleNotFoundError: No module named 'rcp'
```

The filter drops only the workspace's editable paths (they contain `repos/rcp`); `site-packages` stays, so
`pydantic` is importable and the check fails for the right reason. Measured on a stub in the rcp venv: the same
command prints `ModuleNotFoundError: No module named 'rcp'`.

The text check stays, widened to the dynamic forms:

```bash
git grep -n 'from rcp\|import rcp\|__import__(.rcp\|find_spec(.rcp\|pkgutil' -- packages/configkit/src/
# expected: no match
```

The manifest is a **third** check, not a proof. It declares `pydantic` and no sibling:

```bash
grep -n -A4 '^\[project\]' packages/configkit/pyproject.toml | grep -A3 dependencies
# expected: pydantic, and nothing else
```

Round 2 claimed the manifest made `import rcp` fail. It does not: the workspace installs every member editable into
one venv (review 1, M2; measured above).

### P3 — C3: no import-time I/O, and no import-time environment read

No audit event exists for an environment read. On this stack (Python 3.12.9) a read of `os.environ` and a call to
`os.getenv` raise nothing; only a write raises `os.putenv`:

```bash
python3 -c "
import sys, os
ev=[]
sys.addaudithook(lambda e,a: ev.append(e))
os.environ['A']='1'; os.environ.get('A'); os.getenv('A')
print(sorted(set(ev)))"
# ['os.putenv']
```

So the proof records environment reads directly, and records **every** `open`, with no root filter:

```bash
uv run --no-sync python -c "
import sys, os
reads, opened = [], []
class Rec(dict):
    def __getitem__(self, k): reads.append(k); return super().__getitem__(k)
    def get(self, k, d=None): reads.append(k); return super().get(k, d)
os.environ = Rec(os.environ)
_g = os.getenv
def getenv(k, d=None): reads.append(k); return _g(k, d)
os.getenv = getenv
def hook(e, a):
    if e == 'open' and isinstance(a[0], str): opened.append(a[0])
sys.addaudithook(hook)
import configkit
print('data reads:', sorted({p for p in opened
                            if not p.endswith(('.py', '.pyc')) and '__pycache__' not in p}))
print('env reads:', sorted(set(reads)))"
# expected: data reads: []   /   env reads: []
```

**This proof fails on a violation, and I ran both controls.** A stub whose `__init__.py` reads `/etc/hostname` and
`os.environ["HOME"]` prints `data reads: ['/etc/hostname']` and `env reads: ['HOME']`. The consumer control, with
the same body and `import rcp.data.dataset`, prints the four files `dataset.py:62` reads at import
(`data/suites/{bright,nanobeir,trecdl,vidore}.yaml`) and a non-empty env list that includes
`NPY_PROMOTION_STATE`, `PYTHONUSERBASE`, `PYDANTIC_DISABLE_PLUGINS` and `RCP_NDCG_IMAGE_CACHE_SIZE`. Measured, trimmed:

```
data reads: ['data/suites/bright.yaml', 'data/suites/nanobeir.yaml', 'data/suites/trecdl.yaml', 'data/suites/vidore.yaml', ...]
env reads: ['HOME', 'NPY_PROMOTION_STATE', 'PYDANTIC_DISABLE_PLUGINS', 'PYTHONTZPATH', 'PYTHONUSERBASE', 'RCP_NDCG_IMAGE_CACHE_SIZE', ...]
```

Round 2's hook listened for `os.getenv`, which does not exist as an event, and filtered `open` events to the
package root, so an outside-tree read was dropped. Both defects are gone (review 1, B1; review 2, F1).

**The stated blind spot is narrow.** A read through a C extension that opens a file without the `open` event, and a
read through `os.environb`, are outside the record. Both are named here, and neither is a path the library takes.
Round 2's blind-spot sentence claimed only a `.py` file under the tree; that sentence is withdrawn.

### P4 — the extraction is a copy, and the copy imports

```bash
rm -rf /tmp/probe && mkdir -p /tmp/probe && cp -r packages/configkit/src/configkit /tmp/probe/configkit
PYTHONPATH=/tmp/probe uv run --no-sync python -c "
import sys
import configkit
allowed = set(sys.stdlib_module_names) | {'configkit', '__main__'}
roots = sorted({n.split('.')[0] for n in sys.modules
                if n.split('.')[0] not in allowed and not n.startswith('_')})
print(configkit.__file__)
print(roots)"
# expected: /tmp/probe/configkit/__init__.py
#           ['pydantic', 'pydantic_core', 'typing_extensions', 'typing_inspection']
```

The second line is a **closed set**, the same check as P1, not a five-name blocklist (review 1, m4). A copy that
imports `rcp`, `yaml`, `numpy` or `torch` prints that name.

**The copy is the module directory, not the distribution directory.** A copy of `packages/configkit/` imports as a
namespace package: `configkit.__file__` is `None` and no library code runs, so round 2's proof passed on an empty
package (review 1, B2; review 2, F2). Measured on a stub of that layout:

```
$ mkdir -p /tmp/nsp/configkit/src/configkit && touch /tmp/nsp/configkit/pyproject.toml
$ printf 'x=1\n' > /tmp/nsp/configkit/src/configkit/__init__.py
$ cp -r /tmp/nsp/configkit /tmp/probe && PYTHONPATH=/tmp/probe python3 -c "import configkit; print(configkit.__file__)"
None
```

**The negative control, and it can fail:**

```bash
rm -rf /tmp/probe/bad && mkdir -p /tmp/probe/bad
cp -r /tmp/probe/configkit /tmp/probe/bad/configkit
printf 'import rcp\n' >> /tmp/probe/bad/configkit/__init__.py
uv run --no-sync python -I -c "
import sys
sys.path = [p for p in sys.path if 'repos/rcp' not in p]
sys.path.insert(0, '/tmp/probe/bad')
import configkit" 2>&1 | tail -1
# expected: ModuleNotFoundError: No module named 'rcp'
```

The control drops the workspace's editable paths for the same reason P2 does, so the failure names `rcp` and not a
missing dependency. Measured on a stub: `ModuleNotFoundError: No module named 'rcp'`.

**A copy, never a `git mv`**, so a read-only reviewer can run it (review 1, F5). **`--no-sync`**, so `uv run` cannot
write `uv.lock` or `.venv` (review 1, m5). **No personal path and no `cd`**: every command runs from the repository
root, which is the directory the reviewer already stands in (review 2, F3). **`PYTHONPATH`, not the working
directory**, because `python -c` puts the current directory on `sys.path`, and the current directory is the
repository root (review 2, F2).

### The enumeration rule, and why it is not decoration

**P1, P2, P3 and P4 each import every `*.py` file in the package, not `import configkit`.** Round 3's blocker was
that a module `__init__.py` does not export escapes all four proofs while they all still print their expected line.
**The enumeration closes it, and the printed count makes a new module visible in every proof run.**

**What the enumeration cannot catch**: a module that is imported only by a consumer, outside the package directory.
That is a consumer-side check, and it is one of the six consumer tests this proposal asks each repository to add.

### Row 24, corrected — a false home is worse than a missing one

Round 3 found that `ReaderOptions`/`WriterOptions` were **bare config-document models** in `spec.py`, while the
requirement is a **data reader's option schema**: `load_dataset(..., **options: Any)` at `data/dataset.py:366-373`
must become a declared model so a document validates before a run starts.

**The correction**: the library does **not** own `rcp`'s reader options — that is a consumer's data reader.
**It owns two things instead**, and they are the ones that stop the bag coming back:

1. **`spec.OptionSchema`** — a base a consumer subclasses, with the `Role` declarations its options need.
2. **`audit.scan` refuses `**options: Any` in a declared API surface.** The check, not the model, is what keeps the
   requirement true after the first commit.

**The distinction matters**: a library that shipped `ReaderOptions` would have to know rcp's reader, and would then
fail the extraction condition. **A library that ships the base and the check serves all three repositories and stays
extractable.**

### P5 — C5: every seam is a public name, proved in each consumer

`audit.scan` runs inside `configkit` and checks the library's own tree: every imported name from another package is
public, and no library module imports a consumer. That half is real but small, because C1 leaves the library one
third-party package.

The eleven underscore edges rcp 44 names are in rcp's consumer code (`calibration/_projection.py:173`,
`extend.py:40`, `cli/data.py:462`, …), and a library test cannot see them. So each consumer ships the test:
`packages/rcp/tests/test_seam.py` and `emb-eval/tests/test_seam.py` walk their own import graph and refuse an
underscore edge across a package boundary. One shared contract file (`contracts/seams.toml`) lists the allowed
edges, and both tests read it. Round 2 put the whole proof inside the library, where it could not fail (review 1, M9).

### P6 — C6: the payload a front end prints is the library's model, proved in each consumer

Each consumer ships one renderer test. rcp's runs its click renderer against a `command.result` instance and
asserts that the formatter receives the model, not a projected dict: `cli/calibration.py:66-94` and
`cli/eval.py:49-95` are the two projecting front ends, and the test fails while they project. emb-eval ships the
same test for its viewer. Inside the library, `tests/test_render.py` checks only that the shipped argparse adapter
passes the model to `render` unchanged. Round 2 claimed a library test double could prove C6; it cannot (review 1,
M9).

**`scan` takes the parameters its own checks need, and round 3 was right that it did not.**

| parameter | the check it enables | the requirement |
|---|---|---|
| `roots` | a field with no `Role`, and a role on a field that does not exist | rcp 8, 20; R4 |
| `seam_prefix` | a seam that is an underscore path, not a public name | rcp 44, C5 |
| `api_globs` | `**options: Any` in a declared surface | rcp 24 |
| `consumer_imports` | a library module that imports a consumer | C2 |

**A check with no parameter for what it looks at is the defect class this whole programme keeps finding.** `scan` is
the one place that must not repeat it, so every argument is explicit and none has a default that guesses.

## Tests the library carries

| test module | what it pins |
|---|---|
| `tests/test_roles.py` | the seven roles; the four identity-payload rules, each with one pinned payload; `check_declarations` on a nested model |
| `tests/test_document.py` | the five v0 rules; the two path bases; the absolute path kept, and refused when a model declares `absolute_paths = False`; `config_error` on a `ValidationError` with a `SECRET` field; `FieldTypes` registered per test, no leak |
| `tests/test_overrides.py` | the value ladder: `[a, b]` → `list`, `{k: v}` → `dict`, `key=` → `""`, `yes` → `True`, `5` → `int`; the precedence table; the error table; `merge_layers` refusal |
| `tests/test_identity.py` | the digest against a recorded fixture from `rcp_core._hashing` and `emb_eval_contracts.identity`; `short` bounds; `freeze` depth; the run-id grammar |
| `tests/test_configkit_schemas.py` | `accepted_schema_ids`; the removable spelling; `home_of` |
| `tests/test_faults.py` | registration order; a consumer row without importing its class; a warning code outside the set; `classify` with a mapper argument; the install line is the caller's string |
| `tests/test_commands.py` | one spec → flags, envelope, MCP tool; the result model; `--json` declared once; the usage-error path; `customise` |
| `tests/test_ports.py` | one protocol per call; no torch import; no handler at import, one sink, dedup once per key; no `print` in the package |
| `tests/test_plugins.py` | a refusal at declaration; the duplicate-name policy; a hook after resolution |
| `tests/test_storage.py` | atomic write through an injected writer; the lock; the layout; a `file://` URI |
| `tests/test_vocab.py` | each vocabulary constant; `WorldSize`; `DigestPin`; `BudgetKey`; `MetricKey` canonicalisation; an open `TaskKind` that a consumer extends |
| `tests/test_spec.py` | `FrozenSpec`; `OperationOptions`; a record type defined outside a service |
| `tests/test_audit.py` | every `audit` refusal; the graph walk from a root; every `scan` refusal, with one failing control per check; `pure` on one clean validator and one that opens a file |
| `tests/test_isolation.py` | P1, P2, P3 and P4 as tests, each with its own negative control built in `tmp_path`; the suite imports no consumer at module scope |
| `tests/test_identity_parity.py` | the cross-distribution parity with `rcp_core._hashing`, byte for byte, skipped when the core is not installed |
| `tests/test_public_surface.py` | the library is in no `PUBLIC_MODULES` entry and in no contract snapshot |
| `tests/test_library_docs.py` | this page's module tree and test table name exactly what the tree has |
| `tests/test_distribution.py` | the library is `cohere-configkit` at the carved path, not a module of the product's wheel |

The library carries its own tests and its own folder (emb-train 33), and it depends on no test-double package.
**No library test imports a consumer, and no library test writes a personal path** (review 2, F3): the
`rcp.data.dataset` control lives in rcp's suite, not in the library's, so the directory move still carries a suite
that runs in the target repository.

## The open questions, with revised answers

1. **The name.** `configkit` is a placeholder. It is now the distribution name and the import name, so the
   extraction changes neither. It contains no `rcp`, `emb-eval` or `emb-train`, and it survives the move to
   `emb-utils` unchanged.
2. **Where the extraction boundary sits.** Round 1 answered "`faults.py` replaces both". That answer was wrong: it
   replaced a loader with an exception table. **Revised:** the taxonomy moves into `faults.py` with data-driven row
   registration; the loader half splits into `document.py` (reading, schema id, path resolution, `config_error`) and
   `overrides.py` (grammar, precedence, apply); `rcp.errors` keeps the product's own rows and re-exports the base
   for one release; `classify` takes the mapper as an argument, so the function-local back edge to
   `support/config.py` disappears.
3. **Whether `commands.py` is in scope.** **Yes**, and it now carries the result half. The spec is front-end
   neutral; the library ships the argparse adapter; rcp ships a click adapter that renders the same `option_specs`.
   The cost in rcp is one adapter module, not a rewrite of eleven click groups.

## Disputed findings

**No finding in round 2 is disputed.** Every round-2 blocker and major is fixed, and each fix names the probe that
contradicted the round-2 text. The two round-1 disputes below still stand.

**No required change is disputed.** One justification is wrong, and I record it because the coverage table quotes it.

**Review 1, §1 row 5, justification "No warning-code set exists at all".** rcp has one:

```bash
git grep -n "WARNING_CODES\|WarningCode" -- packages/rcp/src/rcp/errors.py
packages/rcp/src/rcp/errors.py:286:WARNING_CODES: tuple[str, ...] = get_args(WarningCode)
packages/rcp/src/rcp/errors.py:301:        if code not in WARNING_CODES:
```

So a warning-code set exists. It is a library `Literal` (`WarningCode`) and `WARNING_CODES` is derived from it, so
requirement 5 still fails: the set is not consumer data. **The verdict stands and the fix stands**; only the
sentence "no warning-code set exists at all" is withdrawn.

**Review 1, F7, and review 2, "the cycle is real".** Both reviewers describe the same two lines
(`support/config.py:24`, `errors.py:497`). Review 1 is right about the import-time direction: one module-level edge
and one function-local back edge. This proposal states the tree's shape and drops the word "cycle". That is an
agreement, not a dispute.

## Repair report r1

One line per finding. `FIXED` names what changed in this document. `DISPUTED` names the probe.

**Review 1**

- F1 (blocker, proof C1 prints `rcp`) — FIXED. §P1 is a closed-set check with a measured expected list; the library
  is its own distribution, so `rcp` is never imported.
- F2 (major, C1 is a blocklist) — FIXED. §P1 compares against `sys.stdlib_module_names` plus a named allow-list.
- F3 (major, C2 is a text scan) — FIXED. §P2 adds the run-time `sys.modules` check and the manifest check; the grep
  stays as the second check.
- F4 (blocker, C3 proves nothing) — FIXED. §P3 uses `sys.addaudithook`; the control on `rcp.data.dataset` is
  recorded, and it fails.
- F5 (blocker, C4 cannot run and mutates the tree) — FIXED. §P4 copies with `cp -r` and runs from `/tmp` with
  `PYTHONPATH`; measured on a stub.
- F6 (major, proof 4 mislabelled, C5/C6 unproved) — FIXED. §P4 is the extraction proof; §P5 and §P6 are new tests.
- F7 (major, "import cycle" is not a cycle) — FIXED. §The extraction hazard states one module-level edge and one
  function-local back edge, and open question 2 takes the mapper as an argument.
- F8 (blocker, digest rests on `rcp_core._hashing`) — FIXED. `identity.py` implements the canonical form and SHA-256
  with `hashlib`, states the byte-for-byte requirement, and pins it with a fixture test; the short length is a
  parameter with a named default.
- F9 (blocker, `identity_of` drops three rules) — FIXED. `roles.py` states the four rules and each has a pinned
  payload test.
- F10 (blocker, `load` validates and `apply` runs before validation) — FIXED. §The pipeline gives read → resolve →
  apply → validate → identity as one call, and states the consumer rule for hashing.
- F11 (blocker, argparse-only against click) — FIXED. The spec is front-end neutral (`option_specs`), the library
  ships argparse, rcp ships a click adapter; open question 3 prices it.
- F12 (blocker, `CODES` cannot express the requirement) — FIXED. `FAULT_CODES` (class → string, consumer-registered)
  and `EXIT_CODES` (code → int, consumer-supplied), plus `WARNING_CODES`.
- F13 (major, the logging exclusion mis-states rcp 35) — FIXED. `ports.py` owns the facade (`logger`,
  `configure`); §What is deliberately not in it says so.
- F14 (major, two citations not in the sources) — FIXED. The 1,402/2.9 s figure is withdrawn and replaced by a
  measurement I ran (236 modules; 1,231 modules in 2.62 s); `_PATH_FIELDS` is attributed to
  `emb_train_cli/config.py:36-40` and `emb_train_core/schema.py:137`.
- F15 (major, the five v0 rules are not kept) — FIXED. §document.py has a rule-by-rule table: five kept, two changed
  (YAML, `extends`), and "verbatim" is withdrawn.
- F16 (major, the library has no tests) — FIXED. §Tests names one module per library module and maps P1–P6 to tests.
- F17 (major, `audit` no longer checks a nested model) — FIXED. `identity_of` checks at every level, and `audit`
  walks the graph from a root.
- F18 (minor, a consumer name in the env example) — FIXED. The example is `env:<PREFIX>_METRICS`, and the prefix is a
  parameter.
- Coverage table, 102 rows (34 blockers, 46 majors) — FIXED. §Coverage maps all 50 rcp rows, all 33 emb-train rows and
  the 19 emb-eval rows; 6 rows are explicit exclusions with a reason. Named rows: rcp 5, 6, 13, 26, 27, 35, 40, 44,
  47 and 50; emb-train 1, 3, 4, 10, 13, 18, 19 and 33; emb-eval S16, M2, M22 and M36.
- Row 5 justification "no warning-code set exists at all" — DISPUTED (justification only). Probe: `errors.py:286`
  defines `WARNING_CODES`. The verdict and the fix stand.

**Review 2**

- F1 (blocker, the C1 proof cannot pass under an eager `rcp/__init__.py`) — FIXED. The library is its own
  distribution, `packages/configkit/`; §Placement gives the measured `import rcp` → `numpy, rcp_core, yaml`.
- F2 (major, the C6 move command fails unconditionally) — FIXED. §P4 copies and sets `PYTHONPATH`; measured.
- F3 (major, the C3 proof is vacuous) — FIXED. §P3, with a control that fails.
- F4 (major, `apply` cannot run before validation) — FIXED. §The pipeline.
- F5 (major, the value parser breaks the documented `--set` grammar) — FIXED. `parse_value` follows the YAML ladder
  `cli/run.py:188-190` publishes, restricted so `yes` stays a string, and `VALUE_ERRORS` is the published table.
- F6 (major, `faults.py` cannot replace both sides, install line has no parameter) — FIXED. Open question 2 is
  restated; `config_error` has a home in `document.py`; the install line is a parameter of `faults.require`.
- F7 (major, the spec is missing the result half and the parameters) — FIXED. `Command` carries `result`;
  `build_parser` takes `prog`, `brand`, `envelope_schema_id`, `exit_codes`; `--json` and `--traceback` are declared
  once, in `build_parser`.
- F8 (major, named requirements have no home) — FIXED. §Coverage.
- F9 (minor, `--unset` cites a requirement rcp never made) — FIXED. The citation is deleted; the token is stated as
  a library decision.
- F10 (minor, `CODES` contradicts "owned per package") — FIXED. `register_fault_codes` is an explicit bootstrap call,
  with the order check inside it.

## Repair report r2

One line per round-2 finding. `FIXED` names what changed in this document. No round-2 finding is disputed.

**Review 1, round 2**

- B1 (blocker, P3 cannot fail; env half vacuous, file half root-filtered) — FIXED. §P3 replaces the event name with
  an `os.environ` proxy and an `os.getenv` wrapper, records every `open` with no root filter, and quotes two controls
  that fail: a stub that prints `data reads: ['/etc/hostname']` and `env reads: ['HOME']`, and `rcp.data.dataset`
  that prints the four suite YAML files and a non-empty env list.
- B2 (blocker, P4 cannot run and cannot fail) — FIXED. §P4 copies `packages/configkit/src/configkit`, imports from
  `/tmp/probe`, asserts `__file__` ends in `/tmp/probe/configkit/__init__.py`, and adds a negative control whose
  `__init__.py` imports `rcp` and fails. Measured: the distribution-directory copy prints `None`.
- B3 (blocker, rcp 8 and rcp 20 missed) — FIXED. §audit.py gains `scan(path)` for source facts (a path literal, a
  group name, a seam, an `__all__`, a call site) and `pure(validator)` for rcp 20, with a failing control. Rows 8 and
  20 name them.
- M1 (major, C1 is false; P1 cannot see a lazy `yaml`) — FIXED. §C1 is an import-time condition with PyYAML as an
  optional extra; §P1 pins the `pydantic` closure at test time and adds a lazy-import check that expects the
  `optional.missing` fault.
- M2 (major, the manifest-level C2 argument is false) — FIXED. §Placement deletes it and records the measured
  editable install; §P2 adds a subprocess whose `sys.path` excludes the checkout, and demotes the manifest to a third
  check.
- M3 (major, the warning-code set is per consumer) — FIXED. §faults.py declares `WARNING_CODES` on the record and
  `warn(record, code)`; `audit` checks the declaration.
- M4 (major, `TaskKind` is a closed set) — FIXED. §vocab.py makes `TaskKind` an open `str` subclass, moves
  `DECISION_SCALE` to the consumer, and cites `emb-train-merged.md:438`.
- M5 (major, emb-train 20 is Python-only) — FIXED. §plugins.py adds `register_from(spec, registry=)` over the
  document field `models.register`, and the refusal error names the field.
- M6 (major, `render=` and the verb's human lines are absent) — FIXED. `Command.human_lines` and
  `build_parser(..., render=)` are in the API, and `envelope()` takes the same callable.
- M7 (major, three rows name a missing symbol) — FIXED. `MetricKey` and `SERVE_KEYS` are in §vocab.py; `DoctorFacts`
  is in §commands.py; all three rows name them.
- M8 (major, the absolute-path refusal contradicts the loader) — FIXED. §document.py keeps an absolute path, cites
  `emb_train_cli/config.py:111`, and counts the change as a third change to the five v0 rules.
- M9 (major, P5 and P6 test the library against itself) — FIXED. §P5 and §P6 move to the consumer suites, with one
  shared `contracts/seams.toml`; the library keeps only its own half.
- M10 (major, the value ladder changes two behaviours) — FIXED. §overrides.py is `yaml.safe_load` on the stripped
  text, empty → `""`, exactly `support/config.py:69`; `key=` and `yes` are pinned.
- M11 (major, rcp 30 has no removable marker and no call-site report) — FIXED. `DocumentId.removable` names the
  spelling; `audit.scan` reports the call sites.
- M12 (major, six rows claim an `audit` check it cannot make) — FIXED. §audit.py splits into `audit` and `scan`, and
  rows rcp 30, 44, emb-train 23, 32, emb-eval S18 and S25 name `scan`.
- m1 (minor, `exit_help` absent) — FIXED. `build_parser` takes `exit_help`.
- m2 (minor, `register_field_type` is a module global, no schema hook) — FIXED. It takes a `FieldTypes` registry and
  `FieldTypes.schema_hook`.
- m3 (minor, P2's grep has holes) — FIXED. The grep adds `__import__`, `find_spec` and `pkgutil`, and the run-time
  check is primary.
- m4 (minor, P4's five-name blocklist) — FIXED. P4 uses P1's closed-set check.
- m5 (minor, `uv run` can write a tracked file) — FIXED. P2 and P4 run `uv run --no-sync`.
- m6 (minor, wrong citation for emb-eval M36) — FIXED. `scoring/registry.py:470-475` is the `import_module`; `:432`
  is `METRIC_REGISTRY`.
- m7 (minor, "nine models" is wrong) — FIXED. §roles.py says eighteen class-level declarations across thirteen
  modules, measured, and notes the source's "nine" is stale.
- m8 (minor, DEBT 207 overstates the fix) — FIXED. The row calls the generic row a label and names the consumer
  change that raises the tree's error.
- m9 (minor, rcp 38 is partial and does not say so) — FIXED. Row 38 is marked partial by design, with the reason.
- m10 (minor, rcp 1's `execute` and `walk` have no function) — FIXED. Row 1 names the mapping from each requirement
  name to the library name.

**Review 2, round 2**

- F1 (blocker, P3 cannot see an import-time environment read; the quoted control is false) — FIXED. §P3, as B1: no
  `os.getenv` event name, a proxy for reads, every `open` recorded, and the corrected control output.
- F2 (blocker, P4 cannot produce its expected output) — FIXED. §P4 copies `src/configkit` and expects
  `/tmp/probe/configkit/__init__.py`; measured on a stub, with a negative control.
- F3 (major, the library's own test suite breaks the directory move and ships a personal path) — FIXED. §Tests moves
  the `rcp.data.dataset` control to rcp's suite, keeps the library's tests consumer-free, and §P4 carries no
  `/root/repos/rcp` path.
- F4 (major, `ports.py` cannot serve `storage.py`) — FIXED. §ports.py adds `AtomicWriter` and `UriIO`, both fields in
  `Ports`, and `read_uri`/`write_uri` take `ports`.
- F5 (major, parameterless lookups imply module-global registries) — FIXED. `validation_hooks(store)`,
  `registry(documents)` and `accepted_schema_ids(document)` all take a parameter.
- F6 (minor, P1's allow-list is environment-dependent) — FIXED. §P1 asserts the `pydantic` closure at test time and
  quotes the measured four-name list from the rcp venv.
- F7 (minor, the canonical digest has no stated successor) — FIXED. §identity.py states that `rcp_core` and
  `emb_eval_contracts.identity` re-export the function after the extraction, and the duplication is a test-guarded
  exclusion until then.
- F8 (minor, emb-eval X1 is missing) — FIXED. X1 is a row in the emb-eval coverage table, with S2's home.
- F9 (minor, two measured numbers do not reproduce at the stated precision) — FIXED. §C1 quotes the module count as
  load-bearing and the seconds as one sample (2.35 s, 2.52 s, 2.62 s).
