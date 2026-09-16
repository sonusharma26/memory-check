# Captured synthetic failure trace

`broken_restart.trace.json` is a fresh source-only run of the deliberately broken reference backend, not a Mem0 or LangGraph production trace. `trace_values="synthetic"` makes registered generated canary values visible. Native IDs and scope labels remain redacted; raw provider data and metadata are omitted.

```bash
python -m memorycheck trace docs/examples/broken_restart.trace.json
```

The API initially hides the deleted canary while the active storage snapshot retains it. Query after restart sees the resurrected canary alongside a live control. The invariant requires API exclusion, so API is FAIL and storage is observed but not asserted. The trace records the first observation boundary, not a proven hidden-state root cause.

Cleanup ran after the failing observation and is recorded separately. The temporary local reference storage was removed after this trace was saved. The verbose operation outline is redacted and is not executable replay; rerun `builtin:delete_survives_restart` with `--adapter broken-restart` for a fresh reproduction.
