# HANDOVER PLAN — `configkit`, the shared CLI and configuration library

**For the agent executing in `/root/repos/rcp`.**
This document is self-contained. It assumes no knowledge of the conversation that produced it.
The design argument and its six review verdicts are in `docs/concepts/configkit.md`; read that
only if you need to know why a decision was made. **This document is the contract.**

---

## 1. What to build, in one paragraph

A library named **`configkit`**, its own distribution at **`packages/configkit/`**, import name `configkit`. It
provides one way to read a YAML or JSON configuration document, validate it with pydantic, apply command-line
overrides, derive an identity, refuse with one exception base, and project a command specification onto both an
argparse parser and an MCP tool. **Three repositories consume it: `rcp`, `emb-train` and `emb-eval`.** It imports
**the standard library and `pydantic` and nothing else**, so that `cp -r packages/configkit /tmp/probe/` followed by
`import configkit` works with no edit.

## 2. Why this library, in the numbers that decided it

Three structural reviews found the same defect in all three repositories, independently:

```
rcp        157 findings · 50 requirements · 9 cross-folder seams
emb-train   33 requirements
emb-eval    25 structural · 45 major · 147 minor
```

**The root cause, quoted from the review:**

> "The tree's integration strategy is a shared contract, a hand-kept second copy, and one test that keeps them equal.
> **Every such triple is a second author.** When the test is written against the copy, the test certifies the copy and
> not the contract."

**Four measurements that make the cost concrete:**

| fact | number | source |
|---|---|---|
| one sibling import pulls the training stack | **1,402 modules, 2.9 s** vs 236 modules, 0.26 s | emb-train |
| `rcp` walks one config document by hand | **four times** — `runs/config.py:700`, `:854`, `:942`, plus six `redact_*_payload` | rcp |
| `emb-train`'s path-field tuple misses a real field | `model.checkpoint` | `emb_train_cli/config.py:36` |
| `emb-eval`'s two config doors disagree | `config check` exits 2 on the file `run` accepts | reproduced |

## 3. The six constraints. Every one is measured. None is negotiable.

| # | constraint | why | how it is checked |
|---|---|---|---|
| **C1** | **Standard library and `pydantic` only, at import time.** | emb-train measured 1,402 modules and 2.9 s for one sibling import. | P1 |
| **C2** | **No import of `rcp`, `emb_eval` or `emb_train`, anywhere.** | The operator's condition: the library must extract to `emb-utils`. | P2 |
| **C3** | **No import-time I/O, and no import-time environment read.** | `rcp/data/dataset.py:62` reads four YAML files at import today. | P3 |
| **C4** | **No `yaml`, `numpy` or `pandas` on the import path.** The YAML reader is a lazy import inside the function. | `rcp` requirement 42. | P1, and P1's lazy half |
| **C5** | **A seam between two packages is a public name, never an underscore path.** | Eleven such edges exist today in `rcp`. | P5 |
| **C6** | **The payload a front end prints is the library's model.** The front end formats; it does not project. | `rcp` requirement 45. | P6 |

**C1 and C2 are the same constraint in two directions**, and together they are the extraction condition.

## 4. The fifteen modules

**One directory, fifteen modules, one `__init__`.** Each module earns its place by a requirement, and each has a
stated prohibition — the thing it must never do, because doing it is how the copies drift back.

| module | owns | **must never** |
|---|---|---|
| `roles.py` | the seven-role vocabulary; identity, path resolution, redaction and local inputs **derive** from it | know a field name. It reads what the model declares. |
| `document.py` | `load(path, model, base)`; the five v0 rules; `extends` | import YAML at module level, or read the environment |
| `overrides.py` | `Override(path, value, source)`; one grammar; one value parser; `apply`; `unset` | keep the raw string beyond the message, or mutate a model |
| `identity.py` | the `CONTENT` payload; one canonical form; `hash_payload`; `short` | invent a canonical form the consumers do not already produce |
| `schemas.py` | the schema-id registry; `home_of` | own a consumer's document |
| `faults.py` | one `Fault` base; a data-driven code table | bake a CLI hint or an install line into a raise |
| `commands.py` | one command spec → argparse flags, validation, help, and the MCP tool | know a verb name. It reads a spec. |
| `ports.py` | the `AtomicWriter` and `UriIO` seam that `storage.py` needs | build a graph, or import torch |
| `plugins.py` | entry-point **lookup**, parameterised | hold a module-global registry a test cannot replace |
| `storage.py` | the atomic-write and URI handlers | assume the caller's storage |
| `vocab.py` | the numeric fits and key types the library owns | ship a benchmark name, a data pin, or a consumer constant |
| `spec.py` | the shared types; `OptionSchema` | import a service to define a type |
| `optional.py` | `optional.require("yaml", ...)` with the consumer's install hint | bake a distribution name into the hint |
| `log.py` | the record shape | install a handler at import, or print |
| `audit.py` | the completeness check, as a **function a test calls**; `scan` | run at import, or at a run |
| `__init__.py` | the public surface and `__all__` | import anything but the fifteen above |

