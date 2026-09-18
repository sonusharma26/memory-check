# MemoryCheck

**Your memory API said it was deleted. MemoryCheck found it after restart.**

MemoryCheck is a pytest-compatible lifecycle conformance testing toolkit for persistent AI memory systems. It checks correction, deletion, restart durability, and user isolation with deterministic canaries, capability-aware contracts, separate API/storage observations, and private diagnostic traces. It is not a memory framework, a compliance certification, or an LLM benchmark.

## Install and integrate

MemoryCheck is currently a source release candidate, so install it from this checkout:

```powershell
python -m pip install -e .
memorycheck init
```

`memorycheck init` creates a small standalone `memorycheck.toml`; it never edits an existing `pyproject.toml`. The default configuration uses the bundled local reference adapter, so you can add the fixture to a pytest test immediately:

```python
def test_a_deleted_memory_does_not_return(memorycheck):
    value = memorycheck.canary("PREFERENCE")
    fact = memorycheck.create(value, user_id="alice")
    memorycheck.delete(fact)
    memorycheck.restart()
    memorycheck.expect("MEMORYCHECK_PREFERENCE", user_id="alice").excludes(value)
```

```powershell
pytest
```

The pytest plugin is installed automatically. Each test receives a unique namespace, writes redacted failure traces only when needed, and cleans up the synthetic records it owns.

### Connect a persistent backend

Generate an adapter-specific configuration instead of hand-editing project metadata:

```powershell
memorycheck init --adapter langgraph
# Set MEMORYCHECK_POSTGRES_DSN to a dedicated test database.
python -m pip install -e ".[postgres]"
memorycheck doctor --probe
```

For Mem0, use `memorycheck init --adapter mem0`, install `memorycheck[mem0]`, and provide the generated configuration's private `mem0-config.json`. For a custom application adapter, use the short factory example below. See [adapter setup](docs/ADAPTERS.md) for all supported options.

For any backend, run `memorycheck doctor --probe` before a full suite: it validates configuration and exercises a short synthetic lifecycle. A `PASS` only covers the contract's available observations; `SKIP` is an explicit coverage gap, never a pass.

## See it catch a bug

From this extracted source directory, use a Python 3.10–3.13 virtual environment:

```bash
python -m pip install -e ".[dev]"
python -m memorycheck run builtin:delete_survives_restart --adapter broken-restart
```

The second command is **supposed to return exit code 1**. `BrokenRestartMemory` deletes only its runtime copy; the next restart reloads the old persistent value. The report identifies the query after restart, distinguishes API from storage, and prints the exact JSON artifact path.

```text
FAIL delete_survives_restart [BrokenRestartMemory]

Oracle results at event #07:
  API       present      FAIL
  STORAGE   present      not asserted
  CONTEXT   unavailable  NOT AVAILABLE
  BEHAVIOR  unavailable  NOT AVAILABLE

First divergence:
  First observed at #07 QUERY, after #06 RESTART

Trace artifact: .memorycheck/traces/delete_survives_restart--<run-id>.json
```

Values and native identifiers are redacted by default. Add `--verbose --trace-values synthetic` for a timeline with registered synthetic canaries, not real payloads. The full recorded example is in [docs/examples](docs/examples/README.md).

Then run the correct implementation:

```bash
pytest --memorycheck -q
memorycheck doctor
memorycheck adapter check
```

`pytest --memorycheck` adds the ten built-in contracts to your normal test collection. The default adapter is a fresh on-disk reference backend per test. No API keys, external services, models, or embeddings are needed for the reference backend.

This archive is a source release candidate; it has not been published to PyPI. The validation performed for this handoff, and the unexecuted packaging/live-provider gates, are recorded in [docs/VALIDATION.md](docs/VALIDATION.md).

## One fixture, one useful test

Create `test_memory.py`:

```python
def test_deleted_fact_stays_deleted(memorycheck):
    value = memorycheck.canary("REGION")
    fact = memorycheck.create(value, user_id="alice")
    memorycheck.expect("MEMORYCHECK_REGION", user_id="alice").contains(value)
    memorycheck.delete(fact)  # The logical scope travels with the reference.
    memorycheck.restart()
    memorycheck.expect("MEMORYCHECK_REGION", user_id="alice").excludes(value)
```

```bash
pytest test_memory.py -q
```

The fixture creates a unique run namespace, records observations, and cleans up owned synthetic state after the lifecycle. Unsupported semantics produce a reasoned skip before contract mutations. Successful tests remain quiet.

## Connect a backend

Configure a named adapter in `pyproject.toml`:

```toml
[tool.memorycheck]
adapter = "mem0"
trace_values = "redacted"
operation_timeout = 10
restart_timeout = 15
contract_timeout = 60

[tool.memorycheck.adapter_options]
config_file = "mem0-config.json"
```

Install the corresponding extra, supply a dedicated test configuration, then run diagnostics and contracts:

```bash
python -m pip install -e ".[mem0]"
memorycheck doctor
memorycheck adapter check
pytest --memorycheck -q
```

Alternatively, override `memory_adapter` in `conftest.py`:

