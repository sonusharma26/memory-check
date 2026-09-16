"""API-shaped in-process fakes, NOT claims of live SDK integration coverage."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4


class FakeMem0:
    def __init__(self, state, lifecycle):
        self.state, self.lifecycle = state, lifecycle
        self.closed = False
        lifecycle.append("open")

    def add(self, messages, *, user_id, infer, metadata, run_id=None):
        assert not self.closed and infer is False
        memory_id = uuid4().hex
        self.state[memory_id] = {"id": memory_id, "memory": messages[0]["content"],
                                 "user_id": user_id, "run_id": run_id, "metadata": metadata}
        return {"results": [{"id": memory_id, "event": "ADD"}]}

    def search(self, query, *, filters, top_k=20, threshold=0.1, rerank=True):
        assert not self.closed
        return {"results": [deepcopy(row) for row in self.state.values()
                            if all(row.get(k) == v for k, v in filters.items())
                            and query.casefold() in row["memory"].casefold()][:top_k]}

    def update(self, memory_id, text):
        assert not self.closed
        self.state[memory_id]["memory"] = text

    def delete(self, memory_id):
        assert not self.closed
        self.state.pop(memory_id, None)

    def close(self):
        self.closed = True
        self.lifecycle.append("close")


class LegacyMem0(FakeMem0):
    def search(self, query, *, user_id, run_id=None, limit=100, threshold=None):
        filters = {"user_id": user_id}
        if run_id is not None:
            filters["run_id"] = run_id
        return super().search(query, filters=filters, top_k=limit)


class FakeStore:
    def __init__(self, state, lifecycle):
        self.state, self.lifecycle = state, lifecycle
        self.closed = False
        lifecycle.append("open")

    def put(self, namespace, key, value):
        assert not self.closed
        self.state[(namespace, key)] = deepcopy(value)

    def search(self, namespace_prefix, *, query=None, limit=10, offset=0):
        assert not self.closed
        items = [SimpleNamespace(namespace=ns, key=key, value=deepcopy(value), score=None)
                 for (ns, key), value in sorted(self.state.items())
                 if ns[:len(namespace_prefix)] == namespace_prefix]
        return items[offset:offset + limit]

    def delete(self, namespace, key):
        assert not self.closed
        self.state.pop((namespace, key), None)

    def close(self):
        self.closed = True
        self.lifecycle.append("close")
