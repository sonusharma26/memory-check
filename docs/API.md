# Public Python API

The supported public surface is explicitly exported through `memorycheck.__all__`.

| Surface | Purpose |
| --- | --- |
| `MemoryCheck`, `ContractRunner`, `RunResult` | Fluent lifecycle, YAML execution, and outcome handling. |
| `MemoryAdapter`, `validate_adapter`, `check_adapter`, `close_adapter` | Adapter boundary, static validation, synthetic conformance probe, safe bounded resource close. |
| `MemoryRecord`, `MemoryRef`, `QueryObservation`, `StorageSnapshot`, `Scope` | Normalized values and scope ownership. |
| `Contract`, `Step`, `parse_contract`, `load_contract`, `load_builtin`, `builtin_names` | Versioned contract construction/discovery. |
| `Settings`, `configure`, `canary`, `Canary` | Explicit defaults, per-context defaults, and synthetic values. |
| `MemoryCheckError` and documented subclasses | Separate configuration, validation, execution, timeout, oracle, cleanup, trace, and capability errors. |

`ContractError` aliases `ContractValidationError`; `UnsupportedCapability` aliases `UnsupportedCapabilityError` for source-snapshot compatibility. `InvariantViolation` is an `AssertionError`; `ContractExecutionError` is not. `RunResult.failed` is strictly invariant failure. `errored`, `skipped`, `passed`, and `unsuccessful` provide non-overlapping or explicitly aggregated checks.

```python
from memorycheck import ContractRunner, Settings
from memorycheck.adapters import ReferenceMemory

with ReferenceMemory() as adapter:
    result = ContractRunner(adapter, settings=Settings()).run("builtin:delete_survives_restart")
    result.assert_passed()
```

```python
from memorycheck import MemoryCheck, Settings
from memorycheck.adapters import ReferenceMemory

with ReferenceMemory() as adapter:
    with MemoryCheck(adapter, settings=Settings(trace_values="synthetic")) as check:
        value = check.canary("REGION")
        ref = check.create(value, user_id="alice")
        check.expect("MEMORYCHECK_REGION", user_id="alice").contains(value)
        check.delete(ref)
        check.restart()
        check.expect("MEMORYCHECK_REGION", user_id="alice").excludes(value)
```

Use a context manager, a pytest fixture, or explicitly call `finish()` so cleanup/final artifact status is recorded. `finish()` is idempotent. `check(contract)` starts a separate full contract and cannot be mixed with preceding fluent operations on the same checker. An already-finished or quarantined session cannot be used for another lifecycle.

`MemoryRef.scope` is logical scope; native generated scope is passed only at the adapter boundary. A mutation must use a reference or ID created by that session. An arbitrary foreign `MemoryRef` is rejected before touching the backend. Scope cannot be changed during update/delete.

`canary("LABEL")` generates a fresh typed string; `MemoryCheck.canary("LABEL")` is stable for that label within its run. A `seed` on the standalone generator is for deliberate deterministic replay, not secrecy. Never put secrets in a canary label.

Provider adapters are imported from `memorycheck.adapters`. Other nested modules are implementation details unless specifically documented for an extension. The 0.1 schema/API freeze intentionally replaces the original pre-release list-returning query boundary with `QueryObservation`.
