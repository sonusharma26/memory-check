"""A small operational CLI; pytest remains the primary interface."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from memorycheck._version import __version__
from memorycheck.adapters.conformance import check_adapter
from memorycheck.adapters.loading import load_configured_adapter
from memorycheck.cleanup import RUN_PATTERN, cleanup_ledger, load_ledger
from memorycheck.config import Settings, read_settings
from memorycheck.contracts import Contract, builtin_names, load_builtin, load_contract
from memorycheck.deadlines import close_adapter
from memorycheck.environment import distribution_version, environment
from memorycheck.errors import ContractError, MemoryCheckError
from memorycheck.reporting.terminal import render_trace
from memorycheck.runner import ContractRunner, RunResult
from memorycheck.trace.schema import Trace
from memorycheck.trace.serializer import load_trace, save_trace, write_json_atomic

LABELS = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "error": "ERROR"}


def _init_template(adapter: str) -> str:
    options = {
        "reference": '''# MemoryCheck project configuration. Keep this file out of production deployments.
[tool.memorycheck]
adapter = "reference"
trace_values = "redacted"

[tool.memorycheck.adapter_options]
path = ".memorycheck/reference.json"
''',
        "langgraph": '''# MemoryCheck project configuration. Use a dedicated test database only.
[tool.memorycheck]
adapter = "langgraph"
trace_values = "redacted"

[tool.memorycheck.adapter_options]
connection_env = "MEMORYCHECK_POSTGRES_DSN"
setup = true
''',
        "mem0": '''# MemoryCheck project configuration. Do not commit provider credentials.
[tool.memorycheck]
adapter = "mem0"
trace_values = "redacted"

[tool.memorycheck.adapter_options]
config_file = "mem0-config.json"
''',
    }
    return options[adapter]


def _contracts(sources: list[str]) -> list[Contract]:
    contracts = []
    for source in sources:
        if source == "builtin":
            contracts.extend(load_builtin(name) for name in builtin_names())
        elif Path(source).is_dir():
            paths = sorted(path for path in Path(source).rglob("*") if path.suffix.lower() in {".yaml", ".yml"})
            contracts.extend(load_contract(path) for path in paths)
        else:
            contracts.append(load_contract(source))
    names = [contract.name for contract in contracts]
    if len(names) != len(set(names)):
        raise ContractError("Selected contracts have duplicate names; select each contract only once")
    return contracts


def _common(parser):
    parser.add_argument("--config", metavar="PYPROJECT")
    parser.add_argument("--adapter", metavar="NAME_OR_MODULE:FACTORY")
    parser.add_argument("--trace-dir", metavar="PATH")
    parser.add_argument("--trace-values", choices=["redacted", "synthetic"])
    parser.add_argument("--save-all", action="store_true", default=None)
    parser.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=None)
    for name in ["operation", "restart", "contract", "cleanup"]:
        parser.add_argument(f"--{name}-timeout", type=float, metavar="SECONDS")
    parser.add_argument("--timeout-mode", choices=["auto", "signal", "thread"])
    parser.add_argument("--verbose", "-v", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memorycheck", description="Find stale, resurrected, and leaked persistent memory state")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser("init", help="Create a standalone MemoryCheck configuration file")
    init.add_argument("--adapter", choices=["reference", "langgraph", "mem0"], default="reference")
    init.add_argument("--path", default="memorycheck.toml", metavar="PATH")
    init.add_argument("--force", action="store_true", help="Replace an existing file at --path")
    run = commands.add_parser("run", help="Run YAML, a directory, builtin, or builtin:NAME (default: builtin)")
    _common(run)
    run.add_argument("contracts", nargs="*", default=["builtin"], metavar="CONTRACT")
    run.add_argument("--json", dest="json_path", metavar="PATH")
    run.add_argument("--fail-fast", action="store_true")
    commands.add_parser("list", help="Discover built-in contracts, categories, and required capabilities")
    show = commands.add_parser("show", help="Explain a built-in contract or YAML file")
    show.add_argument("contract")
    show.add_argument("--yaml", action="store_true", help="Print the authored contract rather than a summary")
    validate = commands.add_parser("validate", help="Validate contracts without loading or contacting a backend")
    validate.add_argument("contracts", nargs="+", metavar="CONTRACT")
    trace = commands.add_parser("trace", aliases=["show-trace"], help="Render an existing JSON trace")
    trace.add_argument("path")
    doctor = commands.add_parser("doctor", help="Diagnose configuration and declared capabilities; --probe writes synthetic test data")
    _common(doctor)
    doctor.add_argument("--probe", action="store_true")
    doctor.add_argument("--json", dest="json_path", metavar="PATH")
    adapter = commands.add_parser("adapter", help="Adapter developer tools")
    adapter_commands = adapter.add_subparsers(dest="adapter_command", required=True)
    check = adapter_commands.add_parser("check", help="Exercise advertised methods using an isolated synthetic lifecycle")
    _common(check)
    cleanup = commands.add_parser("cleanup", help="List private cleanup ledgers; --run-id ID --apply replays scoped cleanup")
    _common(cleanup)
    cleanup.add_argument("--run-id", metavar="MC_ID")
    cleanup.add_argument("--apply", action="store_true")
    return parser


def _settings(args) -> Settings:
    names = ["adapter", "trace_dir", "trace_values", "save_all", "operation_timeout", "restart_timeout",
             "contract_timeout", "cleanup_timeout", "cleanup", "timeout_mode"]
    return read_settings(getattr(args, "config", None), overrides={name: getattr(args, name, None) for name in names})


def _close(adapter, settings, result=None):
    try:
        close_adapter(adapter, timeout=settings.cleanup_timeout, mode=settings.timeout_mode)
    except Exception as exc:
        if result is None:
            raise MemoryCheckError(f"Adapter close failed ({type(exc).__name__}); check resource ownership") from None
        result.trace.error = f"{type(exc).__name__}: adapter close failed"
        result.trace.finish("error")
        result.trace_path = save_trace(result.trace, settings.path(settings.trace_dir))


def _exit(results):
    if any(result.errored for result in results):
        return 2
    if any(result.failed for result in results):
        return 1
    if not results or all(result.skipped for result in results):
        return 5
    return 0


def _run_one(contract, settings):
    adapter = None
    result = None
    try:
        adapter = load_configured_adapter(settings)
        result = ContractRunner(adapter, settings=settings).run(contract)
    except (MemoryCheckError, ValueError, TypeError) as exc:
        trace = Trace(contract.name, settings.adapter, [], environment=environment())
        trace.error = f"{type(exc).__name__}: adapter initialization or execution failed"
        trace.finish("error")
        result = RunResult(trace, save_trace(trace, settings.path(settings.trace_dir)))
    finally:
        if adapter is not None:
            # KeyboardInterrupt/SystemExit must not be hidden by resource-close errors.
            if result is None:
                try:
                    _close(adapter, settings)
                except Exception:
                    pass
            else:
                _close(adapter, settings, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"MemoryCheck {__version__}")
        if args.verbose:
            for key, value in environment().items():
                if key != "memorycheck_version":
                    print(f"{key}: {value or 'not installed / not applicable'}")
            for label, package in [("mem0", "mem0ai"), ("langgraph", "langgraph"), ("postgres", "langgraph-checkpoint-postgres")]:
                print(f"{label}: {distribution_version(package) or 'optional dependency not installed'}")
        return 0
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "init":
            path = Path(args.path).resolve()
            if path.exists() and not args.force:
                raise MemoryCheckError(f"{path} already exists; choose another --path or use --force")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_init_template(args.adapter), encoding="utf-8")
            print(f"Created {path}")
            print(f"Next: memorycheck doctor --config {path.name} --probe")
            if args.adapter == "langgraph":
                print("Set MEMORYCHECK_POSTGRES_DSN to a dedicated test database and install memorycheck[postgres].")
            elif args.adapter == "mem0":
                print("Create the referenced private Mem0 config and install memorycheck[mem0].")
            return 0
        if args.command == "list":
            contracts = [load_builtin(name) for name in builtin_names()]
            for category in ["Correction", "Deletion", "Restart / recall", "Isolation"]:
                print(category)
                for contract in contracts:
                    if contract.category == category:
                        print(f"  {contract.name}\n    Requires: {', '.join(sorted(contract.required_capabilities))}")
            return 0
        if args.command == "show":
            source = "builtin:" + args.contract if args.contract in builtin_names() else args.contract
            contract = load_contract(source)
            if args.yaml:
                import yaml
                print(yaml.safe_dump(contract.to_dict(), sort_keys=False), end="")
            else:
                print(f"{contract.name}\n\nPurpose: {contract.description}\nSchema: {contract.schema_version}")
                print("Requires: " + ", ".join(sorted(contract.required_capabilities)))
                print("Required oracles: " + ", ".join(sorted(contract.required_oracles)))
                print("Storage: " + ("required" if "storage" in contract.required_oracles else "optional; unavailable is not a pass"))
                print("Operations: " + " -> ".join(step.operation for step in contract.steps))
            return 0
        if args.command == "validate":
            contracts = _contracts(args.contracts)
            print(f"Validated {len(contracts)} contract(s); no backend was loaded.")
            return 0 if contracts else 5
        if args.command in {"trace", "show-trace"}:
            print(render_trace(load_trace(args.path), artifact_path=args.path, verbose=True))
            return 0
        settings = _settings(args)
        if args.command == "cleanup":
            directory = settings.path(settings.run_dir)
            if args.run_id and not RUN_PATTERN.fullmatch(args.run_id):
                raise MemoryCheckError("--run-id must be an exact generated mc_ namespace")
            paths = [directory / f"{args.run_id}.json"] if args.run_id else sorted(directory.glob("mc_*.json"))
            if args.apply:
                if not args.run_id:
                    raise MemoryCheckError("Cleanup writes require an explicit --run-id; bulk cleanup is not supported")
                adapter = load_configured_adapter(settings)
                try:
                    count = cleanup_ledger(paths[0], adapter, timeout=settings.cleanup_timeout, mode=settings.timeout_mode)
                    print(f"Cleaned {count} owned scope(s) for {args.run_id}.")
                finally:
                    _close(adapter, settings)
            else:
                for path in paths:
                    data = load_ledger(path)
                    state = "BLOCKED (timeout)" if data["uncertain"] else data.get("status", "pending").upper()
                    print(f"{data['run_id']}  {data['adapter']}  {len(data['scopes'])} scope(s)  {state}")
                print(f"{len(paths)} ledger(s). No backend contacted; use --run-id ID --apply for an explicit scoped cleanup.")
            return 0
        if args.command in {"doctor", "adapter"}:
            adapter = load_configured_adapter(settings)
            result = None
            try:
                runnable, skipped = [], []
                for name in builtin_names():
                    contract = load_builtin(name)
                    missing = sorted(contract.required_capabilities - adapter.capabilities)
                    (skipped if missing else runnable).append({"contract": name, "missing": missing})
                info = {**environment(adapter), "adapter": adapter.name, "declared_capabilities": sorted(adapter.capabilities),
                        "runnable": runnable, "skipped": skipped, "restart_semantics": adapter.restart_description,
                        "probe": None}
                print(f"MemoryCheck {__version__}\nAdapter: {adapter.name}\nAdapter initialized and declarations validated.")
                for name in sorted(adapter.capabilities):
                    print(f"  DECLARED {name}")
                if "inspect_storage" not in adapter.capabilities:
                    print("  Storage: NOT AVAILABLE (not verified)")
                print(f"Built-in contracts: {len(runnable)} runnable, {len(skipped)} skipped")
                for entry in skipped:
                    print(f"  SKIP {entry['contract']}: requires {', '.join(entry['missing'])}")
                if args.command == "adapter" or args.probe:
                    print("Running the isolated synthetic adapter conformance probe.")
                    result = check_adapter(adapter, settings=settings)
                    _close(adapter, settings, result)
                    adapter = None
                    info["probe"] = result.trace.to_dict()
                    print(render_trace(result.trace, artifact_path=result.trace_path, verbose=args.verbose))
                else:
                    print("Declarations are not proof of behavior. Run 'memorycheck adapter check' to exercise them.")
                if getattr(args, "json_path", None):
                    write_json_atomic(args.json_path, info)
                return _exit([result]) if result is not None else (0 if runnable else 5)
            finally:
                if adapter is not None:
                    _close(adapter, settings, result)
        contracts = _contracts(args.contracts or ["builtin"])
        if not contracts:
            print("No contracts found.", file=sys.stderr)
            return 5
        results = []
        for contract in contracts:
            result = _run_one(contract, settings)
            results.append(result)
            if result.unsuccessful or args.verbose:
                print(render_trace(result.trace, artifact_path=result.trace_path, verbose=args.verbose))
            else:
                print(f"{LABELS[result.status]:5} {contract.name}" + (f" - {result.trace.skip_reason}" if result.skipped else ""))
            if args.fail_fast and result.unsuccessful:
                break
        counts = Counter(result.status for result in results)
        print("\n" + ", ".join(f"{counts[status]} {LABELS[status]}" for status in ("passed", "failed", "error", "skipped")))
        if args.json_path:
            write_json_atomic(args.json_path, {"schema_version": "0.1", "summary": dict(counts),
                "results": [{"trace_path": str(result.trace_path) if result.trace_path else None,
                             "trace": result.trace.to_dict()} for result in results]})
        return _exit(results)
    except (MemoryCheckError, OSError, ValueError, TypeError) as exc:
        if isinstance(exc, MemoryCheckError):
            print(f"MemoryCheck ERROR: {exc}", file=sys.stderr)
        else:
            print(f"MemoryCheck ERROR ({type(exc).__name__}); check configuration, paths, and permissions.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("MemoryCheck interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
