"""Seven core regression tests; no live services, model calls, or package builds."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from memorycheck import (
    ContractError, ContractExecutionError, ContractRunner, InvariantViolation, MemoryCheck, MemoryRecord, Scope,
    StorageSnapshot, builtin_names, load_builtin, parse_contract,
)
from memorycheck.adapters import (
    BrokenDeleteMemory, BrokenIsolationMemory, BrokenRestartMemory, BrokenUpdateMemory,
    LangGraphAdapter, Mem0Adapter, ReferenceMemory,
)
from memorycheck.errors import MemoryCheckError
from memorycheck.reporting import render_trace
from memorycheck.trace import load_trace
from tests.helpers import FakeMem0, FakeStore, LegacyMem0

ROOT = Path(__file__).resolve().parents[1]


def test_reference_corpus(tmp_path):
    assert len(builtin_names()) == 10
    for name in builtin_names():
        with ReferenceMemory(tmp_path / f"{name}.json") as adapter:
            result = ContractRunner(adapter, artifact_dir=tmp_path / "traces").run(load_builtin(name))
            assert result.passed, render_trace(result.trace)
            for event in result.trace.events:
                if event.operation == "query":
                    assert set(event.observations) == {"api", "storage", "context", "behavior"}
                    assert event.observations["context"].status == "unavailable"
    # An independent instance, not just restart() on the same adapter, reads disk.
    path = tmp_path / "independent.json"
    with ReferenceMemory(path) as first:
        first.create("MEMORYCHECK_DURABLE", scope=Scope(user_id="alice"))
    with ReferenceMemory(path) as second:
        assert second.query("DURABLE", scope=Scope(user_id="alice"))[0].value == "MEMORYCHECK_DURABLE"


def test_broken_backends_persist_first_divergence(tmp_path):
    cases = [
        (BrokenDeleteMemory, "delete_removes_value", "delete"),
        (BrokenRestartMemory, "delete_survives_restart", "restart"),
        (BrokenIsolationMemory, "user_isolation", "create"),
        (BrokenUpdateMemory, "update_survives_restart", "update"),
    ]
    paths = set()
    for backend, contract, boundary in cases:
        with backend(tmp_path / f"{backend.__name__}.json") as adapter:
            result = ContractRunner(adapter, artifact_dir=tmp_path / "traces").run(load_builtin(contract))
            assert result.status == "failed", render_trace(result.trace)
            assert result.trace_path and result.trace_path.is_file()
            paths.add(result.trace_path)
            payload = load_trace(result.trace_path)
            assert payload["first_divergence"]["after_operation"] == boundary
            assert "not minimized" in render_trace(result.trace, verbose=True)
            if backend is BrokenRestartMemory:
                queries = [e for e in result.trace.events if e.operation == "query"]
                before_restart = queries[-2]
                target = result.trace.events[0].arguments["value"]
                assert target not in before_restart.observations["api"].values
                assert target in before_restart.observations["storage"].values
                assert before_restart.observations["storage"].assertion_status == "not_checked"
    assert len(paths) == 4


def test_strict_parser_and_capability_preflight(tmp_path):
    invalid = [
        "name: a\nname: b\nsteps: [{restart: {}}]",
        "name: typo\nsteps: [{expect: {query: x, contians: x}}]",
        "name: missing\nsteps: [{delete: {id: unknown}}]",
        "name: contradictory\nsteps: [{expect: {query: x, contains: x, excludes: x}}]",
        "name: null_scope\nscope: {user_id: null}\nsteps: [{restart: {}}]",
        "name: unsafe\nsteps: !!python/object/apply:os.system ['false']",
    ]
    for text in invalid:
        with pytest.raises(ContractError):
            parse_contract('schema_version: "0.1"\n' + text)

    class CreateOnly(ReferenceMemory):
        capabilities = frozenset({"create", "user_scope"})

        def create(self, *args, **kwargs):
            raise AssertionError("Preflight must skip BEFORE the first write")

    with CreateOnly(tmp_path / "preflight.json") as adapter:
        result = ContractRunner(adapter, artifact_dir=tmp_path / "traces").run("builtin:delete_survives_restart")
        assert result.skipped and result.trace.events == []
        assert "restart" in result.trace.missing_capabilities
    with ReferenceMemory(tmp_path / "context.json") as adapter:
        adapter.capabilities |= {"inspect_context"}
        contract = parse_contract('schema_version: "0.1"\n' + "name: context\nsteps: [{expect: {query: x, excludes: x, oracles: [context]}}]")
        result = ContractRunner(adapter, artifact_dir=tmp_path / "traces").run(contract)
        assert result.skipped and result.trace.missing_capabilities == ["oracle:context"]


def test_fluent_scope_and_single_observation(tmp_path):
    class CountingMemory(ReferenceMemory):
        queries = 0

        def query(self, query, *, scope):
            self.queries += 1
            return super().query(query, scope=scope)

    with CountingMemory(tmp_path / "fluent.json") as adapter:
        checker = MemoryCheck(adapter, artifact_dir=tmp_path / "traces")
        ref = checker.create("MEMORYCHECK_CANARY_8317", user_id="alice")
        observation = checker.expect(query="CANARY", user_id="alice")
        observation.contains("MEMORYCHECK_CANARY_8317").excludes("MEMORYCHECK_OTHER")
        assert adapter.queries == 1
        with pytest.raises(ValueError, match="scope"):
            checker.update(ref, "wrong owner", user_id="bob")
        checker.delete(ref)  # Scope travels with the reference.
        checker.restart()
        checker.expect(query="CANARY", user_id="alice").is_empty()
        assert checker.finish().passed
    with BrokenRestartMemory(tmp_path / "broken-fluent.json") as adapter:
        checker = MemoryCheck(adapter, artifact_dir=tmp_path / "traces")
        ref = checker.create("MEMORYCHECK_CANARY_8317", user_id="alice")
        checker.delete(ref)
        checker.restart()
        with pytest.raises(InvariantViolation) as failure:
            checker.expect("CANARY", user_id="alice").excludes("MEMORYCHECK_CANARY_8317")
        assert failure.value.trace_path.is_file()
        assert failure.value.trace.first_divergence["after_operation"] == "restart"


def test_sdk_adapters_share_contracts_using_api_shaped_fakes(tmp_path):
    for sdk_class in (FakeMem0, LegacyMem0, FakeStore):
        for contract in ("delete_survives_restart", "update_survives_restart", "delete_removes_storage_artifacts", "user_isolation_after_restart"):
            state, lifecycle = {}, []
            if sdk_class is FakeStore:
                def inspect_storage(store, namespace):
                    return StorageSnapshot(tuple(MemoryRecord(key, value["text"]) for (ns, key), value in state.items()
                                                 if ns == namespace), "Independent test-fake persistent dictionary")
                adapter = LangGraphAdapter(factory=lambda: FakeStore(state, lifecycle),
                                           storage_inspector=inspect_storage, page_size=1)
                assert adapter.native_namespace(Scope(user_id="a:b")) != adapter.native_namespace(Scope(user_id="a", session_id="b"))
            else:
                def inspect_storage(memory, filters):
                    return StorageSnapshot(tuple(MemoryRecord(row["id"], row["memory"]) for row in state.values()
                                                 if all(row.get(k) == v for k,v in filters.items())),
                                           "Independent test-fake persistent dictionary")
                adapter = Mem0Adapter(factory=lambda: sdk_class(state, lifecycle), storage_inspector=inspect_storage)
            with adapter:
                result = ContractRunner(adapter, artifact_dir=tmp_path / "traces").run("builtin:" + contract)
                assert result.passed, render_trace(result.trace)
                assert lifecycle.count("open") >= 2 and lifecycle.count("close") >= 1
            assert lifecycle.count("open") == lifecycle.count("close")
    borrowed = FakeStore({}, [])
    with LangGraphAdapter(borrowed) as adapter:
        assert "restart" not in adapter.capabilities
        assert "inspect_storage" not in adapter.capabilities
    assert not borrowed.closed  # Caller-owned resources remain caller-owned.


def test_pytest_plugin_and_cli(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "conftest.py").write_text('''
import pytest
from memorycheck.adapters import ReferenceMemory
class NoRestart(ReferenceMemory):
    capabilities = ReferenceMemory.capabilities - {"restart"}
@pytest.fixture
def memory_adapter(request, tmp_path):
    cls = NoRestart if request.node.name == "test_skip" else ReferenceMemory
    with cls(tmp_path / "memory.json") as adapter:
        yield adapter
''', encoding="utf-8")
    (suite / "test_cases.py").write_text('''
def test_pass(memorycheck):
    memorycheck.check("builtin:remember_recall")
def test_fail(memorycheck):
    memorycheck.create("MEMORYCHECK_PRESENT")
    memorycheck.expect("MEMORYCHECK").excludes("MEMORYCHECK_PRESENT")
def test_plain_fail(memorycheck):
    memorycheck.create("MEMORYCHECK_PLAIN")
    assert False, "plain Python failure"
def test_skip(memorycheck):
    memorycheck.check("builtin:remember_restart_recall")
''', encoding="utf-8")
    (suite / "test_yaml.memorycheck.yaml").write_text(
        'schema_version: "0.1"\n' + "name: yaml_recall\nsteps:\n  - create: {id: canary, value: MEMORYCHECK_YAML}\n"
        "  - expect: {query: MEMORYCHECK_YAML, contains: MEMORYCHECK_YAML}\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    traces = tmp_path / "plugin-traces"
    completed = subprocess.run([sys.executable, "-m", "pytest", "-p", "memorycheck.pytest_plugin", "-q",
                                f"--memorycheck-trace-dir={traces}", str(suite)],
                               cwd=suite, env=env, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "2 failed, 2 passed, 1 skipped" in completed.stdout, completed.stdout
    assert len(list(traces.glob("*.json"))) == 2
    for path in traces.glob("*.json"):
        assert load_trace(path)["status"] == "failed"
    summary = tmp_path / "summary.json"
    cli = subprocess.run([sys.executable, "-m", "memorycheck", "run", "builtin:delete_survives_restart",
                          "--adapter", "broken-restart", "--trace-dir", str(tmp_path / "cli-traces"),
                          "--json", str(summary)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert cli.returncode == 1, cli.stdout + cli.stderr
    assert "First divergence" in cli.stdout and "RESTART" in cli.stdout
    assert json.loads(summary.read_text())["summary"] == {"failed": 1}


def test_incomplete_inspection_and_json_schema(tmp_path):
    class IncompleteMemory(ReferenceMemory):
        def inspect_storage(self, *, scope):
            snapshot = super().inspect_storage(scope=scope)
            return StorageSnapshot(snapshot.records, snapshot.coverage, complete=False)

    with IncompleteMemory(tmp_path / "incomplete.json") as adapter:
        checker = MemoryCheck(adapter, artifact_dir=tmp_path / "traces")
        checker.create("MEMORYCHECK_PRESENT")
        # Positive evidence is valid even in a partial scan.
        checker.expect("MEMORYCHECK", oracles=["api", "storage"]).contains("MEMORYCHECK_PRESENT")
        with pytest.raises(ContractExecutionError) as failure:
            checker.expect("MEMORYCHECK", oracles=["api", "storage"]).excludes("MEMORYCHECK_ABSENT")
        result = failure.value.result
        assert result.status == "error", "A partial scan must never prove absence"
        payload = load_trace(result.trace_path)
        schema = json.loads((ROOT / "memorycheck/trace/trace.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(payload, schema)
    bad = tmp_path / "bad-trace.json"
    bad.write_text('{"schema_version": "9000"}')
    with pytest.raises(MemoryCheckError):
        load_trace(bad)
