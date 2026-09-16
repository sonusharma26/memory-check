# Reliability, namespaces, deadlines, and cleanup

Each lifecycle has a unique `mc_<32 hex>` namespace. Supported logical user/tenant/session scopes are translated to generated native IDs; logical labels are hashed rather than embedded as potential personal data. Unscoped operations receive a generated default boundary when the adapter supports one. Adapters without any scope capability must isolate their own persistent target; a trace note makes that limitation explicit.

This is collision prevention, not a security boundary against a backend that ignores scopes. Always use dedicated test storage and a least-privilege test account. The reference JSON backend is single-writer; use separate paths for parallel tests. Default pytest fixtures do that automatically.

## Deadlines

| Setting | Default | Covers |
| --- | --- | --- |
| `operation_timeout` | 10 s | An adapter operation, oracle observation, and adapter initialization. |
| `restart_timeout` | 15 s | Runtime reconstruction. |
| `contract_timeout` | 60 s | Shared lifecycle operation/observation wall-clock budget. |
| `cleanup_timeout` | 10 s | Shared scoped-state cleanup budget; also the configured resource-close budget. |

All deadlines must be finite and positive. On a POSIX main thread with no active real-time alarm, auto mode temporarily uses a signal timer and restores the previous handler. Otherwise it uses a daemon-thread fallback. `signal` requires its preconditions explicitly; `thread` forces the portable path. MemoryCheck does not overwrite an active alarm to pretend to enforce its own.

The thread fallback bounds the caller's wait but cannot cancel a provider transaction or kill a running Python thread. A client created on the main thread may be thread-affine; such clients need an appropriately designed adapter/native timeout boundary. Signal handling also cannot guarantee immediate interruption of every native, uninterruptible call or a library that catches all `BaseException` values. Configure backend/network deadlines and CI job/process limits; these limitations are not hidden behind a claim of hard transactional cancellation.

Any in-flight operation timeout quarantines the adapter. No subsequent operation, automatic cleanup, or helper-driven close uses it. The ERROR states that the invariant was not evaluated; a write may still complete remotely. Retain the namespace for provider-side inspection and create a fresh adapter before trying another run. Expiring the overall contract budget before starting the next operation does not invent an in-flight write.

## Cleanup after a run

Before the first generated-scope write, MemoryCheck atomically records a private intent ledger in `.memorycheck/runs/`. It contains the run namespace, adapter class, generated native scopes, and optional non-secret target matching information; not arbitrary payloads or credentials.

Once observations have finished, adapters advertising `cleanup_scope` remove only those generated scopes. Reference cleanup reads the persistent snapshot independently, so intentionally broken deletion/update implementations do not leave demo artifacts. LangGraph rejects foreign namespace results during cleanup. Mem0 uses its scoped delete API only when exposed.

Without scoped cleanup, the session attempts deletion of known still-live IDs if supported. This is labeled `best_effort`, not full storage erasure or proof that hidden/stale artifacts are absent. Without delete, cleanup is `unavailable` and the ledger remains. `cleanup=false` deliberately retains a pending ledger for later cleanup. Disabling isolation never authorizes a broad cleanup of a user-supplied application scope.

Cleanup does not add lifecycle events or alter past observations. A cleanup error escalates the overall run to ERROR and preserves the failure evidence plus the ledger. A successful cleanup discards its ledger. Timeouts retain a blocked/uncertain ledger; they are not automatically replayed later.

## Explicit stale-run cleanup

```bash
memorycheck cleanup
memorycheck cleanup --run-id mc_<32-hex> --apply
```

The first command is read-only and does not contact a backend. The second requires an exact run ID and the currently configured adapter; no factory is imported from a ledger and bulk apply is not supported. Restore target matching must succeed before deletion. Reference cleanup requires configuring the same persistent `adapter_options.path`. Standard Mem0/LangGraph constructors can restore their own generated native namespace after matching the target fingerprint. Custom factories without a safe descriptor require provider-side cleanup instead.

Only trusted local, unedited ledgers are accepted. Symlink manifests, mismatched IDs, missing generated scope boundaries, unknown restore targets, and uncertain timed-out runs are refused. An active ledger whose originating process is still alive is also refused. Do not copy a ledger from an untrusted source into the run directory.

Resource close is separate from data cleanup. Factories/context managers own resources they create; borrowed SDK clients remain caller-owned. Use `close_adapter()` in custom fixtures to avoid closing a quarantined adapter while work may be pending.

Active-ledger replay additionally checks the originating PID on POSIX. An alive or unverifiable owner is refused. On Windows, active ledgers are conservatively refused; interrupted runs need backend-side verification. A finalized `pending` ledger can be replayed against its verified target.
