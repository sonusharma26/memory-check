"""Resource ownership and real runtime reconstruction for SDK adapters."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from typing import Any

from memorycheck.errors import AdapterError


class ManagedResource:
    def __init__(self, instance: Any = None, *, factory: Callable[[], Any] | None = None,
                 close: Callable[[Any], None] | None = None):
        if (instance is None) == (factory is None):
            raise ValueError("Supply exactly one of an existing instance or a fresh-resource factory")
        self.factory = factory
        self.close_callback = close
        self.stack = ExitStack()
        self.closed = False
        if instance is not None:
            self.instance = instance
        else:
            try:
                self.instance = self._open()
            except Exception:
                self.stack.close()
                raise

    def _open(self) -> Any:
        resource = self.factory()
        if resource is None:
            raise AdapterError("Adapter factory returned None")
        if hasattr(resource, "__enter__") and hasattr(resource, "__exit__"):
            instance = self.stack.enter_context(resource)
            if instance is None:
                raise AdapterError("Resource context manager did not yield an instance")
            return instance
        if self.close_callback is not None:
            self.stack.callback(self.close_callback, resource)
        elif callable(getattr(resource, "close", None)):
            self.stack.callback(resource.close)
        return resource

    def get(self) -> Any:
        if self.closed:
            raise AdapterError("Adapter resource is closed")
        return self.instance

    def restart(self) -> None:
        if self.factory is None:
            raise AdapterError("No reconstruction factory configured")
        old = self.get()
        self.stack.close()
        self.stack = ExitStack()
        self.closed = True
        try:
            fresh = self._open()
            if fresh is old:
                raise AdapterError("Restart factory reused the same runtime object; return a fresh instance")
        except Exception:
            self.stack.close()
            raise
        self.instance = fresh
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.stack.close()
