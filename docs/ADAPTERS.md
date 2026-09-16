# Adapter integration and conformance

Adapters translate lifecycle operations; they must not repair bad backend observations. Do not filter query results by locally remembered IDs or owner before returning them, since that would hide isolation and deletion bugs.

## Frozen boundary

```python
from memorycheck import MemoryAdapter, MemoryRecord, QueryObservation, Scope, StorageSnapshot

class MyAdapter(MemoryAdapter):
    capabilities = frozenset({"create", "query", "delete", "user_scope"})

    def create(self, value: str, *, scope: Scope, alias: str | None = None) -> str:
        ...  # Return one new nonempty native ID.

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        ...  # QueryObservation(tuple(MemoryRecord(...), ...)), never raw SDK dicts.

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        ...  # Propagate backend errors rather than treating them as success.
```

This outline is a contract for adapter authors, not a runnable backend. A complete deterministic implementation is in `memorycheck/adapters/reference.py`.

`QueryObservation` is an immutable sequence of `MemoryRecord` values; its `raw` field is available in process only and is never serialized. `MemoryRecord` requires a nonempty native `id`, text `value`, a `Scope`, and mapping metadata. Metadata is also kept out of exported diagnostics. `StorageSnapshot` requires a stated coverage boundary, normalized records, and a truthful `complete` flag.

`validate_adapter(adapter)` checks synchronous method/capability declarations without lifecycle writes. `check_adapter(adapter)` and `memorycheck adapter check` perform one isolated synthetic lifecycle, exercising available create/query/update/delete/restart/isolation/storage behavior. The probe is not a blanket backend certification; its outcomes use the same PASS/FAIL/SKIP/ERROR semantics as contracts.

## Mem0 OSS

```bash
python -m pip install -e ".[mem0]"
```

```toml
[tool.memorycheck]
adapter = "mem0"
[tool.memorycheck.adapter_options]
config_file = "mem0-config.json"
search_api = "auto"
search_limit = 100
```

Use a dedicated configuration file; an example is in `examples/mem0/config.example.json`. `Mem0Adapter.from_config(config)` constructs a fresh synchronous `Memory` through a factory on initialization/restart. Alternatively pass `memory=<existing instance>` or `factory=<fresh-runtime callable>`. Borrowed instances do **not** advertise restart and are not closed by the adapter. A context-manager factory or a `close` callback can manage owned backend resources.

Writes call the public `add(..., infer=False)` interface to preserve canary text; embeddings and remote provider calls may still occur. Retrieval uses the SDK's declared signature for legacy `user_id`/`run_id` keywords versus newer `filters`, and `limit` versus `top_k`. Calls are not retried with weaker filters after an error. Rows are normalized from ID/memory fields; ownership information is not invented when the SDK omits it.

User and session scopes are supported; tenant scope is not emulated. Mem0's namespace and MemoryCheck's per-run user scopes are separate protections. Mutation IDs must have been created by this adapter. Restart preserves native IDs, scope bookkeeping, and the native namespace while reconstructing the runtime.

Storage inspection is available only when the caller provides `storage_inspector(memory, native_scope) -> StorageSnapshot`. A second retrieval search is not a storage oracle. Scoped cleanup is advertised only when the client exposes synchronous `delete_all`; it is called with a generated user boundary and never as an unscoped reset. Provider histories, logs, backups, and other hidden layers are outside that cleanup claim.

`from_config` supplies a non-secret target fingerprint for private stale-cleanup manifests. Direct/custom factories need an explicit non-secret `cleanup_target` identifier to enable safe target matching for replay. Do not change a target identifier to point to a different persistent store.

## LangGraph persistent store

```bash
python -m pip install -e ".[postgres]"
# Set MEMORYCHECK_POSTGRES_DSN in the shell/CI secret store.
```

```toml
[tool.memorycheck]
adapter = "langgraph"
[tool.memorycheck.adapter_options]
connection_env = "MEMORYCHECK_POSTGRES_DSN"
setup = true
query_mode = "lexical"
```

`LangGraphAdapter.from_postgres(dsn, setup=True)` reconnects to the same PostgreSQL store on restart. It owns the connection context and can initialize store schemas. A fresh `InMemoryStore` factory is **not** a persistent restart implementation; it should lose data, and the durability contract should catch that. Borrowed stores have no restart capability.

The adapter uses fixed-depth encoded scope segments so delimiter and prefix collisions do not merge users, tenants, or sessions. Default lexical mode scans the public store search API with bounded pagination, then matches query text/aliases without filtering away foreign owners. Repeated pagination records or exceeding `max_records` is an ERROR, not a passing absence assertion. Semantic mode uses configured store retrieval and reports query-visible results rather than pretending it is a full storage scan.

A physical storage oracle is optional and must be supplied as `storage_inspector(store, native_namespace) -> StorageSnapshot`. Store retrieval itself is not labeled physical inspection. Scoped cleanup repeatedly searches the exact generated namespace and deletes its items, rejecting a foreign namespace, repeated key, or excessive scan. Cleanup filtering is intentionally stricter than oracle filtering because it must not delete leaked foreign records.

`from_postgres` supplies a private target fingerprint. For another persistent factory, supply a stable non-secret `cleanup_target` to enable stale cleanup replay.

## Resource ownership, timeout, and restart requirements

A restart means **discard transient adapter/runtime state and create a fresh runtime while preserving the same persistent target**. It is not a flush, a cache no-op, or permission to clear storage. Factories that return the same runtime object are rejected. Each adapter's `restart_description` is available to diagnostics.

The POSIX main-thread signal deadline path preserves thread-affine calls where it is usable. The portable fallback invokes calls in daemon threads; custom clients must support calls from those threads. For strict thread-affine/noninterruptible clients, supply an integration that enforces native backend deadlines and use an appropriate process/job boundary. A timed-out adapter must never be reused.

`close_adapter(adapter)` bounds close and refuses it when an operation has timed out. Overriding pytest fixtures own their adapter's resource finalization and should use that helper. Scoped test-state cleanup happens in the lifecycle session, not in resource close. Adapters need not implement global `clear_all`, and MemoryCheck never uses an unscoped global reset.

See [compatibility evidence](COMPATIBILITY.md), [reliability](RELIABILITY.md), and [the primary API references used for integration](REFERENCES.md).