### 4.1 `roles.py` — the vocabulary, and it is the library's, not the consumer's

```python
class Role(StrEnum):
    CONTENT = "content"   # part of the identity; a change makes a different document
    RUNTIME = "runtime"   # affects how it runs, not what it is
    PATH    = "path"      # resolved against a declared base
    URI     = "uri"       # redacted in a report
    SECRET  = "secret"    # redacted, and never written to a log
    ENV     = "env"       # a name, not a value
    OUTPUT  = "output"    # written, never read

def roles_of(model: type[BaseModel]) -> Mapping[str, Role]
def identity_of(model: BaseModel) -> Mapping[str, Any]
```

**The seven members are fixed.** A consumer declares a `Role` on each of its fields; **it does not get to choose the
vocabulary**, because "identity" and "redaction" are only meaningful if every repository means the same thing by
`SECRET`.

**Everything below derives from the role, and each is a live defect today:**

| derived | replaces | today's defect |
|---|---|---|
| `identity_of` | rcp's `IDENTITY_ROLES` walk; emb-eval's unit key | four hand-written walks of one document |
| path resolution | emb-train's `_PATH_FIELDS` tuple | `model.checkpoint` never resolves |
| redaction | rcp's seven `redact_*_payload` helpers | a new `SECRET` escapes redaction |
| the local-input listing | rcp's `local_inputs:700` | a new `URI` escapes the listing |

**A field with no declared `Role` is refused by `audit.scan`.** That is what stops "added without a decision".

### 4.2 `document.py` — one loader

```python
def load(path: Path, model: type[ModelT], *, base: Path | None = None) -> ModelT
```

**The five v0 rules, kept from emb-train verbatim:** UTF-8; unique keys; a required `schema` key; **resolve `PATH`
fields against the file's directory**; **resolve before hashing.**

**YAML is a lazy import.** `load` tries `import yaml` inside the function; if it is absent it raises
`optional.require`'s fault with the consumer's install line. **A JSON document never imports YAML** — that is C4.

**`base` is where a `PATH` override resolves.** The caller names it. **There is no default**, because a default is
the ambiguity that produced two `cfg8` values for one run in `emb-eval`.

### 4.3 `overrides.py` — one grammar, one value parser

```python
@dataclass(frozen=True)
class Override:
    path: str      # dotted, the model's own leaf path
    value: object  # already parsed
    source: str    # "--set", "env:EMB_EVAL_METRICS", "file:dev.yaml"

def parse(pairs: Sequence[str]) -> tuple[Override, ...]
def apply(draft: ModelT, overrides: Sequence[Override], *, base: Path) -> ModelT
def unset(draft: ModelT, path: str) -> ModelT
```

**One value parser**, and it is the grammar `rcp` ships today (`cli/run.py:188`): *"VALUE is a YAML literal: 5, true,
`[a, b]`, `{k: v}`"*. Order: `int`, `float`, `true`/`false`/`null`, JSON list or object, else a string.

**`apply` returns a new model.** emb-train's specs are `frozen=True`
(`emb_train_core/schema.py:132`) — **a frozen config is the shape a config should have**, so `apply` uses
`model_copy(update=...)` and never mutates.

**`apply` runs after `load` and before any validation.** That is what puts an override into the identity, and it
closes `emb-eval`'s cache bug **by construction** rather than by a check.

### 4.4 `faults.py` — one base, one table

```python
class Fault(Exception):
    """Every refusal in the library. A caller catches this once."""
    code: str

#: Declared and order-checked. Each row is (fault class, stable code, owner).
ROWS: tuple[tuple[type[Fault], str, str], ...] = (...)
```

**The CLI hint is resolved at output time.** A raise carries a class, a code and a message — **never an exit code and
never a suggestion.** That is `rcp` requirement 45 and `emb-train`'s "hints resolved at output time".

**`rcp/errors.py` is deleted, not re-exported**, because `rcp` is unreleased. Its rows move into `ROWS` with
`owner="rcp"`.

