from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memorycheck.trace.schema import Trace


@dataclass
class RunResult:
    trace: Trace
    trace_path: Path | None = None

    @property
    def status(self) -> str:
        return self.trace.status

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def errored(self) -> bool:
        return self.status == "error"

    @property
    def unsuccessful(self) -> bool:
        return self.status in {"failed", "error"}

    def assert_passed(self) -> RunResult:
        from memorycheck.errors import ContractExecutionError, InvariantViolation, UnsupportedCapability

        if self.skipped:
            raise UnsupportedCapability(self.trace.missing_capabilities, self.trace.skip_reason)
        if self.errored:
            raise ContractExecutionError(self)
        if not self.passed:
            raise InvariantViolation(self)
        return self
