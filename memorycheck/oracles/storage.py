from __future__ import annotations

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import AdapterError
from memorycheck.trace.schema import Observation
from memorycheck.types import Scope, StorageSnapshot


class StorageOracle:
    name = "storage"
    capability = "inspect_storage"

    def observe(self, adapter: MemoryAdapter, query: str, scope: Scope) -> Observation:
        # Intentionally ignore the retrieval query. Old versions may no longer
        # match it; the entire declared storage boundary must be inspected.
        snapshot = adapter.inspect_storage(scope=scope)
        if not isinstance(snapshot, StorageSnapshot):
            raise AdapterError("inspect_storage() must return a StorageSnapshot with explicit coverage")
        return Observation(
            status="present" if snapshot.records else "absent",
            values=[record.value for record in snapshot.records],
            records=[record.to_dict() for record in snapshot.records],
            coverage=snapshot.coverage,
            complete=snapshot.complete,
            notes=list(snapshot.notes),
        )
