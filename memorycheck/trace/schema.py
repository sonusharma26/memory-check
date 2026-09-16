"""The versioned event model shared by runners, pytest, CLI, and reporters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from memorycheck.environment import environment
from memorycheck.trace.privacy import Redactor

SCHEMA_VERSION = "0.1"


@dataclass
class Check:
    kind: str
    expected: str | bool
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return vars(self).copy()


@dataclass
class Observation:
    status: str = "unavailable"
    values: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    coverage: str = "Not configured"
    complete: bool = False
    assertion_status: str = "not_checked"
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self), "checks": [check.to_dict() for check in self.checks]}


@dataclass
class Event:
    sequence: int
    timestamp: float
    operation: str
    subject: str | None = None
    scope: dict[str, str] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    status: str = "running"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self), "arguments": dict(self.arguments), "scope": dict(self.scope),
                "expected": dict(self.expected),
                "observations": {key: value.to_dict() for key, value in self.observations.items()}}


@dataclass
class Trace:
    contract: str
    adapter: str
    capabilities: list[str]
    run_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: str = SCHEMA_VERSION
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    status: str = "running"
    events: list[Event] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    skip_reason: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    environment: dict[str, str | None] = field(default_factory=environment)
    namespace: str = ""
    cleanup: dict[str, Any] = field(default_factory=lambda: {"status": "not_started"})
    _redactor: Redactor = field(default_factory=Redactor, repr=False)
    _clock_start: float = field(default_factory=perf_counter, repr=False)

    def event(self, operation: str, **kwargs: Any) -> Event:
        event = Event(len(self.events) + 1, round(perf_counter() - self._clock_start, 6), operation, **kwargs)
        self.events.append(event)
        return event

    @property
    def first_divergence(self) -> dict[str, Any] | None:
        for index, event in enumerate(self.events):
            if event.status not in {"failed", "error"}:
                continue
            prior = self.events[:index]
            transition = next((e for e in reversed(prior) if e.operation != "query"), None)
            verified = next((e for e in reversed(prior) if e.expected and e.status == "passed"), None)
            return {
                "observed_at_sequence": event.sequence,
                "operation": event.operation,
                "after_sequence": transition.sequence if transition else None,
                "after_operation": transition.operation if transition else None,
                "last_verified_sequence": verified.sequence if verified else None,
            }
        return None

    def finish(self, status: str | None = None) -> None:
        if status is None:
            status = ("error" if any(e.status == "error" for e in self.events) else
                      "failed" if any(e.status == "failed" for e in self.events) else "passed")
        # A fixture finalizer must never turn a previous failure into success.
        priority = {"running": -1, "passed": 0, "skipped": 1, "failed": 2, "error": 3}
        if priority[status] >= priority[self.status]:
            self.status = status
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return self._redactor.apply({
            **self.environment, "namespace": self.namespace, "cleanup": dict(self.cleanup),
            "schema_version": self.schema_version, "run_id": self.run_id,
            "contract": self.contract, "adapter": self.adapter,
            "capabilities": self.capabilities, "started_at": self.started_at,
            "finished_at": self.finished_at, "status": self.status,
            "events": [e.to_dict() for e in self.events],
            "first_divergence": self.first_divergence,
            "missing_capabilities": self.missing_capabilities, "skip_reason": self.skip_reason,
            "error": self.error, "notes": list(self.notes),
        })
