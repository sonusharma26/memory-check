"""LangGraph synchronous BaseStore adapter, not an agent/checkpointer wrapper."""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from memorycheck.adapters._resource import ManagedResource
from memorycheck.adapters.base import MemoryAdapter
from memorycheck.adapters.reference import matches_query
from memorycheck.errors import AdapterError
from memorycheck.types import MemoryRecord, QueryObservation, Scope, StorageSnapshot

StorageInspector = Callable[[Any, tuple[str, ...]], StorageSnapshot]


class LangGraphAdapter(MemoryAdapter):
    provider_distribution = "langgraph"
    restart_description = "Close the owned BaseStore connection and invoke its factory against the same persistent store"
    def __init__(
        self, store: Any = None, *, factory: Callable[[], Any] | None = None,
        close: Callable[[Any], None] | None = None, cleanup_target: str | None = None, namespace: Sequence[str] | None = None,
        storage_inspector: StorageInspector | None = None, query_mode: str = "lexical",
        page_size: int = 100, max_records: int = 10_000,
    ):
        if query_mode not in {"lexical", "semantic"}:
            raise ValueError("query_mode must be lexical or semantic")
        for key, value in (("page_size", page_size), ("max_records", max_records)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if isinstance(namespace, str):
            raise ValueError("namespace must be a sequence of path segments, not a string")
        self.namespace = tuple(namespace) if namespace is not None else ("memorycheck", uuid4().hex)
        if not self.namespace or any(not isinstance(s, str) or not s for s in self.namespace):
            raise ValueError("namespace must contain nonempty strings")
        self.query_mode, self.page_size, self.max_records = query_mode, page_size, max_records
        self.storage_inspector = storage_inspector
        self._cleanup_target = cleanup_target
        self._known: dict[str, tuple[Scope, str | None]] = {}
        self._resource = ManagedResource(store, factory=factory, close=close)
        try:
            for method in ("put", "search", "delete"):
                function = getattr(self.store, method, None)
                if not callable(function) or inspect.iscoroutinefunction(function):
                    raise AdapterError(f"LangGraph adapter requires synchronous {method}()")
        except Exception:
            self._resource.close()
            raise
        capabilities = {"create", "query", "update", "delete", "user_scope", "tenant_scope", "session_scope", "cleanup_scope"}
        if factory is not None:
            capabilities.add("restart")
        if storage_inspector is not None:
            capabilities.add("inspect_storage")
        self.capabilities = frozenset(capabilities)

    @property
    def store(self) -> Any:
        return self._resource.get()

    @classmethod
    def from_postgres(cls, conn_string: str, *, setup: bool = True, **kwargs: Any) -> LangGraphAdapter:
        """Reconnect to the same database on restart. Use a dedicated test DB."""
        try:
            from langgraph.store.postgres import PostgresStore
        except ImportError as exc:
            raise AdapterError('Install PostgreSQL support: pip install -e ".[postgres]"') from exc

        @contextmanager
        def factory():
            with PostgresStore.from_conn_string(conn_string) as store:
                if setup:
                    store.setup()
                yield store

        kwargs.setdefault("cleanup_target", hashlib.sha256(conn_string.encode()).hexdigest())
        return cls(factory=factory, **kwargs)

    def native_namespace(self, scope: Scope) -> tuple[str, ...]:
        self.require(*scope.capabilities)
        canonical = json.dumps(scope.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        segment = "scope-" + base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=")
        # A single fixed-depth slot avoids prefix collisions and delimiter ambiguity.
        return self.namespace + (segment,)

    @staticmethod
    def _scope_from_namespace(namespace: tuple[str, ...]) -> Scope:
        try:
            if not namespace or not namespace[-1].startswith("scope-"):
                return Scope()
            value = namespace[-1][6:]
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            return Scope.from_mapping(json.loads(decoded))
        except (ValueError, TypeError, UnicodeError):
            return Scope()

    def _record(self, item: Any) -> MemoryRecord:
        key, payload, namespace = getattr(item, "key", None), getattr(item, "value", None), getattr(item, "namespace", ())
        if not isinstance(key, str) or not key or not isinstance(payload, Mapping) or not isinstance(payload.get("text"), str):
            raise AdapterError("Store search returned an unsupported item; expected key and value.text")
        if not isinstance(namespace, (list, tuple)) or any(not isinstance(p, str) for p in namespace):
            raise AdapterError("Store returned a malformed namespace")
        return MemoryRecord(key, payload["text"], self._scope_from_namespace(tuple(namespace)), {
            "memorycheck_alias": payload.get("memorycheck_alias"),
            "native_namespace": list(namespace), "score": getattr(item, "score", None),
        })

    def create(self, value: str, *, scope: Scope, alias: str | None = None) -> str:
        memory_id = str(uuid4())
        self.store.put(self.native_namespace(scope), memory_id, {"text": value, "memorycheck_alias": alias})
        self._known[memory_id] = (scope, alias)
        return memory_id

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        namespace = self.native_namespace(scope)
        if self.query_mode == "semantic":
            items = self.store.search(namespace, query=query, limit=self.page_size)
            if not isinstance(items, list):
                raise AdapterError("Store search must return a list, not None")
            return QueryObservation(tuple(self._record(item) for item in items))
        # Deterministic mode scans the public store API, then matches text/alias.
        # It NEVER filters returned records by owner or by the IDs we created.
        records = []
        seen = set()
        offset = 0
        while True:
            limit = min(self.page_size, self.max_records - offset + 1)
            items = self.store.search(namespace, limit=limit, offset=offset)
            if not isinstance(items, list):
                raise AdapterError("Store search must return a list, not None")
            for item in items:
                record = self._record(item)
                identity = (tuple(record.metadata["native_namespace"]), record.id)
                if identity in seen:
                    raise AdapterError("Store pagination repeated a record; cannot establish complete retrieval")
                seen.add(identity)
                records.append(record)
            offset += len(items)
            if offset > self.max_records:
                raise AdapterError("Store scan exceeds max_records; increase the limit instead of asserting false absence")
            if len(items) < limit:
                break
        return QueryObservation(tuple(record for record in records if matches_query(record, query)))

    def _owned(self, memory_id: str, scope: Scope) -> str | None:
        if memory_id not in self._known or self._known[memory_id][0] != scope:
            raise AdapterError("Refusing an unowned or cross-scope mutation")
        return self._known[memory_id][1]

    def update(self, memory_id: str, value: str, *, scope: Scope) -> None:
        alias = self._owned(memory_id, scope)
        self.store.put(self.native_namespace(scope), memory_id, {"text": value, "memorycheck_alias": alias})

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)
        self.store.delete(self.native_namespace(scope), memory_id)

    def restart(self) -> None:
        self.require("restart")
        self._resource.restart()

    def inspect_storage(self, *, scope: Scope) -> StorageSnapshot:
        self.require("inspect_storage")
        return self.storage_inspector(self.store, self.native_namespace(scope))

    def cleanup_scope(self, *, scope: Scope) -> None:
        from memorycheck.cleanup import require_test_scope
        require_test_scope(scope)
        namespace = self.native_namespace(scope)
        # Search from offset zero after each batch, avoiding pagination skips
        # caused by our own deletes. Refuse foreign namespaces even if the backend
        # leaks them. Cleanup is not an oracle and must not delete leaked data.
        removed = set()
        while True:
            items = self.store.search(namespace, limit=self.page_size, offset=0)
            if not isinstance(items, list):
                raise AdapterError("Cleanup search returned a malformed response")
            if not items:
                return
            for item in items:
                key = getattr(item, "key", None)
                if tuple(getattr(item, "namespace", ())) != namespace or not isinstance(key, str) or not key:
                    raise AdapterError("Cleanup refused a foreign or malformed store item")
                if key in removed or len(removed) >= self.max_records:
                    raise AdapterError("Cleanup made no progress or exceeded max_records")
                self.store.delete(namespace, key)
                removed.add(key)

    def cleanup_descriptor(self):
        if self._cleanup_target is None:
            return None
        return {"kind": "langgraph", "target": self._cleanup_target, "namespace": list(self.namespace)}

    def restore_cleanup_descriptor(self, descriptor):
        namespace = descriptor.get("namespace")
        if (descriptor.get("kind") != "langgraph" or self._cleanup_target is None
                or descriptor.get("target") != self._cleanup_target
                or not isinstance(namespace, list) or not namespace
                or any(not isinstance(s, str) or not s for s in namespace)):
            raise AdapterError("Cleanup target does not match the configured LangGraph store")
        self.namespace = tuple(namespace)

    def close(self) -> None:
        self._resource.close()
