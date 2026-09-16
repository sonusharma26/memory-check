from __future__ import annotations

from collections.abc import Sequence

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import AdapterError
from memorycheck.trace.schema import Observation
from memorycheck.types import QueryObservation, Scope


class APIOracle:
    name = "api"
    capability = "query"

    def observe(self, adapter: MemoryAdapter, query: str, scope: Scope) -> Observation:
        response = adapter.query(query, scope=scope)
        if not isinstance(response, QueryObservation):
            raise AdapterError("query() must return QueryObservation, never a list, None, or raw SDK data")
        records = response.records
        # No local scope filtering: it would hide isolation bugs in the backend.
        return Observation(
            status="present" if records else "absent",
            values=[record.value for record in records],
            records=[record.to_dict() for record in records],
            coverage="Public retrieval response; absence means not returned by this query, not erased",
            complete=response.complete,
        )
