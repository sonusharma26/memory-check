"""Sequential execution with full capability preflight before any mutation."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.contracts.model import Contract
from memorycheck.contracts.parser import load_contract
from memorycheck.errors import AdapterError, ContractExecutionError, InvariantViolation, UnsupportedCapability
from memorycheck.config import Settings, get_settings
from memorycheck.oracles import Oracle
from memorycheck.runner.result import RunResult


class ContractRunner:
    def __init__(self, adapter: MemoryAdapter, *, artifact_dir: str | Path | None = None,
                 oracles: Sequence[Oracle] = (), save_all: bool | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.adapter = adapter
        self.artifact_dir = artifact_dir
        self.oracles = oracles
        self.save_all = save_all

    def run(self, contract: Contract | str | Path) -> RunResult:
        from memorycheck.session import MemoryCheck

        contract = load_contract(contract)
        session = MemoryCheck(self.adapter, name=contract.name, artifact_dir=self.artifact_dir,
                              scope=contract.scope, oracles=self.oracles, save_all=self.save_all, settings=self.settings)
        try:
            session._require(contract.required_capabilities)
            session._require_oracles(contract.required_oracles)
        except UnsupportedCapability:
            return session.finish()
        aliases = {}
        try:
            for step in contract.steps:
                args = session.expand(step.arguments)
                if step.operation == "create":
                    aliases[args["id"]] = session.create(args["value"], id=args["id"], scope=args["scope"])
                elif step.operation == "update":
                    session.update(aliases[args["id"]], args["value"], scope=args["scope"])
                elif step.operation == "delete":
                    session.delete(aliases[args["id"]], scope=args["scope"])
                elif step.operation == "restart":
                    session.restart()
                elif step.operation == "expect":
                    session.expect(**args)
                elif step.operation == "query":
                    session.query(**args)
        except (InvariantViolation, ContractExecutionError, AdapterError):
            # Both failure paths have already recorded their failing event.
            pass
        except BaseException as exc:
            if not isinstance(exc, Exception):
                session.record_test_failure("Interrupted contract", error=True)
                session.finish()
                raise
            session.record_test_failure(f"Contract execution error: {type(exc).__name__}", error=True)
        return session.finish()
