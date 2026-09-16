"""Adapter structural preflight and a small opt-in behavioral probe."""
from __future__ import annotations

import inspect
import re

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import AdapterConfigurationError

CAPABILITIES = frozenset({
    "create", "query", "update", "delete", "restart", "inspect_storage", "inspect_context",
    "inspect_behavior", "clear_all", "consolidate", "summarize", "user_scope", "tenant_scope",
    "session_scope", "cleanup_scope",
})


def valid_capability(value: str) -> bool:
    return isinstance(value, str) and (value in CAPABILITIES or bool(re.fullmatch(r"scope:[a-zA-Z][a-zA-Z0-9_-]*", value)))


def validate_adapter(adapter: MemoryAdapter) -> None:
    """Validate declarations without creating any test data."""
    if not isinstance(adapter, MemoryAdapter):
        raise AdapterConfigurationError("Adapter must be a MemoryAdapter instance")
    if not isinstance(adapter.capabilities, (set, frozenset)) or any(not valid_capability(c) for c in adapter.capabilities):
        raise AdapterConfigurationError("Adapter capabilities must be a set of documented capability names")
    for capability in adapter.capabilities:
        if capability.endswith("_scope") and capability != "cleanup_scope" or capability.startswith("scope:"):
            continue
        method = getattr(adapter, capability, None)
        if capability in {"inspect_context", "inspect_behavior"}:
            continue  # Extension oracles can provide these externally.
        if not callable(method) or inspect.iscoroutinefunction(method):
            raise AdapterConfigurationError(f"Declared capability {capability} needs a synchronous method")
        base = getattr(MemoryAdapter, capability, None)
        if base is not None and getattr(method, "__func__", None) is base:
            raise AdapterConfigurationError(f"Declared capability {capability} still uses the unsupported base implementation")
    if not isinstance(adapter.name, str) or not adapter.name:
        raise AdapterConfigurationError("Adapter.name must be nonempty text")


def check_adapter(adapter: MemoryAdapter, *, settings=None):
    """Exercise advertised operations using one isolated synthetic lifecycle.

    Shape/auth/timeout problems are ERROR. Observed behavioral violations are
    FAIL. Static declarations alone are not evidence of backend correctness.
    """
    from memorycheck.contracts.parser import contract_from_mapping
    from memorycheck.runner.executor import ContractRunner
    validate_adapter(adapter)
    steps = [
        {"create": {"id": "probe", "value": "{{canary:PROBE}}", "user_id": "alice"}},
        {"expect": {"query": "MEMORYCHECK", "contains": "{{canary:PROBE}}", "user_id": "alice"}},
    ]
    current = "{{canary:PROBE}}"
    if "update" in adapter.capabilities:
        current = "{{canary:CORRECTED}}"
        steps += [{"update": {"id": "probe", "value": current}},
                  {"expect": {"query": "MEMORYCHECK", "contains": current, "excludes": "{{canary:PROBE}}", "user_id": "alice"}}]
    if "restart" in adapter.capabilities:
        steps += [{"restart": {}}, {"expect": {"query": "MEMORYCHECK", "contains": current, "user_id": "alice"}}]
    if "user_scope" in adapter.capabilities:
        steps += [{"expect": {"query": "MEMORYCHECK", "excludes": current, "user_id": "bob"}}]
    if "delete" in adapter.capabilities:
        steps += [{"delete": {"id": "probe"}}, {"expect": {"query": "MEMORYCHECK", "excludes": current, "user_id": "alice"}}]
        if "restart" in adapter.capabilities:
            steps += [{"restart": {}}, {"expect": {"query": "MEMORYCHECK", "excludes": current, "user_id": "alice"}}]
    if "user_scope" not in adapter.capabilities:
        for step in steps:
            next(iter(step.values())).pop("user_id", None)
    if "inspect_storage" in adapter.capabilities:
        for step in steps:
            if "expect" in step and step["expect"].get("user_id") != "bob":
                step["expect"]["oracles"] = ["api", "storage"]
    contract = contract_from_mapping({"schema_version": "0.1", "name": "adapter_conformance", "steps": steps})
    return ContractRunner(adapter, settings=settings).run(contract)