### 4.5 `commands.py` — one spec, two front ends

```python
@dataclass(frozen=True)
class Command:
    name: str
    request: type[BaseModel]     # the flags, the validation and the MCP schema all derive from this
    result: type[BaseModel]      # the payload the front end formats, never projects
    run: Callable[[Any], BaseModel]

def option_specs(command: Command) -> tuple[OptionSpec, ...]
def add_parser(subparsers, command: Command, *, render=..., exit_help=...) -> None   # argparse
def tool(command: Command) -> dict[str, Any]                                        # MCP
```

**The library ships the argparse adapter. `rcp` ships a click adapter** that renders the same `option_specs`.
**The cost in `rcp` is one adapter module, not a rewrite of eleven click groups.**

**The request model is the contract**, so `--json` cannot be declared twice and a subcommand cannot report its
parent's name.

### 4.6 `audit.py` — the check that fails a test

```python
def audit(*roots: type[BaseModel], brand: Brand | None = None) -> list[str]

def scan(
    *paths: Path,
    roots: Sequence[type[BaseModel]] = (),
    seam_prefix: str = "_",
    api_globs: Sequence[str] = ("**/*.py",),
    consumer_imports: Sequence[str] = (),
) -> list[str]
```

**Each `scan` parameter enables exactly one check, and each traces to a requirement:**

| parameter | check | requirement |
|---|---|---|
| `roots` | a field with no `Role`; a role on a field that does not exist | rcp 8, 20 |
| `seam_prefix` | a seam that is an underscore path, not a public name | rcp 44, C5 |
| `api_globs` | `**options: Any` in a declared surface | rcp 24 |
| `consumer_imports` | a library module that imports a consumer | C2 |

**A function, not a hook.** No import-time run (C3), no run-time cost. **A test in each consumer calls it.**

## 5. The pipeline, and it is one call

```
read  ->  resolve  ->  apply  ->  validate  ->  identity
```

| stage | module | what it does |
|---|---|---|
| read | `document.load` | UTF-8, unique keys, `schema` present, `extends` |
| resolve | `document.load` | every `PATH` against `base`; every `URI` recorded |
| apply | `overrides.apply` | every `Override`, in declared precedence, before validation |
| validate | pydantic | the model, and `OptionSchema` for a consumer's own options |
| identity | `identity.identity_of` | the `CONTENT` fields, one canonical form |

**`emb-eval`'s bug, named so you can see what the order buys**: `--compute` and `--score` changed the payload
**without entering the identity**, so an override reused the old cache. **Stage 3 before stage 5 makes that
impossible.**

## 6. The work packages

**Six packages. Each is a commit. Each has an `owns` set, a dependency, acceptance criteria and a mutation.**

### W1 — `roles.py` and `spec.py`

```yaml
owns:   packages/configkit/src/configkit/roles.py
        packages/configkit/src/configkit/spec.py
        packages/configkit/src/configkit/__init__.py
        packages/configkit/pyproject.toml
depends: []
```

**Deliverable**: the seven-role vocabulary, `roles_of`, `identity_of`, `OptionSchema`, and a package that imports
with `pydantic` and the standard library only.

**Acceptance**
- **A1**: `uv run pytest -q packages/configkit/tests/test_roles.py` exits 0.
- **A2**: a model with a field that declares no `Role` makes `roles_of` raise `faults.Fault`. Mutation: delete the
  raise → the test reddens.
- **A3**: `identity_of` returns the `CONTENT` fields and **not** a `RUNTIME`, `PATH`, `URI`, `SECRET`, `ENV` or
  `OUTPUT` field. Mutation: include `RUNTIME` → the test reddens.
- **A4**: `uv run python -c "import configkit"` exits 0 **with `yaml`, `numpy`, `pandas`, `torch` and `rcp` absent
  from `sys.modules`**.
- **A5**: the mutation for A4 — add `import yaml` to `roles.py` → A4 reddens.

### W2 — `document.py` and `optional.py`

```yaml
owns:   packages/configkit/src/configkit/document.py
        packages/configkit/src/configkit/optional.py
depends: [W1]
```

**Deliverable**: `load` with the five v0 rules, YAML as a lazy import, and `optional.require` naming the consumer's
install line.

**Acceptance**
- **B1**: a YAML document loads and validates. Mutation: drop the unique-key check → a duplicate-key document passes
  and the test reddens.
