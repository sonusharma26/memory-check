# Architecture and vocabulary

A **Contract** describes **Operations**, captured **Observations**, and **Invariants**. An **Adapter** translates operations and declares **Capabilities**. A **Scope** identifies a logical ownership boundary. An **Oracle** observes one layer. A **Trace** records the lifecycle and its evidence. These terms are used consistently throughout the public API and documentation.

The strict YAML parser validates an entire contract and infers requirements before the runner writes. `ContractRunner` delegates every operation to the same `MemoryCheck` engine used by fluent pytest tests. The core engine knows capabilities and normalized values, not provider APIs.

Each session owns canary generation, native run-scope translation, a shared contract deadline, references created during that session, observations, and a cleanup ledger. Resource ownership belongs to adapters/factories/fixtures. Test-state cleanup and resource close are separate actions.

The API oracle accepts only `QueryObservation`; the storage oracle accepts only `StorageSnapshot` with an explicit complete/incomplete scan declaration. The engine must not hide leaked rows by locally filtering them to IDs or owners it expected. Required oracles participate in invariants; optional observations remain clearly not asserted. Unavailable/incomplete required evidence is ERROR or preflight SKIP, never a fabricated pass.

Serialization is a separate field-by-field privacy boundary: raw provider responses and memory metadata stay in process. Both the CLI and pytest renderer consume the same redacted trace representation. Metadata is an allowlisted environment envelope, not a platform/env dump. The renderer identifies first divergence without inferring a hidden root cause.

Cleanup is performed only after observations have ended. It has a separate deadline and outcome. A timed-out adapter is quarantined, so a later cleanup/close does not race a possible pending write. Durable generated-scope ledgers support explicit, target-checked replay where the adapter can restore safely.

`pytest_plugin.py` supplies fixtures, conventional/opt-in YAML collection, built-in collection, markers, and private result reporting. `cli.py` supplies discovery, validation, diagnostics, execution, trace rendering, and explicit cleanup. Both use the same settings, parser, runner, error hierarchy, and serializers.

No dashboard, hosted service, benchmark leaderboard, compliance engine, LLM judge, arbitrary provider marketplace, or property-testing/shrinking engine is part of 0.1.0. The verbose operation outline is not presented as an automatically minimized counterexample.
