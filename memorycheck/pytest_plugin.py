"""Pytest-native fixtures, contract collection, typed errors, and private reporting."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from memorycheck._version import __version__
from memorycheck.adapters.base import MemoryAdapter
from memorycheck.adapters.loading import load_configured_adapter
from memorycheck.config import Settings, read_settings
from memorycheck.contracts import builtin_names, load_builtin, load_contract
from memorycheck.deadlines import close_adapter
from memorycheck.errors import ContractError, MemoryCheckError, UnsupportedCapability
from memorycheck.reporting.terminal import render_trace
from memorycheck.session import MemoryCheck

_SESSION = pytest.StashKey[MemoryCheck]()
_SETTINGS = pytest.StashKey[Settings]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("memorycheck", "Persistent memory lifecycle conformance")
    group.addoption("--memorycheck", action="store_true", help="Add the 10 built-in lifecycle contracts to collection")
    group.addoption("--memorycheck-config", metavar="PYPROJECT")
    group.addoption("--memorycheck-trace-dir", metavar="PATH")
    group.addoption("--memorycheck-trace-values", choices=["redacted", "synthetic"])
    group.addoption("--memorycheck-save-all", action="store_true", default=None)
    group.addoption("--memorycheck-verbose", action="store_true", help="Show operation timelines without disabling redaction")
    group.addoption("--memorycheck-collect-yaml", action="store_true", help="Collect all YAML under selected test paths as contracts")
    group.addoption("--memorycheck-adapter", metavar="NAME_OR_MODULE:FACTORY")
    group.addoption("--memorycheck-no-cleanup", dest="memorycheck_cleanup", action="store_false", default=None)
    group.addoption("--memorycheck-timeout-mode", choices=["auto", "signal", "thread"])
    for name in ["operation", "restart", "contract", "cleanup"]:
        group.addoption(f"--memorycheck-{name}-timeout", type=float, metavar="SECONDS")


def pytest_configure(config: pytest.Config) -> None:
    for marker, description in {
        "memorycheck": "memory lifecycle conformance test",
        "memorycheck_restart": "reconstructs adapter runtime against persistent storage",
        "memorycheck_destructive": "creates, updates, or deletes synthetic test data",
    }.items():
        config.addinivalue_line("markers", f"{marker}: {description}")
    names = ["adapter", "trace_dir", "trace_values", "save_all", "operation_timeout", "restart_timeout",
             "contract_timeout", "cleanup_timeout", "timeout_mode"]
    overrides = {name: config.getoption("memorycheck_" + name) for name in names}
    overrides["cleanup"] = config.getoption("memorycheck_cleanup")
    try:
        config.stash[_SETTINGS] = read_settings(config.getoption("memorycheck_config"), start=config.rootpath, overrides=overrides)
    except MemoryCheckError as exc:
        raise pytest.UsageError(str(exc)) from None


def pytest_report_header(config: pytest.Config):
    if config.getoption("memorycheck") or config.getoption("memorycheck_verbose"):
        return f"MemoryCheck {__version__} | private failure traces: {config.stash[_SETTINGS].trace_dir}"
    return None


@pytest.fixture
def memory_adapter(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MemoryAdapter]:
    """Override to connect a backend. The overriding fixture owns resource close()."""
    settings = request.config.stash[_SETTINGS]
    adapter = load_configured_adapter(settings, reference_path=tmp_path / "memorycheck-reference.json")
    try:
        yield adapter
    finally:
        try:
            close_adapter(adapter, timeout=settings.cleanup_timeout, mode=settings.timeout_mode)
        except Exception as exc:
            raise MemoryCheckError(f"Adapter close failed ({type(exc).__name__}); verify resource ownership") from None


class _PytestMemoryCheck(MemoryCheck):
    def _require(self, capabilities) -> None:
        try:
            super()._require(capabilities)
        except UnsupportedCapability as exc:
            pytest.skip(str(exc))

    def _require_oracles(self, names) -> None:
        try:
            super()._require_oracles(names)
        except UnsupportedCapability as exc:
            pytest.skip(str(exc))

    def check(self, contract):
        try:
            return super().check(contract)
        except UnsupportedCapability as exc:
            pytest.skip(str(exc))


@pytest.fixture
def memorycheck(request: pytest.FixtureRequest, memory_adapter: MemoryAdapter) -> Iterator[MemoryCheck]:
    settings = request.config.stash[_SETTINGS]
    # Do not use parametrized node IDs as trace names: they can contain secrets.
    name = getattr(request.node, "originalname", None) or request.node.name.split("[", 1)[0]
    session = _PytestMemoryCheck(memory_adapter, name=name, settings=settings)
    request.node.stash[_SESSION] = session
    try:
        yield session
    finally:
        session.finish()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    session = item.stash.get(_SESSION, None)
    if session is None or report.when == "setup":
        return
    if report.failed:
        error = (report.when != "call" or session.trace.status == "error" or
                 call.excinfo is not None and not isinstance(call.excinfo.value, AssertionError))
        session.record_test_failure("pytest failure", error=error)
    elif report.skipped and report.when == "call" and session.trace.status == "running":
        session.trace.skip_reason = "Pytest skipped this lifecycle; see the pytest skip reason"
        session.trace.finish("skipped")
    result = session.finish()
    # A cleanup or infrastructure ERROR must not become an XFAIL or a passing test.
    if result.errored and (report.when == "call" or report.failed):
        report.outcome = "failed"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    report.user_properties.append(("memorycheck_status", result.status))
    if result.trace_path:
        report.user_properties.append(("memorycheck_trace", str(result.trace_path)))
    if report.failed:
        report.longrepr = render_trace(session.trace, artifact_path=session.trace_path,
                                      verbose=item.config.getoption("memorycheck_verbose"))
        # Backend logs can contain payloads. This protects captured failure output;
        # user -s / live logging remains outside the plugin's control.
        report.sections = []


@pytest.hookimpl(tryfirst=True)
def pytest_report_teststatus(report, config):
    if report.failed and dict(report.user_properties).get("memorycheck_status") == "error":
        return "error", "E", "ERROR"
    return None


def _item(parent, contract):
    def run_contract(memorycheck):
        memorycheck.check(contract)
    item = pytest.Function.from_parent(parent, name=contract.name, callobj=run_contract)
    item.add_marker("memorycheck")
    item.add_marker("memorycheck_destructive")
    if "restart" in contract.required_capabilities:
        item.add_marker("memorycheck_restart")
    return item


class _ContractFile(pytest.File):
    def collect(self):
        try:
            contract = load_contract(self.path)
        except ContractError as exc:
            raise self.CollectError(str(exc)) from None
        yield _item(self, contract)


class _BuiltinFile(pytest.File):
    def collect(self):
        for name in builtin_names():
            yield _item(self, load_builtin(name))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    if config.getoption("memorycheck"):
        collector = _BuiltinFile.from_parent(session, path=Path(config.rootpath) / "memorycheck_builtin.memorycheck.yaml")
        items.extend(collector.collect())
    for item in items:
        if "memorycheck" in getattr(item, "fixturenames", ()):
            item.add_marker("memorycheck")
            item.add_marker("memorycheck_destructive")


def pytest_collect_file(file_path: Path, parent: pytest.Collector):
    if file_path.suffix.lower() not in {".yaml", ".yml"}:
        return None
    if (parent.session.isinitpath(file_path) or file_path.name.endswith((".memorycheck.yaml", ".memorycheck.yml"))
            or parent.config.getoption("memorycheck_collect_yaml")):
        return _ContractFile.from_parent(parent, path=file_path)
    return None
