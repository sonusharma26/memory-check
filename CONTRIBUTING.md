# Contributing

Use Python 3.10–3.13 and a virtual environment. Source checks do not require a MemoryCheck build:

```bash
python -m pip install --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
```

Keep tests focused on meaningful regressions. The current suite has seven core tests and three release regressions; the built-in ten-contract corpus is a product feature, not a reason to add hundreds of unit tests. Never make ordinary CI depend on paid APIs, credentials, LLM judges, or live backends.

Adapters must preserve erroneous backend evidence, return normalized types, declare only implemented synchronous capabilities, document runtime restart behavior, and never confuse retrieval with physical inspection. Run `memorycheck adapter check` with a dedicated test backend before proposing an adapter change. Pin and record exact live-tested versions separately from fake-backed compatibility evidence.

A regression report should include MemoryCheck/Python/pytest/provider versions, a minimal synthetic contract, the first divergence, and a redacted trace. Do not attach `.memorycheck/runs/`, provider credentials, production payloads, or raw application logs. Changes to contract/trace schemas or exported names require an explicit migration note.

Release publishing is a maintainer action. Do not silently run builds or publish artifacts as part of unrelated source changes. Follow `docs/RELEASING.md` and its recorded gates.
