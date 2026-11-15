# configkit

The shared configuration and command-line library of [RCP-nDCG](https://github.com/cohere-ai/rcp): one value
rule, one canonical form, one document model, one override parser, one command tree. It is the library
`cohere-rcp` configures itself with, and its own distribution (`cohere-configkit`) so a second consumer can adopt
the configuration surface without the product's stack.

It needs `pydantic` only. The library imports the standard library and `pydantic` and nothing else: it imports
no module of the product (`rcp`, `rcp_core`, `rcp_vllm`), reads no file and no environment variable at import
time, and imports no YAML reader until a YAML document is read (that reader is the `[yaml]` extra, `PyYAML`).

The release builds the wheel and stages it in the release candidate's wheelhouse; publishing it to PyPI is a
recorded separate decision -- the `cohere-configkit` project does not exist yet
(`.github/workflows/release.yml` records the precondition) -- so install it from the checkout for now
(Python 3.12):

```bash
pip install ./packages/configkit
```

`pip install cohere-configkit` (or `cohere-configkit[yaml]`) once the project is published.

```python
from configkit import canonical_bytes, deep_merge, parse

# One value rule: two values are the same when their canonical bytes are, so `True` and `1` are not.
assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'

# One override grammar: `--set a.b=5` parses to the typed leaf it names, not to the string "5".
(override,) = parse(["a.b=5"])
assert (override.path, override.value) == ("a.b", 5)

# One merge rule: a child document wins key by key, recursively, and neither argument is changed.
assert deep_merge({"a": {"b": 0, "c": 1}}, {"a": {"b": 2}}) == {"a": {"b": 2, "c": 1}}
```

The design argument, the four proofs (standard library and `pydantic` at import time, the extraction, the
seams) and the module tree are in
[the library's concept page](https://github.com/cohere-ai/rcp/blob/main/docs/concepts/configkit.md).

Security reports: [SECURITY.md](https://github.com/cohere-ai/rcp/blob/main/SECURITY.md) (GitHub private
vulnerability reporting).
