"""Capability-oriented adapter surface, deliberately independent of frameworks."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from memorycheck.errors import UnsupportedCapability
from memorycheck.types import QueryObservation, Scope, StorageSnapshot


class MemoryAdapter:
    capabilities: frozenset[str] = frozenset()
    adapter_version = "0.1.0"
    provider_distribution: str | None = None
    restart_description = "Not supported"

    @property
    def name(self) -> str:
        return type(self).__name__

    def require(self, *capabilities: str) -> None:
        missing = set(capabilities) - set(self.capabilities)
        if missing:
            raise UnsupportedCapability(missing)

    def create(self, value: str, *, scope: Scope, alias: str | None = None) -> str:
        raise UnsupportedCapability({"create"})

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        raise UnsupportedCapability({"query"})

    def update(self, memory_id: str, value: str, *, scope: Scope) -> None:
        raise UnsupportedCapability({"update"})

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        raise UnsupportedCapability({"delete"})

    def restart(self) -> None:
        raise UnsupportedCapability({"restart"})

    def inspect_storage(self, *, scope: Scope) -> StorageSnapshot:
        raise UnsupportedCapability({"inspect_storage"})

    def cleanup_scope(self, *, scope: Scope) -> None:
        """Remove only a MemoryCheck-generated scope, never all application data."""
        raise UnsupportedCapability({"cleanup_scope"})

    def cleanup_descriptor(self) -> dict[str, Any] | None:
        """Non-secret restore information for local cleanup manifests, if safe."""
        return None

    def restore_cleanup_descriptor(self, descriptor: Mapping[str, Any]) -> None:
        raise UnsupportedCapability({"cleanup_restore"})

    def close(self) -> None:
        """Release owned runtime resources; never clear application data."""

    def __enter__(self) -> MemoryAdapter:
        return self

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> None:
        from memorycheck.deadlines import close_adapter
        try:
            close_adapter(self)
        except Exception:
            if exc_type is None:
                raise  # A close failure must not replace an existing contract failure.