```python
import pytest
from memorycheck import close_adapter
from memorycheck.adapters import Mem0Adapter

@pytest.fixture
def memory_adapter(my_mem0_factory):
    # Must return a fresh runtime connected to the SAME persistent test storage.
    adapter = Mem0Adapter(factory=my_mem0_factory)
    try:
        yield adapter
    finally:
        close_adapter(adapter)  # Will not close a quarantined, in-flight adapter.
```

A trusted `module:factory` is also supported. Contract YAML cannot import factories or execute code. LangGraph targets a synchronous persistent `BaseStore`, not an entire agent/checkpointer stack. See [adapter setup](docs/ADAPTERS.md), [configuration](docs/CONFIGURATION.md), and the explicit [compatibility matrix](docs/COMPATIBILITY.md).

## YAML contracts

```yaml
schema_version: "0.1"
name: delete_survives_restart_custom
scope:
  user_id: alice
steps:
  - create:
      id: secret
      value: "{{canary:SECRET}}"
  - expect:
      query: MEMORYCHECK_SECRET
      contains: "{{canary:SECRET}}"
  - delete:
      id: secret
  - restart: {}
  - expect:
      query: MEMORYCHECK_SECRET
      excludes: "{{canary:SECRET}}"
```

```bash
memorycheck validate contracts/
memorycheck run contracts/
pytest examples/reference/correction.memorycheck.yaml -q
```

`*.memorycheck.yaml` and `*.memorycheck.yml` are collected conventionally. Other YAML is collected only when explicitly selected or with `--memorycheck-collect-yaml`. Source locations, typo suggestions, a versioned editor schema, inferred capabilities, and mutation-scope checks are included. [Contract reference](docs/CONTRACTS.md).

## Know what was actually verified

| Outcome | Meaning |
| --- | --- |
| PASS | The evaluated invariants were satisfied. This does not imply unavailable layers passed. |
| FAIL | An observation violated an invariant. |
| SKIP | A required capability/oracle was unavailable; the contract was not verified. |
| ERROR | Configuration, adapter behavior, observation completeness, timeout, cleanup, or artifact persistence prevented reliable execution. |

CLI exit codes are **0** for completed success, **1** for invariant failures, **2** for execution/configuration errors, **5** for no runnable contracts/all skipped, and **130** for interruption. ERROR takes precedence over FAIL in a mixed CLI run. Pytest keeps its native exit-code rules; the plugin reports infrastructure problems as `ERROR` and adds `memorycheck_status` to report/JUnit properties. See [status and trace semantics](docs/TRACES.md).

An API exclusion means a value was not returned by that query, not that it has been erased from every storage layer. Storage is asserted only when explicitly required, and an incomplete scan cannot prove absence. No adapter claims erasure of backups, provider histories, logs, or filesystem blocks.

## Useful commands

```bash
memorycheck list
memorycheck show delete_survives_restart
memorycheck doctor --probe
memorycheck adapter check
memorycheck run builtin --save-all --json .memorycheck/summary.json
memorycheck trace .memorycheck/traces/<exact-trace-name>.json
memorycheck cleanup                         # Read-only: lists private cleanup ledgers.
memorycheck cleanup --run-id mc_<32-hex> --apply
memorycheck --version --verbose
pytest -m "memorycheck and not memorycheck_destructive"
pytest --memorycheck --memorycheck-verbose
```

`doctor` initializes the configured adapter and validates its declarations; it does not silently claim to have exercised every method. `--probe` or `adapter check` explicitly runs a synthetic lifecycle. Adapter initialization may itself connect to storage or perform configured schema setup.

## Safety and reliability

Use dedicated test storage. Namespaces reduce accidental overlap; they cannot make a broken or malicious backend safe. MemoryCheck never invokes an unscoped global reset. Scoped cleanup runs **after** observations, cannot change recorded evidence, and is distinct from a deletion invariant.

Default deadlines are 10 seconds per operation, 15 seconds for restart, 60 seconds per contract, and 10 seconds total for cleanup. The portable daemon-thread fallback bounds the caller's wait, not an underlying transaction. A timed-out adapter is quarantined; automatic cleanup/close is blocked because a write may still be in flight. Configure provider-side network deadlines and a CI job timeout too. [Timeout and cleanup details](docs/RELIABILITY.md).

Only MemoryCheck-generated diagnostics are redacted. SDK live logging, user `print`, `pytest -s`, debugger state, and application logs are outside that boundary. Private `.memorycheck/runs/` manifests contain cleanup targets/scopes and must **not** be uploaded with traces. [Security policy](SECURITY.md).

## Development and release

There are ten focused source regression tests, not a large generic test suite:

```bash
python -m pip install --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
```

Source CI is configured for the declared Python range on Linux and Windows; a separate manually dispatched release workflow contains the wheel/sdist and installed-entry-point checks. Those build steps were not run while preparing this archive. Live Mem0 and PostgreSQL/LangGraph probes also remain maintainer-run gates, not claimed validation. See [release checklist](docs/RELEASING.md) and [contributing](CONTRIBUTING.md).

The [original product brief](docs/PRODUCT_SPEC.md) and [release-readiness brief](docs/RELEASE_BRIEF.md) are preserved for provenance. The current supported API is documented here and in [docs/API.md](docs/API.md).

MIT licensed.
