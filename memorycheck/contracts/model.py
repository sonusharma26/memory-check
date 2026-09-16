"""Validated lifecycle contracts; requirements are declared AND inferred."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memorycheck.oracles.base import ORACLE_CAPABILITIES
from memorycheck.types import Scope


@dataclass(frozen=True)
class Step:
    operation: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Contract:
    name: str
    steps: tuple[Step, ...]
    requires: frozenset[str] = frozenset()
    description: str = ""
    scope: Scope = field(default_factory=Scope)
    source: str | None = None
    schema_version: str = "0.1"

    @property
    def category(self) -> str:
        if self.name.startswith("update_"):
            return "Correction"
        if self.name.startswith("delete_"):
            return "Deletion"
        if self.name.startswith("user_"):
            return "Isolation"
        if self.name.startswith("remember_"):
            return "Restart / recall"
        return "Custom"

    @property
    def required_capabilities(self) -> frozenset[str]:
        needed = set(self.requires) | set(self.scope.capabilities)
        for step in self.steps:
            needed.update(self.scope.merged(step.arguments.get("scope")).capabilities)
            if step.operation == "expect":
                needed.update(ORACLE_CAPABILITIES[name] for name in step.arguments["oracles"])
            else:
                needed.add(step.operation)
        return frozenset(needed)

    @property
    def required_oracles(self) -> frozenset[str]:
        return frozenset(name for step in self.steps if step.operation == "expect"
                         for name in step.arguments["oracles"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "name": self.name, "description": self.description,
            "requires": sorted(self.requires), "scope": self.scope.as_dict(),
            "steps": [{step.operation: dict(step.arguments)} for step in self.steps],
        }
