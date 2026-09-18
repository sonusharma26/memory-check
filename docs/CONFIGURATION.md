# Configuration

Configuration precedence is programmatic defaults, nearest discovered `memorycheck.toml` (or `pyproject.toml` when no standalone file exists), then explicit CLI/pytest overrides. Run `memorycheck init` to create a standalone file without editing your project metadata. An overriding `memory_adapter` fixture takes precedence for adapter construction; the MemoryCheck session still receives project trace, timeout, isolation, and cleanup settings. Relative configured paths resolve against the selected TOML file's directory, or the current project directory when no file exists.

`memorycheck.configure(...)` changes defaults for subsequently constructed Python sessions in the current context. It does not mutate existing sessions. Passing an explicit immutable `Settings(...)` to `MemoryCheck` or `ContractRunner` is preferable for library integrations and concurrent callers.

```toml
[tool.memorycheck]
adapter = "reference"
trace_dir = ".memorycheck/traces"
run_dir = ".memorycheck/runs"
trace_values = "redacted"       # Or "synthetic"; no raw export mode.
save_all = false
operation_timeout = 10.0
restart_timeout = 15.0
contract_timeout = 60.0
cleanup_timeout = 10.0
timeout_mode = "auto"           # auto, signal, thread
isolate = true
cleanup = true

# Optional: a persistent reference target for explicit stale-run cleanup.
[tool.memorycheck.adapter_options]
path = ".memorycheck/reference.json"
```

All keys are validated; unknown settings, malformed types, non-finite/non-positive deadlines, or invalid TOML fail before a contract runs. `project_root` is derived from the configuration file and cannot be set in TOML. Do not use one reference JSON file concurrently: it is deliberately single-writer. The default pytest reference fixture uses a different file per test instead.

## Selecting adapters

`reference`, `broken-delete`, `broken-restart`, `broken-update`, and `broken-isolation` accept only `adapter_options.path`.

`mem0` accepts `config_file` (JSON path) or `config` (a TOML table), plus `namespace`, `search_limit`, and `search_api`. The environment variable `MEMORYCHECK_MEM0_CONFIG` can supply the JSON path. Do not supply both an inline config and a config file. SDK credentials belong in the provider's documented secret mechanism or in a private local config, not in committed TOML.

`langgraph` creates a PostgreSQL `BaseStore` and accepts `connection_env` (default `MEMORYCHECK_POSTGRES_DSN`), `setup` (default true), `namespace`, `query_mode`, `page_size`, and `max_records`. The DSN is read from the named environment variable. `setup=true` permits schema initialization when the adapter is constructed, including by doctor. Use `setup=false` after provisioning schemas separately where this distinction matters.

A trusted `your_package.factory:make_adapter` receives `adapter_options` as keyword arguments and must return a `MemoryAdapter`. This is application code with full Python privileges, like `conftest.py`; never use factories/configuration from an untrusted project. YAML contracts cannot load code.

## Overrides

CLI operational commands accept `--config`, `--adapter`, `--trace-dir`, `--trace-values`, `--save-all`, `--no-cleanup`, `--operation-timeout`, `--restart-timeout`, `--contract-timeout`, `--cleanup-timeout`, and `--timeout-mode`.

Pytest equivalents have the `--memorycheck-` prefix, including `--memorycheck-config`, `--memorycheck-adapter`, `--memorycheck-no-cleanup`, and `--memorycheck-verbose`. `--memorycheck` adds built-ins; it is not necessary for a test that explicitly uses the `memorycheck` fixture.

Disabling `isolate` is a deliberate Python/TOML setting for specialized integrations, not a convenient CLI switch. It disables generated scope isolation; cleanup falls back to session-owned live identifiers rather than broad scope deletion.

MemoryCheck does not auto-load `.env`, expand environment templates in YAML, dump environment variables, or send telemetry. Installing/using a provider SDK may introduce that provider's separate logging/telemetry behavior.
