# Traces and outcome semantics

A trace is the provider-neutral record of a contract: ordered operations, captured observations, evaluated invariants, the first divergent observation boundary, runtime metadata, and cleanup outcome. The JSON schema is `memorycheck/trace/trace.schema.json`, version `"0.1"`.

| Status | Interpretation |
| --- | --- |
| `passed` | All required evaluated invariants were satisfied. |
| `failed` | At least one evaluated invariant was violated. |
| `skipped` | A required capability or oracle implementation was unavailable. |
| `error` | Execution or a reliable observation/artifact was not possible. |

`RunResult.failed` means only `failed`; use `errored` for errors or `unsuccessful` for either. `assert_passed()` raises `InvariantViolation` only for an invariant failure, `ContractExecutionError` for an execution error, and `UnsupportedCapabilityError` for a skip. Configuration/parser exceptions have separate public classes.

An API query and an independent storage inspection are different observations. Each layer has a visibility `status` and an independent `assertion_status` (`passed`, `failed`, `error`, or `not_checked`). Unavailable layers render as NOT AVAILABLE, not as a green check. API absence describes a retrieval response, not global erasure. Incomplete scans can establish positive evidence but cannot prove absence or emptiness.

`first_divergence` identifies the first failed/error event, its most recent state-changing operation, and the last verified observation. It is an observation boundary, **not proof of the root cause**. No suspected hidden state is invented. Cleanup happens afterward and is stored separately; cleanup errors can make the overall run ERROR without deleting the original failed event.

## Artifact paths

Failures and errors are persisted under `.memorycheck/traces/<contract>--<unique-run-id>.json`. Unique suffixes avoid parallel/repeated-run collisions; the exact path is printed. `--save-all`/`--memorycheck-save-all` also writes successful/skipped traces.

```bash
memorycheck trace .memorycheck/traces/<exact-file>.json
memorycheck run builtin --json .memorycheck/summary.json --save-all
```

`show-trace` remains an alias for `trace`. The reader validates its structural boundary and enforces a 16 MiB input limit. Writes use a same-directory temporary file, flush/fsync, and atomic replacement. New files are private on POSIX; inherited ACLs and security on other platforms still depend on the host. Failure to persist a requested artifact is an ERROR rather than a silent success.

## Redaction boundary

Default `trace_values="redacted"` replaces values, identifiers, queries, aliases, and scope values with per-run keyed tokens. Equal strings receive equal tokens within a trace, permitting comparison without exporting the originals. Keys are not persisted, so tokens are not intended as a cross-run correlation mechanism.

`trace_values="synthetic"` exposes only exact values registered by the canary generator/session. It does not unmask an arbitrary value merely because it resembles a canary. A canary plus other arbitrary text is not automatically safe. Labels must themselves be non-sensitive. Metadata and `QueryObservation.raw` are omitted in both modes. Arbitrary provider exception text, inspector notes, and provider-supplied coverage descriptions are not exported. Storage coverage is described generically in artifacts and precisely in adapter documentation/live `StorageSnapshot` objects.

Environment metadata is limited to MemoryCheck, adapter, provider and pytest versions, Python version, and platform name. Provider version is null when no distribution version is available; a fake-backed adapter is not labeled as a tested live provider. There are no API keys, connection strings, complete environment snapshots, hostname, or arbitrary memory metadata in traces.

A verbose reproduction is a **redacted operation outline**, not an executable replay of original data and not an automatically minimized counterexample. Use the original YAML/Python contract to reproduce behavior. Raw values remain in live Python objects for execution and are outside the artifact privacy boundary.

Private `.memorycheck/runs/` manifests are operational cleanup state, not trace artifacts. Do not upload them. They contain native target information/scopes and no memory payloads or raw credentials.

## Pytest and CI

The plugin reports infrastructure errors with the `ERROR`/`E` category, invariant failures with normal FAIL/F, and unsupported contracts as SKIP. It redacts its failure representation and suppresses captured backend log sections on MemoryCheck failures. `--memorycheck-verbose` changes detail, not the redaction policy. It cannot intercept user `-s`, live log handlers, arbitrary application output, or debugger locals.

Pytest keeps native exit codes: ordinary test failures and plugin-reported call-phase errors make the session unsuccessful. CLI codes are 0 success, 1 invariant failure, 2 execution/configuration error, 5 empty/all-skipped selection, and 130 interruption. A mixed CLI run gives ERROR precedence.

The plugin adds `memorycheck_status` and, when present, `memorycheck_trace` to report properties. Standard pytest JUnit still uses its native phase-based XML element classification; a call-phase exception can be a `<failure>` even when MemoryCheck labels it ERROR. Consumers needing exact lifecycle distinctions should read `memorycheck_status` or the JSON trace, rather than infer them from the XML element alone.