- **B2**: **`PATH` fields resolve against the file's directory**, not the cwd. Mutation: resolve against the cwd →
  the test reddens. **This is the emb-train defect; the test must use a file in a different directory from the cwd.**
- **B3**: resolution happens **before** hashing. Mutation: hash first → the two hashes differ and the test reddens.
- **B4**: with `sys.modules['yaml'] = None`, loading a YAML document raises the `optional.require` fault with the
  install line. **A JSON document loads with `yaml` absent.** Mutation: import `yaml` at module level → B4 reddens
  **and** A4 reddens.
- **B5**: a missing `schema` key is refused by name.

### W3 — `overrides.py`

```yaml
owns:   packages/configkit/src/configkit/overrides.py
depends: [W1, W2]
```

**Deliverable**: `Override`, `parse`, `apply`, `unset`, one value parser.

**Acceptance**
- **C1**: `--set a.b=5` sets an integer, `--set a.b=true` a boolean, `--set a.b=[1,2]` a list. Mutation: parse
  everything as a string → the tests redden.
- **C2**: **`apply` returns a new model and the draft is unchanged.** Mutation: mutate in place → a `frozen=True`
  model raises and the test reddens. **Use a frozen model in the test**, because that is emb-train's shape.
- **C3**: **an override enters the identity.** Mutation: apply after `identity_of` → the two identities are equal and
  the test reddens. **This is emb-eval's cache bug.**
- **C4**: `base` is required. Mutation: default it to `Path.cwd()` → the test that names a base reddens.
- **C5**: `--unset a.b` removes the field.
- **C6**: an override whose path names no leaf is refused with the path.

### W4 — `faults.py`, `identity.py`, `schemas.py`

```yaml
owns:   packages/configkit/src/configkit/faults.py
        packages/configkit/src/configkit/identity.py
        packages/configkit/src/configkit/schemas.py
depends: [W1]
```

**Deliverable**: the `Fault` base, the data-driven `ROWS` table, the canonical identity form, the schema-id registry.

**Acceptance**
- **D1**: `ROWS` is order-checked: a code declared twice fails a test. Mutation: duplicate a code → reddens.
- **D2**: every `Fault` subclass appears in `ROWS`. Mutation: add a subclass → reddens.
- **D3**: a raise carries a code and a message and **no exit code**. Mutation: add `exit_code` to `Fault` → the test
  that asserts the attribute is absent reddens.
- **D4**: `identity_of`'s canonical JSON is **byte-identical** to the form `rcp` and `emb-train` already produce.
  **Take the two existing forms as the fixture**, not a new one.
- **D5**: `short` returns the declared length; the length is one constant, not a literal in two places.

### W5 — `commands.py` and `log.py`

```yaml
owns:   packages/configkit/src/configkit/commands.py
        packages/configkit/src/configkit/log.py
depends: [W1, W4]
```

**Deliverable**: the command spec, the argparse projection, the MCP tool, the log record.

**Acceptance**
- **E1**: one `Command` produces an argparse parser whose flags equal `option_specs`. Mutation: hand-write one flag →
  reddens.
- **E2**: `tool(command)` produces an MCP schema from the same model. Mutation: declare a flag twice → reddens.
- **E3**: a subcommand's result carries its **own** name, not its parent's. **This is `rcp --json run list` reporting
  `"command": "rcp"`.**
- **E4**: `log.py` installs no handler at import. Mutation: add `logging.basicConfig` at module level → A4 reddens.

### W6 — `plugins.py`, `storage.py`, `ports.py`, `vocab.py`, `audit.py`

```yaml
owns:   packages/configkit/src/configkit/plugins.py
        packages/configkit/src/configkit/storage.py
        packages/configkit/src/configkit/ports.py
        packages/configkit/src/configkit/vocab.py
        packages/configkit/src/configkit/audit.py
depends: [W1, W2, W3, W4]
```

**Deliverable**: parameterised lookups, the atomic-writer and URI seam, the numeric fits, and `audit`/`scan`.

**Acceptance**
- **F1**: `plugins.lookup(entry_points)` takes its entry points as a parameter. Mutation: make it read a module
  global → the test that passes a stub reddens.
- **F2**: `audit.scan` finds a field with no `Role`. Mutation: remove the check → reddens.
- **F3**: `audit.scan` finds an underscore seam. Mutation: remove the check → reddens.
- **F4**: `audit.scan` finds `**options: Any` in a declared surface. Mutation: remove the check → reddens.
- **F5**: **no consumer constant lives in `vocab.py`.** `SERVE_KEYS` and `DECISION_SCALE` are **not** there. A test
  asserts `not hasattr(configkit.vocab, "SERVE_KEYS")`.

