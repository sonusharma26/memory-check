"""Trusted project configuration selects adapters; contract YAML never imports code."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from memorycheck.adapters.base import MemoryAdapter
from memorycheck.adapters.conformance import validate_adapter
from memorycheck.adapters.reference import (
    BrokenDeleteMemory, BrokenIsolationMemory, BrokenRestartMemory, BrokenUpdateMemory, ReferenceMemory,
)
from memorycheck.config import Settings
from memorycheck.deadlines import Deadlines
from memorycheck.errors import AdapterConfigurationError, OperationTimeoutError

REFERENCE_ADAPTERS = {"reference": ReferenceMemory, "broken-delete": BrokenDeleteMemory,
    "broken-restart": BrokenRestartMemory, "broken-isolation": BrokenIsolationMemory,
    "broken-update": BrokenUpdateMemory}


def _options(options: dict, allowed: set[str]) -> None:
    if set(options) - allowed:
        raise AdapterConfigurationError("Unknown adapter_options field; consult docs/ADAPTERS.md")


def load_adapter(spec: str, *, reference_path: str | Path | None = None,
                 options: dict[str, Any] | None = None, project_root: Path | None = None) -> MemoryAdapter:
    options = dict(options or {})
    root = Path(project_root or Path.cwd()).resolve()
    try:
        if spec in REFERENCE_ADAPTERS:
            _options(options, {"path"})
            path = options.get("path", reference_path)
            if path is not None:
                path = Path(path).expanduser()
                path = path if path.is_absolute() else root / path
            adapter = REFERENCE_ADAPTERS[spec](path)
        elif spec == "mem0":
            from memorycheck.adapters.mem0 import Mem0Adapter
            _options(options, {"config_file", "config", "namespace", "search_limit", "search_api"})
            config = options.pop("config", None)
            filename = options.pop("config_file", None) or os.environ.get("MEMORYCHECK_MEM0_CONFIG")
            if config is not None and filename:
                raise AdapterConfigurationError("Select one Mem0 config or config_file, not both")
            if config is None:
                if not filename:
                    raise AdapterConfigurationError("Mem0 needs adapter_options.config_file or MEMORYCHECK_MEM0_CONFIG pointing to a JSON config")
                path = Path(filename).expanduser()
                path = path if path.is_absolute() else root / path
                if path.stat().st_size > 1_048_576:
                    raise AdapterConfigurationError("Mem0 configuration exceeds the 1 MiB limit")
                config = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise AdapterConfigurationError("Mem0 configuration must be a JSON object")
            adapter = Mem0Adapter.from_config(config, **options)
        elif spec == "langgraph":
            from memorycheck.adapters.langgraph import LangGraphAdapter
            _options(options, {"connection_env", "setup", "namespace", "query_mode", "page_size", "max_records"})
            key = options.pop("connection_env", "MEMORYCHECK_POSTGRES_DSN")
            if not isinstance(key, str) or not os.environ.get(key):
                raise AdapterConfigurationError("LangGraph needs a PostgreSQL DSN in MEMORYCHECK_POSTGRES_DSN or the configured connection_env")
            if "setup" in options and not isinstance(options["setup"], bool):
                raise AdapterConfigurationError("LangGraph setup must be boolean")
            adapter = LangGraphAdapter.from_postgres(os.environ[key], **options)
        elif ":" in spec:
            module_name, attribute = spec.rsplit(":", 1)
            # Importing a project factory is explicitly trusted, just like conftest.py.
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            factory = getattr(importlib.import_module(module_name), attribute)
            if not callable(factory):
                raise AdapterConfigurationError("Configured adapter factory is not callable")
            adapter = factory(**options)
        else:
            raise AdapterConfigurationError("Unknown adapter; use reference, mem0, langgraph, a broken-demo name, or trusted module:factory")
        validate_adapter(adapter)
        return adapter
    except AdapterConfigurationError:
        raise
    except ImportError:
        raise AdapterConfigurationError("Optional adapter dependency is missing; install the matching extra or check the factory module") from None
    except Exception as exc:
        raise AdapterConfigurationError(f"Adapter initialization failed ({type(exc).__name__}); verify credentials, dependencies, and backend configuration") from None


def load_configured_adapter(settings: Settings, *, reference_path: str | Path | None = None) -> MemoryAdapter:
    class Initialization:
        pass
    return Deadlines(Initialization(), settings.timeout_mode).call(
        lambda: load_adapter(settings.adapter, reference_path=reference_path,
                             options=dict(settings.adapter_options), project_root=settings.project_root),
        operation="adapter initialization", timeout=settings.operation_timeout,
    )
