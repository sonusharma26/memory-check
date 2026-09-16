"""Public exception hierarchy: execution errors are never assertion failures."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memorycheck.runner.result import RunResult


class MemoryCheckError(Exception):
    """Base error for MemoryCheck configuration and execution."""


class ContractValidationError(MemoryCheckError, ValueError):
    """A malformed contract; no backend operations should have run."""

    def __init__(self, message: str, *, source: str | None = None,
                 line: int | None = None, column: int | None = None,
                 hint: str | None = None, excerpt: str | None = None):
        self.message, self.source = message, source
        self.line, self.column, self.hint, self.excerpt = line, column, hint, excerpt
        super().__init__(message)

    def __str__(self) -> str:
        location = self.source or "<contract>"
        if self.line is not None:
            location += f":{self.line}:{self.column or 1}"
        parts = [location, self.message]
        if self.excerpt is not None and self.line is not None:
            parts += [f"{self.line:>4} | {self.excerpt}", "       " + " " * ((self.column or 1) - 1) + "^"]
        if self.hint:
            parts.append(self.hint)
        return "\n".join(parts)


# Compatibility aliases for the original, unreleased API.
ContractError = ContractValidationError


class UnsupportedCapabilityError(MemoryCheckError):
    def __init__(self, missing: Iterable[str], message: str | None = None):
        self.missing = frozenset(missing)
        super().__init__(message or "Missing capabilities: " + ", ".join(sorted(self.missing)))


UnsupportedCapability = UnsupportedCapabilityError


class AdapterError(MemoryCheckError):
    """An adapter operation could not be performed faithfully."""


class AdapterConfigurationError(AdapterError):
    """Missing credentials, invalid configuration, or an invalid adapter factory."""


class OperationTimeoutError(AdapterError):
    """A deadline expired; the invariant was not evaluated."""


class OracleError(AdapterError):
    """An observation is unavailable, malformed, or insufficient to evaluate."""


class TraceSerializationError(MemoryCheckError):
    """A requested trace could not be safely serialized or persisted."""


class CleanupError(AdapterError):
    """Owned synthetic test state could not be cleaned up."""


class ContractExecutionError(MemoryCheckError):
    """A contract ERROR, deliberately not an AssertionError."""

    def __init__(self, result: RunResult):
        from memorycheck.reporting.terminal import render_trace
        self.result, self.trace, self.trace_path = result, result.trace, result.trace_path
        super().__init__(render_trace(result.trace, artifact_path=result.trace_path))


class InvariantViolation(AssertionError):
    """An evaluated lifecycle invariant was violated (FAIL)."""

    def __init__(self, result: RunResult):
        from memorycheck.reporting.terminal import render_trace
        self.result, self.trace, self.trace_path = result, result.trace, result.trace_path
        super().__init__(render_trace(result.trace, artifact_path=result.trace_path))