## 7. The six proofs, and every one is a command

**Run these from `/root/repos/rcp` after each work package. Each has an expected line, and each has a control that
must fail.**

### P1 — C1 and C4: standard library and `pydantic` at import time

```bash
uv run --no-sync python -c "
import importlib, pathlib, sys
import pydantic, configkit
pkg = pathlib.Path(configkit.__file__).parent
mods = sorted(f.stem for f in pkg.glob('*.py') if f.stem != '__init__')
for name in mods:
    importlib.import_module(f'configkit.{name}')
allowed = set(sys.stdlib_module_names) | {'configkit', '__main__'}
roots = sorted({n.split('.')[0] for n in sys.modules
                if n.split('.')[0] not in allowed and not n.startswith('_')})
print(len(mods), 'modules'); print(roots)"
```

**Expected**: the module count, then the `pydantic` closure and nothing else.
**Control that must fail**: add `import yaml` to any module → `yaml` appears.

**The enumeration is not decoration.** It imports every `*.py` file, **not** `import configkit`, so a module that
`__init__.py` does not export is still imported, still traced and still checked. **The count is printed so a new
module without a proof is visible.**

### P2 — C2: no `rcp` import, proved at run time

```bash
uv run --no-sync python -c "
import subprocess, sys
out = subprocess.run([sys.executable, '-c', 'import configkit; print(sorted(m for m in sys.modules if m.split(\".\")[0] in {\"rcp\",\"rcp_core\",\"emb_eval\",\"emb_train\"}))'],
                     capture_output=True, text=True, cwd='/tmp')
print(out.stdout.strip())"
```

**Expected**: `[]`. **Control**: add `import rcp.errors` → the list is non-empty.

**Note the `cwd='/tmp'`**: the rcp workspace installs `rcp` editable into the same venv, so a check run from the repo
root can see it. **A subprocess whose cwd is outside the checkout is the only honest check.**

### P3 — C3: no import-time I/O, and no import-time environment read

```bash
uv run --no-sync python -c "
import builtins, os, sys
reads, opens = [], []
_open = builtins.open
class Env(dict):
    def __getitem__(self, k): reads.append(k); return dict.__getitem__(self, k)
    def get(self, k, d=None): reads.append(k); return dict.get(self, k, d)
def _open_hook(*a, **k): opens.append(a[0] if a else k.get('file')); return _open(*a, **k)
builtins.open = _open_hook
os.environ = Env(os.environ)
import importlib, pathlib, configkit
pkg = pathlib.Path(configkit.__file__).parent
for f in sorted(pkg.glob('*.py')):
    if f.stem != '__init__':
        importlib.import_module(f'configkit.{f.stem}')
print('opens:', opens); print('env reads:', reads)"
```

**Expected**: `opens: []` and `env reads: []`. **Control**: add `os.environ.get('X')` at module level → `env reads`
is non-empty. **Add `open(__file__)` → `opens` is non-empty.**

**A `sys.addaudithook` does not work here and must not be used.** Measured on Python 3.12.9: **reading the environment
raises no audit event at all**; only `os.putenv` does. **A hook that listens for `os.getenv` is vacuous.**

### P4 — the extraction is a copy, and the copy imports

```bash
rm -rf /tmp/probe && mkdir -p /tmp/probe
cp -r packages/configkit/src/configkit /tmp/probe/configkit
cd /tmp && PYTHONPATH=/tmp/probe uv run --no-sync python -c "
import configkit
print(configkit.__file__)"
```

**Expected**: a path under `/tmp/probe/configkit/__init__.py`.
**Control**: add `from rcp.errors import Fault` → `ModuleNotFoundError`.

**`__file__` must not be `None`.** Copying the *distribution* directory rather than `src/configkit` yields an empty
**namespace package** — `import configkit` succeeds, imports nothing, and the proof passes while proving nothing.
**This is why the copy is `src/configkit` and the expected line is `__file__`.**

### P5 — C5: every seam is a public name, in each consumer

```bash
uv run python -c "
from configkit.audit import scan
print(scan('packages/rcp/src/rcp', seam_prefix='_'))"
```

**Expected**: `[]` after the consumer's refactor; before it, the findings are the consumer's remaining seams.
**Control**: add `from rcp.calibration._projection import x` → a finding names the path.

**`scan` takes the paths positionally** (`scan(*paths, ...)`): `scan(paths=[...])` raises `TypeError` and the proof
proves nothing.

