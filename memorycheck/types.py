"""Small, provider-neutral values. Adapters must not discard leaked records."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scope:
    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    extra: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        reserved = {"user_id", "tenant_id", "session_id"}
        pairs = list(self.extra)
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)) or reserved.intersection(keys):
            raise ValueError("Scope.extra contains duplicate or reserved keys")
        for key, value in pairs:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Scope keys must be nonempty strings")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Scope {key!r} must be a nonempty string")
        for key in reserved:
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Scope {key!r} must be a nonempty string")
        object.__setattr__(self, "extra", tuple(sorted(pairs)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, str] | Scope | None) -> Scope:
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("scope must be a mapping")
        known = {k: v for k, v in value.items() if k in {"user_id", "tenant_id", "session_id"}}
        extra = tuple((k, v) for k, v in value.items() if k not in known)
        # Nulls in a supplied mapping must not silently remove a scope boundary.
        if any(not isinstance(v, str) or not v.strip() for v in value.values()):
            raise ValueError("All scope values must be nonempty strings")
        return cls(**known, extra=extra)

    def as_dict(self) -> dict[str, str]:
        result = {key: getattr(self, key) for key in ("user_id", "tenant_id", "session_id")
                  if getattr(self, key) is not None}
        result.update(self.extra)
        return result

    def merged(self, other: Mapping[str, str] | Scope | None = None) -> Scope:
        result = self.as_dict()
        result.update(Scope.from_mapping(other).as_dict())
        return Scope.from_mapping(result)

    @property
    def capabilities(self) -> frozenset[str]:
        standard = {"user_id": "user_scope", "tenant_id": "tenant_scope", "session_id": "session_scope"}
        return frozenset(standard.get(key, f"scope:{key}") for key in self.as_dict())


@dataclass(frozen=True)
class MemoryRef:
    id: str
    scope: Scope = field(default_factory=Scope)
    alias: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("MemoryRef.id must be a nonempty string")

    def __str__(self) -> str:
        return self.id


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    value: str
    scope: Scope = field(default_factory=Scope)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("MemoryRecord.id must be a nonempty string")
        if not isinstance(self.value, str):
            raise ValueError("MemoryRecord.value must be text")
        if not isinstance(self.scope, Scope) or not isinstance(self.metadata, Mapping):
            raise ValueError("MemoryRecord needs a Scope and mapping metadata")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "value": self.value, "scope": self.scope.as_dict(),
                "metadata": dict(self.metadata)}


@dataclass(frozen=True)
class StorageSnapshot:
    """A full scan of a stated storage boundary, not another retrieval query.

    `complete=False` prevents absence/empty assertions from passing. Coverage must
    identify the inspected layer; it must not imply erasure of backups or logs.
    """

    records: tuple[MemoryRecord, ...]
    coverage: str
    complete: bool = True
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "notes", tuple(self.notes))
        if not self.coverage or not isinstance(self.coverage, str):
            raise ValueError("A storage snapshot must declare its coverage")
        if not isinstance(self.complete, bool):
            raise ValueError("StorageSnapshot.complete must be a boolean")
        if any(not isinstance(row, MemoryRecord) for row in self.records):
            raise ValueError("Storage inspectors must return MemoryRecord objects")
        if any(not isinstance(note, str) for note in self.notes):
            raise ValueError("StorageSnapshot.notes must contain strings")


@dataclass(frozen=True)
class QueryObservation(Sequence[MemoryRecord]):
    """Normalized public retrieval, never raw dictionaries or arbitrary strings.

    raw is intentionally available only in process and is never serialized.
    complete describes this query response, not physical storage erasure.
    """

    records: tuple[MemoryRecord, ...]
    raw: Any = field(default=None, repr=False, compare=False)
    complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.records, (list, tuple)) or any(not isinstance(r, MemoryRecord) for r in self.records):
            raise ValueError("QueryObservation.records must be a list or tuple of MemoryRecord")
        if not isinstance(self.complete, bool):
            raise ValueError("QueryObservation.complete must be boolean")
        object.__setattr__(self, "records", tuple(self.records))

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index):
        return self.records[index]
