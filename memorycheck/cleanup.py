"""Private local cleanup ledgers. These are NOT trace/CI artifacts.

Only a trusted current adapter may restore a ledger, and every scope must have
this run's generated boundary. No factories, credentials, or payloads are read
from a ledger. Cleanup is explicit, bounded, and never global.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.deadlines import Deadlines, quarantined
from memorycheck.errors import CleanupError
from memorycheck.trace.serializer import write_json_atomic
from memorycheck.types import Scope

RUN_PATTERN = re.compile(r"mc_[a-f0-9]{32}")


def require_test_scope(scope: Scope, run_id: str | None = None) -> None:
    prefix = f"memorycheck_{run_id}_" if run_id else "memorycheck_mc_"
    if not any(value.startswith(prefix) for key, value in scope.as_dict().items()
               if key in {"user_id", "tenant_id", "session_id"}):
        raise CleanupError("Refusing cleanup outside a generated MemoryCheck test scope")


class RunLedger:
    def __init__(self, directory: Path, run_id: str, adapter: MemoryAdapter):
        if not RUN_PATTERN.fullmatch(run_id):
            raise CleanupError("Invalid generated run namespace")
        self.path = directory / f"{run_id}.json"
        self.adapter = adapter
        self.payload: dict[str, Any] = {
            "schema_version": "0.1", "run_id": run_id,
            "adapter": type(adapter).__name__, "created_at": datetime.now(timezone.utc).isoformat(),
            "owner_pid": os.getpid(), "scopes": [], "uncertain": False,
            "descriptor": adapter.cleanup_descriptor(), "status": "active",
        }

    def touch(self, scope: Scope) -> None:
        require_test_scope(scope, self.payload["run_id"])
        value = scope.as_dict()
        if value not in self.payload["scopes"]:
            self.payload["scopes"].append(value)
            self.save()  # Durable intent BEFORE the first write in this scope.

    def save(self) -> None:
        self.payload["uncertain"] = quarantined(self.adapter)
        write_json_atomic(self.path, self.payload)

    def done(self) -> None:
        self.path.unlink(missing_ok=True)


def load_ledger(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > 1_048_576:
            raise ValueError("Unsafe ledger path or size")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != "0.1":
            raise ValueError("Unsupported ledger schema")
        run_id = data.get("run_id")
        if not isinstance(run_id, str) or not RUN_PATTERN.fullmatch(run_id) or path.stem != run_id:
            raise ValueError("Run ID does not match the ledger filename")
        scopes = data.get("scopes")
        if not isinstance(scopes, list) or not scopes or len(scopes) > 10000:
            raise ValueError("Missing or excessive scopes")
        for value in scopes:
            require_test_scope(Scope.from_mapping(value), run_id)
        if not isinstance(data.get("uncertain"), bool) or not isinstance(data.get("adapter"), str):
            raise ValueError("Invalid ledger fields")
        if type(data.get("owner_pid")) is not int or data["owner_pid"] <= 0:
            raise ValueError("Invalid ledger owner")
        if data.get("status") not in {"active", "pending", "blocked"}:
            raise ValueError("Invalid ledger status")
        return data
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise CleanupError(f"Cannot read cleanup ledger ({type(exc).__name__}); do not use untrusted or edited manifests") from None


def cleanup_ledger(path: Path, adapter: MemoryAdapter, *, timeout: float, mode: str) -> int:
    data = load_ledger(path)
    if data["status"] == "active":
        # Never replay an active process's intent while it can still write.
        # On non-POSIX systems avoid os.kill(pid, 0), which is not portable.
        alive = True
        if os.name == "posix":
            try:
                os.kill(data["owner_pid"], 0)
            except ProcessLookupError:
                alive = False
            except (PermissionError, OSError):
                pass  # An inaccessible/unknown owner is not proof of termination.
        if alive:
            raise CleanupError("Run is still active or its owner cannot be verified as stopped; use backend tools, not automatic replay")
    if data["uncertain"]:
        raise CleanupError("This run timed out and may have pending writes. Verify/clean it with backend tools; automatic replay is blocked")
    if data["adapter"] != type(adapter).__name__:
        raise CleanupError("Configured adapter does not match the cleanup ledger")
    if not isinstance(data.get("descriptor"), dict):
        raise CleanupError("This adapter did not provide a safe restore target; use backend tools for the recorded scopes")
    if "cleanup_scope" not in adapter.capabilities:
        raise CleanupError("Adapter has no scoped cleanup capability")
    adapter.restore_cleanup_descriptor(data["descriptor"])
    from time import monotonic
    deadline = monotonic() + timeout
    calls = Deadlines(adapter, mode)
    for value in data["scopes"]:
        scope = Scope.from_mapping(value)
        calls.call(lambda: adapter.cleanup_scope(scope=scope), operation="cleanup", timeout=timeout,
                   contract_deadline=deadline)
    path.unlink()
    return len(data["scopes"])
