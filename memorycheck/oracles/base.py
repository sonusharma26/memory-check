from __future__ import annotations

from typing import Protocol

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.trace.schema import Observation
from memorycheck.types import Scope

ORACLE_CAPABILITIES = {
    "api": "query", "storage": "inspect_storage",
    "context": "inspect_context", "behavior": "inspect_behavior",
}


class Oracle(Protocol):
    """Optional context/behavior implementations can use this observation hook."""

    name: str
    capability: str

    def observe(self, adapter: MemoryAdapter, query: str, scope: Scope) -> Observation:
        ...
