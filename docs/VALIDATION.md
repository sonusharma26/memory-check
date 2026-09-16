# Validation record — 2026-09-16

This record describes checks actually executed against the delivered source tree. It does not certify an unbuilt distribution, an unexecuted CI matrix, or a live external backend.

## Executed source checks

Environment: **Linux, Python 3.13.5, pytest 9.0.2, PyYAML 6.0.3, jsonschema 4.26.0**. No optional Mem0, LangGraph, or PostgreSQL provider SDK was installed or contacted.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```

**Final result: `10 passed in 6.83s`.** These are ten focused test functions: seven retained core regressions and three consolidated release regressions. No large generated self-test suite was added.

Core coverage includes the ten-contract reference corpus, independent persisted-state reconstruction, four deliberate lifecycle defects, typed Mem0/LangGraph API-shaped fakes, separate API/storage observations, incomplete storage scans, strict YAML/capability preflight, first-divergence reporting, CLI/pytest integration, and saved JSON traces validated against the schema.

Release coverage includes per-run namespaces, foreign-reference rejection, generated canaries, payload/metadata/raw redaction, private trace permissions, cleanup and safe stale-target replay, bounded thread deadlines, quarantine, typed execution errors, malformed adapter observations, TOML configuration, doctor/conformance/validation/discovery commands, and pytest PASS/FAIL/ERROR separation without sensitive provider text.

Some parent tests deliberately execute failing nested pytest suites or broken-backend CLI runs and verify their nonzero outcomes and redacted artifacts. The release integration case verifies ten built-in passes plus one intentional invariant failure and one intentional execution error. These expected nested outcomes are not failed self-tests.

## Additional executed checks

The offline reference examples ran directly from source with explicit plugin loading:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -p memorycheck.pytest_plugin examples/reference -q
```

Result: **`2 passed in 0.02s`** (one fluent example and one YAML contract). The actual invocation selected an external temporary trace-output directory; neither traces nor test caches from this run are part of the archive.

Three small safety smoke checks also completed: refusing cleanup replay while its originating run remains active; surfacing cleanup-only errors when a fluent context exits; and rejecting a malformed trace timestamp. These checks made no network calls and created no additional permanent test functions.

Static inspection passed for **56 Python files**, including parsing under Python 3.10 grammar; both JSON schemas; project TOML and declared public exports; all ten mirrored built-in contract pairs; the reference YAML example; five workflow/issue/example YAML configuration files; and 20 relative documentation links. Grammar parsing is not a substitute for running Python 3.10.

A fresh synthetic `BrokenRestartMemory` trace was captured, schema-validated, and saved under `docs/examples/`. It records the expected failed deletion-after-restart API assertion, separate storage observation, and completed post-observation cleanup. Native identifiers/scopes are redacted and raw/metadata fields omitted. `memorycheck --version --verbose` was checked locally.

Workflow YAML was parsed but no GitHub Actions workflow was dispatched. Static metadata/resource inspection is not a packaging build or an installed-artifact test.

## Not executed / remaining maintainer gates

**No package build was run.** No wheel or sdist was created, no editable/package installation was performed, no dependency installation was performed, and nothing was published. The release workflow and `scripts/distribution_smoke.py` are included for a separately authorized maintainer release; neither was executed.

Python 3.10–3.12 and Windows runtime matrix jobs were not executed here. The declared Python 3.10–3.13 Linux/Windows matrix and minimum-core-dependency job are configured in source CI, not claimed as passing.

No live Mem0 service, LangGraph SDK, PostgreSQL store, model API, credentials, or provider network behavior was exercised. Adapter fakes establish source-level interface and lifecycle behavior only. Exact live-provider version/coverage claims require the dedicated probes described in `COMPATIBILITY.md` and `RELEASING.md`.

The requested Sol/subagent feature was checked through available tools/plugin discovery but was unavailable. No work is represented as delegated: modifications and validation were performed directly.

## Source delivery

The ZIP includes the revised implementation, packaging metadata, ten versioned built-in contracts and editable copies, ten focused self-test functions, reference and opt-in provider examples, schemas, diagnostics/cleanup commands, documentation, CI/release workflow definitions, issue templates, the preserved original briefs, and a fresh redacted/synthetic demo trace.

Generated caches, local run ledgers, backend state, credentials, bytecode, and package-build outputs are excluded. `SHA256SUMS` records source-file integrity inside the archive; it is not a signature or a claim of publisher identity.
