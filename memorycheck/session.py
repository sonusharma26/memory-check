"""The fluent pytest-facing lifecycle session; the runner uses the same engine."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import hashlib
import re
from time import monotonic
from typing import Any, TypeVar

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.errors import (AdapterError, ContractError, ContractExecutionError, InvariantViolation,
    MemoryCheckError, OperationTimeoutError, TraceSerializationError, UnsupportedCapability)
from memorycheck.adapters.conformance import validate_adapter
from memorycheck.canaries import Canary, canary
from memorycheck.cleanup import RunLedger
from memorycheck.config import Settings, get_settings
from memorycheck.deadlines import Deadlines, quarantined
from memorycheck.environment import environment
from memorycheck.trace.privacy import Redactor
from memorycheck.oracles import APIOracle, Oracle, StorageOracle
from memorycheck.oracles.base import ORACLE_CAPABILITIES
from memorycheck.runner.result import RunResult
from memorycheck.trace.schema import Check, Event, Observation, Trace
from memorycheck.trace.serializer import save_trace
from memorycheck.types import MemoryRef, Scope

T = TypeVar("T")


def _tokens(values: str | Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    values = [values] if isinstance(values, str) else values
    if not isinstance(values, Sequence) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError("Expected nonempty text values; use is_empty() to assert an empty response")
    return list(dict.fromkeys(values))


class Expectation:
    """Chained assertions share ONE captured observation, never a fresh query."""

    def __init__(self, session: MemoryCheck, event: Event):
        self.session = session
        self.event = event

    def contains(self, *values: str) -> Expectation:
        if not values:
            raise ValueError("contains() needs at least one value")
        return self._extend("contains", values)

    def excludes(self, *values: str) -> Expectation:
        if not values:
            raise ValueError("excludes() needs at least one value")
        return self._extend("excludes", values)

    def is_empty(self) -> Expectation:
        self.event.expected["empty"] = True
        self.session._evaluate(self.event)
        return self

    def is_not_empty(self) -> Expectation:
        self.event.expected["empty"] = False
        self.session._evaluate(self.event)
        return self

    def _extend(self, key: str, values: Sequence[str]) -> Expectation:
        self.event.expected[key] = _tokens(self.event.expected[key] + _tokens(values))
        self.session._evaluate(self.event)
        return self

    @property
    def observations(self) -> dict[str, Observation]:
        return self.event.observations

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(self.event.observations["api"].values)


class MemoryCheck:
    def __init__(
        self, adapter: MemoryAdapter, *, name: str = "memory_lifecycle",
        artifact_dir: str | Path | None = None, scope: Scope | Mapping[str, str] | None = None,
        oracles: Sequence[Oracle] = (), save_all: bool | None = None, settings: Settings | None = None,
    ):
        validate_adapter(adapter)
        self.settings = settings or get_settings()
        self.adapter = adapter
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else self.settings.path(self.settings.trace_dir)
        self.default_scope = Scope.from_mapping(scope)
        self.save_all = self.settings.save_all if save_all is None else save_all
        self.deadlines = Deadlines(adapter, self.settings.timeout_mode)
        self._contract_deadline = monotonic() + self.settings.contract_timeout
        self._finalized = False
        self._deleted: set[str] = set()
        self._touched: set[Scope] = set()
        self.oracles: dict[str, Oracle] = {"api": APIOracle(), "storage": StorageOracle()}
        for oracle in oracles:
            if oracle.name not in ORACLE_CAPABILITIES or oracle.capability != ORACLE_CAPABILITIES[oracle.name]:
                raise ValueError("Extension oracles must use the documented layer and capability names")
            self.oracles[oracle.name] = oracle
        self.trace = Trace(name, adapter.name, sorted(adapter.capabilities), environment=environment(adapter))
        self.trace._redactor = Redactor(self.settings.trace_values)
        self.trace.namespace = "mc_" + self.trace.run_id.replace("-", "")
        self._ledger = RunLedger(self.settings.path(self.settings.run_dir), self.trace.namespace, adapter)
        self._canaries: dict[str, Canary] = {}
        self.trace_path: Path | None = None
        self._aliases: dict[str, MemoryRef] = {}
        self._refs: dict[str, MemoryRef] = {}

    def _active(self) -> None:
        if self.trace.status != "running":
            raise MemoryCheckError("This lifecycle is finished; create a new MemoryCheck session")

    def _require(self, capabilities: Sequence[str] | set[str] | frozenset[str]) -> None:
        missing = set(capabilities) - set(self.adapter.capabilities)
        if missing:
            self.trace.missing_capabilities = sorted(missing)
            self.trace.skip_reason = ("Requires: " + ", ".join(sorted(missing)) + "; adapter: " +
                                      self.adapter.name + "; available: " + ", ".join(sorted(self.adapter.capabilities)))
            self.trace.finish("skipped")
            if self.save_all:
                self.save()
            raise UnsupportedCapability(missing, self.trace.skip_reason)

    def _require_oracles(self, names: Sequence[str] | frozenset[str]) -> None:
        missing = {"oracle:" + name for name in names if name not in self.oracles}
        if missing:
            self.trace.missing_capabilities = sorted(missing)
            self.trace.skip_reason = "No implementation configured for " + ", ".join(sorted(missing))
            self.trace.finish("skipped")
            if self.save_all:
                self.save()
            raise UnsupportedCapability(missing, self.trace.skip_reason)

    def _scope(self, scope: Scope | Mapping[str, str] | None, values: dict[str, str],
               base: Scope | None = None) -> Scope:
        supplied = Scope.from_mapping(scope).as_dict()
        duplicates = set(supplied) & set(values)
        if duplicates:
            raise ValueError("Scope specified twice: " + ", ".join(sorted(duplicates)))
        supplied.update(values)
        return (base if base is not None else self.default_scope).merged(supplied)

    def canary(self, label: str = "canary") -> Canary:
        if label not in self._canaries:
            self._canaries[label] = canary(label, seed=self.trace.namespace)
            self.trace._redactor.safe_values.add(str(self._canaries[label]))
        return self._canaries[label]

    def expand(self, value: Any) -> Any:
        """Only canary templates are expanded; never environment values or code."""
        if isinstance(value, str):
            return re.sub(r"\{\{canary:([A-Za-z][A-Za-z0-9_]{0,47})\}\}",
                          lambda match: self.canary(match.group(1)), value)
        if isinstance(value, dict):
            return {key: self.expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        return value

    def _native_scope(self, logical: Scope) -> Scope:
        if not self.settings.isolate:
            return logical
        result = logical.as_dict()
        candidates = [("user_id", "user_scope"), ("tenant_id", "tenant_scope"), ("session_id", "session_scope")]
        supported = [key for key, capability in candidates if capability in self.adapter.capabilities]
        if not supported:
            note = "Adapter has no namespace-capable scope; its factory must isolate persistent storage between runs."
            if note not in self.trace.notes:
                self.trace.notes.append(note)
            return logical
        for key in supported:
            if key in result:
                digest = hashlib.sha256((key + "\0" + result[key]).encode()).hexdigest()[:20]
                result[key] = f"memorycheck_{self.trace.namespace}_{key}_{digest}"
        if not any(key in result for key in supported):
            result[supported[0]] = f"memorycheck_{self.trace.namespace}_default"
        return Scope.from_mapping(result)

    @staticmethod
    def _error_text(exc: Exception, operation: str) -> str:
        if isinstance(exc, OperationTimeoutError):
            return "OperationTimeoutError: " + str(exc)
        # Exception text, repr, traceback locals and chained SDK errors can contain credentials.
        return f"{type(exc).__name__}: {operation} could not complete; verify backend configuration and availability"

    def _operation(self, operation: str, subject: str | None, scope: Scope,
                   arguments: dict[str, Any], action: Callable[[], T]) -> tuple[T, Event]:
        self._active()
        native = self._native_scope(scope)
        self._require({operation} | set(native.capabilities))
        event = self.trace.event(operation, subject=subject, scope=native.as_dict(), arguments=arguments)
        try:
            if operation == "create" and self.settings.isolate and native != scope:
                self._ledger.touch(native)
                self._touched.add(native)
            timeout = self.settings.restart_timeout if operation == "restart" else self.settings.operation_timeout
            value = self.deadlines.call(action, operation=operation, timeout=timeout,
                                        contract_deadline=self._contract_deadline)
        except Exception as exc:
            event.status = "error"
            event.error = self._error_text(exc, operation)
            self.trace.error = event.error
            self.trace.finish("error")
            if quarantined(self.adapter) and self._touched:
                try:
                    self._ledger.save()
                except OSError:
                    self.trace.notes.append("Cleanup ledger could not be updated after timeout; verify the run with backend tools.")
            self.save()
            raise ContractExecutionError(RunResult(self.trace, self.trace_path)) from None
        event.status = "passed"
        return value, event

    def create(self, value: str, *, id: str | None = None,
               scope: Scope | Mapping[str, str] | None = None, **scope_values: str) -> MemoryRef:
        if not isinstance(value, str) or not value:
            raise ValueError("create() needs nonempty text")
        if id is not None and (not isinstance(id, str) or not id or id in self._aliases):
            raise ValueError("Logical IDs must be nonempty and unique within a session")
        resolved = self._scope(scope, scope_values)
        if isinstance(value, Canary):
            self.trace._redactor.safe_values.add(str(value))

        def action() -> MemoryRef:
            native_id = self.adapter.create(value, scope=self._native_scope(resolved), alias=id)
            if not isinstance(native_id, str) or not native_id or native_id in self._refs:
                raise AdapterError("create() must return a new nonempty native memory ID")
            return MemoryRef(native_id, resolved, id)

        ref, event = self._operation("create", id, resolved, {"value": value, "alias": id}, action)
        event.subject = id or ref.id
        event.arguments["memory_id"] = ref.id
        self._refs[ref.id] = ref
        if id is not None:
            self._aliases[id] = ref
        return ref

    def _resolve(self, memory: MemoryRef | str, scope: Scope | Mapping[str, str] | None,
                 values: dict[str, str]) -> tuple[MemoryRef, Scope]:
        if isinstance(memory, MemoryRef):
            if self._refs.get(memory.id) != memory:
                raise ValueError("MemoryRef must have been created by this session")
            ref = memory
        elif isinstance(memory, str) and memory:
            ref = self._aliases.get(memory) or self._refs.get(memory)
            if ref is None:
                raise ValueError("Mutations require a MemoryRef or an ID created in this session")
        else:
            raise ValueError("Expected a MemoryRef or a previously created ID")
        resolved = self._scope(scope, values, base=ref.scope)
        if resolved != ref.scope:
            raise ValueError("Cannot change a reference's scope during mutation")
        return ref, resolved

    def update(self, memory: MemoryRef | str, value: str, *,
               scope: Scope | Mapping[str, str] | None = None, **scope_values: str) -> MemoryRef:
        if not isinstance(value, str) or not value:
            raise ValueError("update() needs nonempty text")
        ref, resolved = self._resolve(memory, scope, scope_values)
        if isinstance(value, Canary):
            self.trace._redactor.safe_values.add(str(value))
        self._operation("update", ref.alias or ref.id, resolved,
                        {"memory_id": ref.id, "value": value},
                        lambda: self.adapter.update(ref.id, value, scope=self._native_scope(resolved)))
        return ref

    def delete(self, memory: MemoryRef | str, *,
               scope: Scope | Mapping[str, str] | None = None, **scope_values: str) -> None:
        ref, resolved = self._resolve(memory, scope, scope_values)
        self._operation("delete", ref.alias or ref.id, resolved, {"memory_id": ref.id},
                        lambda: self.adapter.delete(ref.id, scope=self._native_scope(resolved)))
        self._deleted.add(ref.id)

    def restart(self) -> None:
        self._operation("restart", None, Scope(), {}, self.adapter.restart)

    def query(self, query: str, *, scope: Scope | Mapping[str, str] | None = None,
              **scope_values: str) -> list[str]:
        """Record a query and return API values without adding an invariant."""
        return list(self.expect(query, scope=scope, **scope_values).values)

    def expect(
        self, query: str, *, contains: str | Sequence[str] | None = None,
        excludes: str | Sequence[str] | None = None, empty: bool | None = None,
        oracles: Sequence[str] = ("api",), oracle: str | None = None,
        match: str = "substring", scope: Scope | Mapping[str, str] | None = None,
        **scope_values: str,
    ) -> Expectation:
        self._active()
        if not isinstance(query, str) or not query:
            raise ValueError("query must be nonempty text; use '*' to scan the reference backend")
        if oracle is not None:
            if tuple(oracles) != ("api",):
                raise ValueError("Specify oracle or oracles, not both")
            oracles = (oracle,)
        if (isinstance(oracles, str) or not oracles or len(set(oracles)) != len(oracles)
                or set(oracles) - ORACLE_CAPABILITIES.keys()):
            raise ValueError("oracles must be a nonempty sequence of unique layer names")
        if match not in {"substring", "exact"}:
            raise ValueError("match must be substring or exact")
        if empty is not None and not isinstance(empty, bool):
            raise ValueError("empty must be a boolean")
        expected = {"oracles": list(oracles), "contains": _tokens(contains),
                    "excludes": _tokens(excludes), "match": match}
        if empty is not None:
            expected["empty"] = empty
        self._validate_expected(expected)
        resolved = self._native_scope(self._scope(scope, scope_values))
        self._require(set(resolved.capabilities) | {ORACLE_CAPABILITIES[name] for name in oracles})
        self._require_oracles(oracles)
        event = self.trace.event("query", subject=query, scope=resolved.as_dict(),
                                 arguments={"query": query}, expected=expected)
        for name, capability in ORACLE_CAPABILITIES.items():
            if capability not in self.adapter.capabilities or name not in self.oracles:
                event.observations[name] = Observation(
                    notes=[f"Requires {capability} and a configured {name} oracle"],
                )
                continue
            try:
                observation = self.deadlines.call(
                    lambda: self.oracles[name].observe(self.adapter, query, resolved),
                    operation=f"{name} observation", timeout=self.settings.operation_timeout,
                    contract_deadline=self._contract_deadline,
                )
                if (not isinstance(observation, Observation)
                        or observation.status not in {"present", "absent", "error", "unavailable"}
                        or not isinstance(observation.complete, bool)
                        or not isinstance(observation.values, list)
                        or any(not isinstance(v, str) for v in observation.values)):
                    raise AdapterError(f"{name} returned a malformed Observation")
                event.observations[name] = observation
            except Exception as exc:
                event.observations[name] = Observation(status="error", error=self._error_text(exc, name))
                if isinstance(exc, OperationTimeoutError):
                    event.status, event.error = "error", self._error_text(exc, name)
                    self.trace.error = event.error
                    self.trace.finish("error")
                    if quarantined(self.adapter) and self._touched:
                        try:
                            self._ledger.save()
                        except Exception:
                            self.trace.notes.append("Cleanup ledger could not be updated after timeout; verify the run with backend tools.")
                    self.save()
                    raise ContractExecutionError(RunResult(self.trace, self.trace_path)) from None
        self._evaluate(event)
        return Expectation(self, event)

    @staticmethod
    def _validate_expected(expected: dict[str, Any]) -> None:
        if set(expected["contains"]) & set(expected["excludes"]):
            raise ValueError("The same text cannot be required and excluded")
        if expected.get("empty") is True and expected["contains"]:
            raise ValueError("empty=True contradicts contains")

    def _evaluate(self, event: Event) -> None:
        self._active()
        self._validate_expected(event.expected)
        expected = event.expected
        has_checks = bool(expected["contains"] or expected["excludes"] or "empty" in expected)
        errors: list[str] = []
        failed = False
        for name in expected["oracles"]:
            observation = event.observations[name]
            observation.checks = []
            if observation.status in {"unavailable", "error"}:
                observation.assertion_status = "error"
                errors.append(f"{name}: {observation.error or 'observation unavailable'}")
                continue
            layer_error = False
            for kind in ("contains", "excludes"):
                for token in expected[kind]:
                    found = any(token == value if expected["match"] == "exact" else token in value
                                for value in observation.values)
                    passed = found if kind == "contains" else not found
                    if not found and not observation.complete:
                        passed = False
                        layer_error = True
                        message = f"Cannot establish {kind} {token!r}: storage/observation scan is incomplete"
                    elif passed:
                        message = f"{token!r} is {'present' if kind == 'contains' else 'absent'}"
                    else:
                        message = (f"Required value {token!r} was not observed" if kind == "contains"
                                   else f"Excluded value {token!r} is still observable")
                    observation.checks.append(Check(kind, token, passed, message))
            if "empty" in expected:
                is_empty = not observation.values
                passed = is_empty == expected["empty"]
                if is_empty and not observation.complete:
                    passed = False
                    layer_error = True
                    message = "Cannot establish emptiness: observation scan is incomplete"
                else:
                    message = f"Expected {'empty' if expected['empty'] else 'nonempty'}; observed {len(observation.values)} value(s)"
                observation.checks.append(Check("empty", expected["empty"], passed, message))
            if layer_error:
                observation.assertion_status = "error"
                errors.append(f"{name}: incomplete observation cannot establish the invariant")
            elif any(not check.passed for check in observation.checks):
                observation.assertion_status = "failed"
                failed = True
            else:
                observation.assertion_status = "passed" if has_checks else "not_checked"
        event.status = "error" if errors else "failed" if failed else "passed" if has_checks else "observed"
        event.error = "; ".join(errors) if errors else None
        if errors or failed:
            self.trace.error = "OracleError: required observation could not establish the invariant" if errors else event.error
            self.trace.finish(event.status)
            self.save()
            result = RunResult(self.trace, self.trace_path)
            if errors or result.errored:
                raise ContractExecutionError(result)
            raise InvariantViolation(result)

    def save(self) -> Path | None:
        try:
            self.trace_path = save_trace(self.trace, self.artifact_dir)
        except Exception as exc:
            # Keep the evaluated events, but a missing requested artifact is an ERROR.
            self.trace.error = "TraceSerializationError: requested artifact could not be persisted"
            self.trace.finish("error")
            note = f"Trace persistence failed ({type(exc).__name__}); check directory permissions and free space."
            if note not in self.trace.notes:
                self.trace.notes.append(note)
        return self.trace_path

    def cleanup(self) -> None:
        """Best-effort owned-state cleanup, separate from all observed invariants."""
        if self.trace.cleanup["status"] != "not_started":
            return
        if not self._refs and not self._touched:
            self.trace.cleanup = {"status": "not_needed"}
            return
        if not self.settings.cleanup:
            self.trace.cleanup = {"status": "disabled"}
            if self._touched:
                self._ledger.payload["status"] = "pending"
                self._ledger.save()
            return
        if quarantined(self.adapter):
            self.trace.cleanup = {"status": "blocked", "reason": "Adapter quarantined; writes may still be in flight"}
            if self._touched:
                self._ledger.payload["status"] = "blocked"
                self._ledger.save()
            return
        deadline = monotonic() + self.settings.cleanup_timeout
        try:
            if self._touched and "cleanup_scope" in self.adapter.capabilities:
                for scope in sorted(self._touched, key=lambda value: sorted(value.as_dict().items())):
                    self.deadlines.call(lambda: self.adapter.cleanup_scope(scope=scope), operation="cleanup",
                                        timeout=self.settings.cleanup_timeout, contract_deadline=deadline)
                self.trace.cleanup = {"status": "completed", "scopes": len(self._touched)}
            elif "delete" in self.adapter.capabilities:
                for ref in self._refs.values():
                    if ref.id not in self._deleted:
                        self.deadlines.call(lambda: self.adapter.delete(ref.id, scope=self._native_scope(ref.scope)),
                                            operation="cleanup", timeout=self.settings.cleanup_timeout, contract_deadline=deadline)
                self.trace.cleanup = {"status": "best_effort", "reason": "Known live IDs only; no scoped storage cleanup is exposed"}
            else:
                self.trace.cleanup = {"status": "unavailable", "reason": "Adapter exposes neither scoped cleanup nor delete"}
                if self._touched:
                    self._ledger.payload["status"] = "pending"
                    self._ledger.save()
                return
            self._ledger.done()
        except Exception as exc:
            self.trace.cleanup = {"status": "error", "error_type": type(exc).__name__}
            self.trace.error = self._error_text(exc, "cleanup")
            self.trace.finish("error")
            if self._touched:
                try:
                    self._ledger.payload["status"] = "pending"
                    self._ledger.save()
                except OSError:
                    self.trace.notes.append("Cleanup ledger could not be saved; verify this run with backend tools.")

    def finish(self) -> RunResult:
        if self._finalized:
            return RunResult(self.trace, self.trace_path)
        self.trace.finish()
        try:
            self.cleanup()
        except Exception as exc:
            self.trace.error = self._error_text(exc, "cleanup")
            self.trace.finish("error")
        if self.save_all or self.trace.status in {"failed", "error"}:
            self.save()
        self._finalized = True
        return RunResult(self.trace, self.trace_path)

    def record_test_failure(self, message: str, *, error: bool = False) -> None:
        """Do not persist arbitrary pytest longrepr text, source lines, or locals."""
        if self.trace.status not in {"failed", "error"}:
            self.trace.error = "ExecutionError: external test execution error" if error else "AssertionError: Python assertion outside lifecycle invariants"
            self.trace.finish("error" if error else "failed")
        self.save()

    def check(self, contract: Any) -> RunResult:
        from memorycheck.runner.executor import ContractRunner
        self._active()
        if self.trace.events:
            raise ContractError("check() starts a full contract; do not mix it with prior fluent operations")
        result = ContractRunner(self.adapter, artifact_dir=self.artifact_dir,
                                oracles=tuple(self.oracles.values()), save_all=self.save_all,
                                settings=self.settings).run(contract)
        self.trace, self.trace_path = result.trace, result.trace_path
        self._finalized = True  # The runner already finalized and cleaned its session.
        return result.assert_passed()

    def __enter__(self) -> MemoryCheck:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc is not None and self.trace.status == "running":
            self.record_test_failure("external exception", error=not isinstance(exc, AssertionError))
        result = self.finish()
        if exc is None:
            result.assert_passed()  # Cleanup/artifact errors must not silently succeed.
