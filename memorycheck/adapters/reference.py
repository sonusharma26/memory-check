"""A tiny on-disk JSON backend and four intentionally incorrect variants.

Deletion rewrites the active snapshot. This is NOT secure filesystem erasure.
Each instance is single-writer; use distinct paths for concurrent tests.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import AdapterError
from memorycheck.types import MemoryRecord, QueryObservation, Scope, StorageSnapshot


def matches_query(record: MemoryRecord, query: str) -> bool:
    needle = query.casefold()
    return (query == "*" or needle in record.value.casefold()
            or needle in str(record.metadata.get("memorycheck_alias", "")).casefold())


class ReferenceMemory(MemoryAdapter):
    restart_description = "Discard all cached records and read the same active JSON snapshot from disk"
    capabilities = frozenset({"create", "query", "update", "delete", "restart",
                              "inspect_storage", "user_scope", "tenant_scope", "session_scope", "cleanup_scope"})

    def __init__(self, path: str | Path | None = None):
        self._temp = tempfile.TemporaryDirectory(prefix="memorycheck-") if path is None else None
        self.path = Path(path) if path is not None else Path(self._temp.name) / "memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._records: dict[str, MemoryRecord] = {}
        if self.path.exists():
            self._records = self._read()
        else:
            self._persist()

    def _ensure_open(self) -> None:
        if self._closed:
            raise AdapterError("Reference memory is closed")

    def _read(self) -> dict[str, MemoryRecord]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("records"), list):
                raise ValueError("Expected reference snapshot version 1")
            records = {}
            for row in data["records"]:
                record = MemoryRecord(row["id"], row["value"], Scope.from_mapping(row["scope"]), row["metadata"])
                if record.id in records:
                    raise ValueError(f"Duplicate persisted ID {record.id}")
                records[record.id] = record
            return records
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise AdapterError(f"Cannot read reference snapshot {self.path}: {exc}") from exc

    def _persist(self) -> None:
        self._ensure_open()
        payload = {"version": 1, "records": [row.to_dict() for row in self._records.values()]}
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _owned(self, memory_id: str, scope: Scope) -> MemoryRecord:
        self._ensure_open()
        row = self._records.get(memory_id)
        if row is None or row.scope != scope:
            raise AdapterError(f"Memory {memory_id!r} is not present in the requested scope")
        return row

    def create(self, value: str, *, scope: Scope, alias: str | None = None) -> str:
        self._ensure_open()
        memory_id = str(uuid4())
        self._records[memory_id] = MemoryRecord(memory_id, value, scope, {"memorycheck_alias": alias})
        self._persist()
        return memory_id

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        self._ensure_open()
        return QueryObservation(tuple(row for row in self._records.values() if row.scope == scope and matches_query(row, query)))

    def update(self, memory_id: str, value: str, *, scope: Scope) -> None:
        old = self._owned(memory_id, scope)
        self._records[memory_id] = MemoryRecord(memory_id, value, scope, old.metadata)
        self._persist()

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)
        del self._records[memory_id]
        self._persist()

    def restart(self) -> None:
        self._ensure_open()
        # Discard the complete runtime representation and reconstruct from disk.
        self._records = self._read()

    def inspect_storage(self, *, scope: Scope) -> StorageSnapshot:
        self._ensure_open()
        return StorageSnapshot(
            tuple(row for row in self._read().values() if row.scope == scope),
            coverage="Reference JSON active snapshot; no filesystem journals, backups, or deleted blocks",
            notes=("Independent file read; no retrieval-query filtering.",),
        )

    def cleanup_scope(self, *, scope: Scope) -> None:
        from memorycheck.cleanup import require_test_scope
        require_test_scope(scope)
        self._ensure_open()
        # Cleanup is deliberately independent of broken test mutations and runs
        # only after observations were captured. It removes synthetic stale copies.
        self._records = {key: row for key, row in self._read().items() if row.scope != scope}
        self._persist()

    def cleanup_descriptor(self):
        return {"kind": "reference", "path": str(self.path.resolve())}

    def restore_cleanup_descriptor(self, descriptor):
        if descriptor != self.cleanup_descriptor():
            raise AdapterError("Cleanup target differs; explicitly configure the same reference path")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._temp is not None:
                self._temp.cleanup()


class BrokenDeleteMemory(ReferenceMemory):
    """Reports a successful deletion without deleting anything."""

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)


class BrokenRestartMemory(ReferenceMemory):
    """Deletes only from the runtime cache; restart resurrects persisted data."""

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)
        del self._records[memory_id]


class BrokenIsolationMemory(ReferenceMemory):
    """Forgets the scope filter on retrieval."""

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        self._ensure_open()
        return QueryObservation(tuple(row for row in self._records.values() if matches_query(row, query)))


class BrokenUpdateMemory(ReferenceMemory):
    """Updates the fact but leaves a retrievable old version behind."""

    def update(self, memory_id: str, value: str, *, scope: Scope) -> None:
        old = self._owned(memory_id, scope)
        stale_id = str(uuid4())
        self._records[stale_id] = MemoryRecord(
            stale_id, old.value, scope,
            {**old.metadata, "artifact": "stale-version", "source_id": memory_id},
        )
        self._records[memory_id] = MemoryRecord(memory_id, value, scope, old.metadata)
        self._persist()
