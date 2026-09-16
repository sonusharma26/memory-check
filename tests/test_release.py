"""Three focused release regressions. No live provider calls or package builds."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from memorycheck import (
    ContractError, ContractExecutionError, ContractRunner, InvariantViolation, MemoryCheck,
    MemoryRecord, MemoryRef, QueryObservation, Scope, Settings, canary, parse_contract,
)
from memorycheck.adapters import ReferenceMemory
from memorycheck.adapters.loading import load_configured_adapter
from memorycheck.cleanup import cleanup_ledger, load_ledger
from memorycheck.config import read_settings
from memorycheck.trace import load_trace

ROOT = Path(__file__).resolve().parents[1]


def test_release_privacy_namespace_and_cleanup(tmp_path):
    secret = "sensitive-personal-payload@example.invalid"
    metadata_secret = "DO_NOT_EXPORT_THIS_CREDENTIAL"
    class PrivateMemory(ReferenceMemory):
        def query(self, query, *, scope):
            response = super().query(query, scope=scope)
            return QueryObservation(tuple(MemoryRecord(r.id, r.value, r.scope, {"api_key": metadata_secret})
                                          for r in response), raw={"credential": metadata_secret})
    settings = Settings(project_root=tmp_path, save_all=True)
    with PrivateMemory(tmp_path / "private.json") as adapter:
        first = MemoryCheck(adapter, settings=settings)
        ref = first.create(secret, user_id="person@example.invalid")
        with pytest.raises(ValueError, match="session"):
            first.delete(MemoryRef("unowned", Scope(user_id="person@example.invalid")))
        second = MemoryCheck(adapter, settings=settings)
        second.expect(secret, user_id="person@example.invalid").is_empty()
        assert first.trace.namespace != second.trace.namespace
        with pytest.raises(InvariantViolation):
            first.expect(secret, user_id="person@example.invalid").excludes(secret)
        result = first.finish()
        payload = result.trace_path.read_text()
        assert secret not in payload and metadata_secret not in payload and "person@example.invalid" not in payload
        data = load_trace(result.trace_path)
        assert data["privacy"] == {"trace_values": "redacted", "metadata": "omitted", "raw": "omitted"}
        assert data["memorycheck_version"] == "0.1.0"
        assert data["cleanup"]["status"] == "completed"
        assert json.loads(adapter.path.read_text())["records"] == []
        if os.name == "posix":
            assert result.trace_path.stat().st_mode & 0o077 == 0
        second.finish()
    with ReferenceMemory(tmp_path / "synthetic.json") as adapter:
        checker = MemoryCheck(adapter, settings=settings.updated(trace_values="synthetic"))
        value = checker.canary("REGION")
        checker.create(value)
        checker.expect("MEMORYCHECK").contains(value)
        result = checker.finish()
        assert str(value) in result.trace_path.read_text()
        assert canary("REGION") != canary("REGION")
    # A stale ledger can only be applied to the same explicitly selected backend.
    persistent = tmp_path / "persistent.json"
    with ReferenceMemory(persistent) as adapter:
        session = MemoryCheck(adapter, settings=settings.updated(cleanup=False))
        session.create(canary("STALE"))
        session.finish()
        ledger = next((tmp_path / ".memorycheck/runs").glob("*.json"))
        assert load_ledger(ledger)["status"] == "pending"
        assert cleanup_ledger(ledger, adapter, timeout=2, mode="auto") == 1
        assert json.loads(persistent.read_text())["records"] == []


def test_release_timeouts_and_typed_errors(tmp_path):
    released, completed = threading.Event(), threading.Event()
    class BlockingMemory(ReferenceMemory):
        def query(self, query, *, scope):
            try:
                released.wait(2)
                return super().query(query, scope=scope)
            finally:
                completed.set()
    adapter = BlockingMemory(tmp_path / "blocking.json")
    settings = Settings(project_root=tmp_path, timeout_mode="thread", operation_timeout=0.04,
                        contract_timeout=2, cleanup_timeout=0.05)
    checker = MemoryCheck(adapter, settings=settings)
    checker.create("MEMORYCHECK_TIMEOUT")
    start = time.monotonic()
    try:
        with pytest.raises(ContractExecutionError):
            checker.expect("TIMEOUT").contains("MEMORYCHECK_TIMEOUT")
        assert time.monotonic() - start < 0.8
        result = checker.finish()
        assert result.errored and not result.failed
        assert result.trace.cleanup["status"] == "blocked"
        with pytest.raises(ContractExecutionError):
            result.assert_passed()
        ledger = next((tmp_path / ".memorycheck/runs").glob("*.json"))
        assert load_ledger(ledger)["uncertain"] is True
        assert "OperationTimeoutError" in result.trace_path.read_text()
    finally:
        released.set()
        assert completed.wait(1)
        adapter.close()
    class BadShape(ReferenceMemory):
        def query(self, query, *, scope):
            return [{"value": "not normalized"}]
    with BadShape(tmp_path / "badshape.json") as adapter:
        result = ContractRunner(adapter, settings=settings.updated(timeout_mode="auto", operation_timeout=2)).run("builtin:remember_recall")
        assert result.errored


def test_release_configuration_diagnostics_and_pytest_status(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('''
[tool.memorycheck]
adapter = "reference"
trace_values = "redacted"
operation_timeout = 2
[tool.memorycheck.adapter_options]
path = "state.json"
''')
    settings = read_settings(start=root)
    assert settings.operation_timeout == 2
    with load_configured_adapter(settings) as adapter:
        assert adapter.path == root / "state.json"
    malformed = 'schema_version: "0.1"\nname: typo\nsteps:\n  - remove: {id: x}\n'
    with pytest.raises(ContractError) as exc:
        parse_contract(malformed, source="contracts/delete.yaml")
    assert exc.value.line == 4 and "Did you mean 'delete'" in str(exc.value)
    assert "contracts/delete.yaml:4" in str(exc.value)
    with pytest.raises(ContractError, match="schema_version"):
        parse_contract("name: missing\nsteps: []")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    # CLI static diagnostics and the functional adapter probe use the same config.
    for args in [("doctor",), ("adapter", "check"), ("validate", "builtin"), ("show", "delete_survives_restart")]:
        proc = subprocess.run([sys.executable, "-m", "memorycheck", *args], cwd=root, env=env,
                              capture_output=True, text=True, timeout=15)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    suite = tmp_path / "pytest-suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\n")
    (suite / "conftest.py").write_text('''
import pytest
from memorycheck.adapters import ReferenceMemory
class Unauthorized(ReferenceMemory):
    def create(self, *args, **kwargs):
        raise RuntimeError("provider_secret_MUST_NOT_APPEAR")
@pytest.fixture
def memory_adapter(request, tmp_path):
    cls = Unauthorized if request.node.name == "test_execution_error" else ReferenceMemory
    with cls(tmp_path / "state.json") as adapter:
        yield adapter
''')
    (suite / "test_status.py").write_text('''
def test_execution_error(memorycheck):
    memorycheck.check("builtin:remember_recall")
def test_invariant_fail(memorycheck):
    memorycheck.create("private_assertion_payload")
    memorycheck.expect("private").excludes("private_assertion_payload")
''')
    proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "memorycheck.pytest_plugin", "--memorycheck", "-q"],
                          cwd=suite, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 failed, 10 passed, 1 error" in proc.stdout, proc.stdout + proc.stderr
    assert "provider_secret_MUST_NOT_APPEAR" not in proc.stdout
    assert "private_assertion_payload" not in proc.stdout