**P5 runs in the consumer, not in the library.** The library provides the check; each repository runs it.

### P6 — C6: the payload is the library's model, in each consumer

**A test in each consumer** asserts that its front end renders `Command.result` and does not build its own dict.
**Mutation**: replace the render with a hand-built dict → the test reddens.

## 8. What is deliberately not in the library

**Not a dependency-injection container.** `rcp`'s command layer needs one; the library provides the spec, not the
graph.
**Not a logging framework.** `log.py` is a record shape. A consumer installs its own handler.
**Not a retry, cache or concurrency layer.** Every repository has one and they differ on purpose.
**Not a schema for any consumer's document.** The library owns the **reading**, not the **fields**.
**Not `SERVE_KEYS` or `DECISION_SCALE`.** **Consumer data.** A library constant would make two consumers agree **by
accident**, which is the defect the library exists to remove.

## 9. Definition of done, per commit

1. The narrowest test **failed before the change and passes after, and you watched it fail.**
2. `uv run ruff check packages/configkit && uv run ruff format --check packages/configkit` exits 0.
3. `uv run mypy packages/configkit/src` exits 0.
4. **All six proofs run, and each has been shown to fail once** — by the mutation named beside it.
5. `cp -r packages/configkit/src/configkit /tmp/probe/configkit` and `import configkit` works with **no edit**.
6. The commit message says what the change does **and what it does not prove**.

## 10. What happens after the library ships

**Three consumer agents, one per repository. You are not one of them.**

| repository | the refactor | the evidence it must produce |
|---|---|---|
| `rcp` | delete `errors.py`; move its rows to `faults.ROWS`; replace the four hand walks with `roles_of`; add the click adapter rendering `option_specs`; `audit.scan` returns `[]` | P5 and P6 green in `rcp` |
| `emb-train` | `load` from `configkit.document`; delete `_PATH_FIELDS`; move `run_id_for`/`config_hash` beside the schema; `audit.scan` returns `[]` | `import emb_train_cli.config` pulls **fewer than 300 modules** |
| `emb-eval` | one config door: `run --selection` loads through `configkit.document`; `--set` replaces the JSON edit; `config check` reads the same document; `audit.scan` returns `[]` | `config check` and `run` produce the **same `cfg8`** for one document |

**`emb-eval`'s evidence is the one that closes the original defect**: today `config check` exits 2 on the file `run`
accepts, and the two agree on `cfg8` **by construction, not by connection**.

## 11. Stop and escalate when

- A constraint C1–C6 cannot be met by a module's design. **Do not weaken the constraint; report it.**
- A requirement in §6's acceptance has no falsifiable mutation. **A criterion that cannot fail is a card defect.**
- A proof passes and you cannot make its control fail. **The proof is vacuous; report it rather than shipping it.**
- The extraction in P4 needs an edit. **That is the operator's condition failing, and it is a blocker.**

**Three review rounds is the ceiling per work package.** If a package has not converged after three, stop with both
verdicts.

---

## Appendix A — the coverage table

**§Coverage is authoritative. The module table in §4 is a summary.** A disagreement is a defect in the summary,
never in the coverage.

`rcp` 1–50 and `emb-train` 1–33 are enumerated in `docs/concepts/configkit.md` §Coverage, with the
module or the named exclusion for each. **Read that table before you start W1**: it is what a reviewer walks, and it
is the list of what this library owes.

## Appendix B — the six review verdicts

```
round 1   FAIL + FAIL   34 blockers · 46 majors · 59 of 102 requirements MISSED
round 2   FAIL + FAIL    2 blockers · 15 majors
round 3   FAIL + FAIL    2 blockers (narrow) · 4 majors · 5 minors
```

**The trajectory is the useful part**: 34 blockers, then 2, then 2 narrow ones, and **round 3's reviewers confirmed
C1–C6 pass the module table, `commands.py` needs no `rcp`, `document.py` needs no YAML at import, and the extraction
is a directory move.**

**Round 1's blockers were all in the proofs, not the design** — `-X importtime` traces imports rather than `open()`;
`/tmp` is not on `sys.path`; a blocklist contained `rcp`, which `import rcp.configkit` always puts in `sys.modules`.
**That is why §7 is commands with controls and not a paragraph.**

## Appendix C — the one sentence to carry

> **A proof is a command with an exit code, or it is a sentence. A sentence is not a check.**

**Four isolation proofs were written in prose in round 1, and two of them could not fail.** The reviewers ran them.
**Run yours.**
