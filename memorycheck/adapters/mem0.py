"""Synchronous Mem0 OSS adapter using its real add/search/update/delete API.

No SDK import occurs until from_config() is used. infer=False preserves the
canary text; embedding infrastructure may still be required by Mem0 itself.
"""
from __future__ import annotations

import inspect
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from memorycheck.adapters._resource import ManagedResource
from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import AdapterError
from memorycheck.types import MemoryRecord, QueryObservation, Scope, StorageSnapshot

StorageInspector = Callable[[Any, Mapping[str, str]], StorageSnapshot]


class Mem0Adapter(MemoryAdapter):
    provider_distribution = "mem0ai"
    restart_description = "Close the owned Mem0 runtime and invoke its factory against unchanged persistent storage"
    def __init__(
        self, memory: Any = None, *, factory: Callable[[], Any] | None = None,
        close: Callable[[Any], None] | None = None, storage_inspector: StorageInspector | None = None,
        namespace: str | None = None, cleanup_target: str | None = None, search_limit: int = 100, search_api: str = "auto",
    ):
        if not isinstance(search_limit, int) or isinstance(search_limit, bool) or search_limit < 1:
            raise ValueError("search_limit must be a positive integer")
        if search_api not in {"auto", "legacy", "filters"}:
            raise ValueError("search_api must be auto, legacy, or filters")
        self.namespace = namespace or "mc_" + uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.namespace):
            raise ValueError("namespace must contain only letters, digits, underscores, and hyphens")
        self.search_limit, self.search_api = search_limit, search_api
        self.storage_inspector = storage_inspector
        self._cleanup_target = cleanup_target
        self._known: dict[str, Scope] = {}
        self._resource = ManagedResource(memory, factory=factory, close=close)
        try:
            for method in ("add", "search", "update", "delete"):
                if not callable(getattr(self.memory, method, None)):
                    raise AdapterError(f"Mem0 instance is missing synchronous {method}()")
                if inspect.iscoroutinefunction(getattr(self.memory, method)):
                    raise AdapterError("Use synchronous Mem0 Memory, not AsyncMemory")
        except Exception:
            self._resource.close()
            raise
        capabilities = {"create", "query", "update", "delete", "user_scope", "session_scope"}
        if factory is not None:
            capabilities.add("restart")
        if callable(getattr(self.memory, "delete_all", None)) and not inspect.iscoroutinefunction(self.memory.delete_all):
            capabilities.add("cleanup_scope")
        if storage_inspector is not None:
            capabilities.add("inspect_storage")
        self.capabilities = frozenset(capabilities)

    @property
    def memory(self) -> Any:
        return self._resource.get()

    @classmethod
    def from_config(cls, config: Mapping[str, Any], **kwargs: Any) -> Mem0Adapter:
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise AdapterError('Install the optional Mem0 dependency: pip install -e ".[mem0]"') from exc
        frozen = deepcopy(dict(config))
        kwargs.setdefault("cleanup_target", hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest())
        return cls(factory=lambda: Memory.from_config(deepcopy(frozen)), **kwargs)

    def native_scope(self, scope: Scope) -> dict[str, str]:
        self.require(*scope.capabilities)
        user = "unscoped" if scope.user_id is None else "user-" + quote(scope.user_id, safe="")
        result = {"user_id": self.namespace + ":" + user}
        if scope.session_id is not None:
            result["run_id"] = "session-" + quote(scope.session_id, safe="")
        return result

    def _reported_scope(self, row: Mapping[str, Any]) -> Scope:
        result = {}
        native_user = row.get("user_id")
        if isinstance(native_user, str):
            prefix = self.namespace + ":user-"
            if native_user.startswith(prefix):
                result["user_id"] = unquote(native_user[len(prefix):])
            elif native_user != self.namespace + ":unscoped":
                result["user_id"] = native_user
        native_session = row.get("run_id")
        if isinstance(native_session, str):
            result["session_id"] = unquote(native_session.removeprefix("session-"))
        # Missing reported scope remains unknown; never substitute the query's scope.
        return Scope.from_mapping(result)

    @staticmethod
    def _rows(response: Any, operation: str) -> list[Mapping[str, Any]]:
        rows = response.get("results") if isinstance(response, Mapping) else response
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise AdapterError(f"Mem0 {operation} returned an unsupported shape (expected a results list)")
        return rows

    def create(self, value: str, *, scope: Scope, alias: str | None = None) -> str:
        response = self.memory.add(
            [{"role": "user", "content": value}], infer=False,
            metadata={"memorycheck_alias": alias} if alias is not None else {},
            **self.native_scope(scope),
        )
        rows = self._rows(response, "add")
        if len(rows) != 1 or str(rows[0].get("event", "ADD")).upper() != "ADD":
            raise AdapterError("infer=False must create exactly one memory; asynchronous/extracted writes are unsupported")
        memory_id = rows[0].get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise AdapterError("Mem0 add returned no usable memory ID")
        self._known[memory_id] = scope
        return memory_id

    def query(self, query: str, *, scope: Scope) -> QueryObservation:
        method = self.memory.search
        try:
            params = inspect.signature(method).parameters
        except (TypeError, ValueError):
            params = {}
        style = self.search_api
        if style == "auto":
            style = "legacy" if "user_id" in params else "filters"
        scope_kwargs = self.native_scope(scope)
        kwargs: dict[str, Any] = dict(scope_kwargs) if style == "legacy" else {"filters": scope_kwargs}
        # SDK releases changed syntax, not lifecycle semantics. Do not retry
        # failed calls with different filters: that could hide real adapter bugs.
        limit_key = "top_k" if "top_k" in params or (style == "filters" and "limit" not in params) else "limit"
        kwargs[limit_key] = self.search_limit
        if "threshold" in params:
            kwargs["threshold"] = 0.0
        if "rerank" in params:
            kwargs["rerank"] = False
        records = []
        for row in self._rows(method(query, **kwargs), "search"):
            memory_id, value = row.get("id"), row.get("memory")
            if not isinstance(memory_id, str) or not memory_id or not isinstance(value, str):
                raise AdapterError("Mem0 search rows must contain string id and memory fields")
            metadata = row.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                raise AdapterError("Mem0 returned malformed memory metadata")
            records.append(MemoryRecord(memory_id, value, self._reported_scope(row), {
                **dict(metadata), "native_user_id": row.get("user_id"),
                "native_run_id": row.get("run_id"), "score": row.get("score"),
            }))
        return QueryObservation(tuple(records))

    def _owned(self, memory_id: str, scope: Scope) -> None:
        self.native_scope(scope)
        if self._known.get(memory_id) != scope:
            raise AdapterError("Refusing an unowned or cross-scope mutation; use an ID created by this adapter")

    def update(self, memory_id: str, value: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)
        # Positional second argument works for text= and older data= signatures.
        self.memory.update(memory_id, value)

    def delete(self, memory_id: str, *, scope: Scope) -> None:
        self._owned(memory_id, scope)
        self.memory.delete(memory_id)

    def restart(self) -> None:
        self.require("restart")
        self._resource.restart()

    def inspect_storage(self, *, scope: Scope) -> StorageSnapshot:
        self.require("inspect_storage")
        return self.storage_inspector(self.memory, self.native_scope(scope))

    def cleanup_scope(self, *, scope: Scope) -> None:
        from memorycheck.cleanup import require_test_scope
        require_test_scope(scope)
        self.require("cleanup_scope")
        self.memory.delete_all(**self.native_scope(scope))

    def cleanup_descriptor(self):
        if self._cleanup_target is None:
            return None
        return {"kind": "mem0", "target": self._cleanup_target, "namespace": self.namespace}

    def restore_cleanup_descriptor(self, descriptor):
        if (descriptor.get("kind") != "mem0" or self._cleanup_target is None
                or descriptor.get("target") != self._cleanup_target
                or not isinstance(descriptor.get("namespace"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", descriptor["namespace"])):
            raise AdapterError("Cleanup target does not match the configured Mem0 backend")
        self.namespace = descriptor["namespace"]

    def close(self) -> None:
        self._resource.close()
