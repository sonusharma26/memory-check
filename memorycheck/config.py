"""Small, strict configuration surface. Never imports a backend while parsing."""
from __future__ import annotations

import math
from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from memorycheck.errors import AdapterConfigurationError


@dataclass(frozen=True)
class Settings:
    adapter: str = "reference"
    adapter_options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    trace_dir: str = ".memorycheck/traces"
    trace_values: str = "redacted"
    save_all: bool = False
    operation_timeout: float = 10.0
    restart_timeout: float = 15.0
    contract_timeout: float = 60.0
    cleanup_timeout: float = 10.0
    timeout_mode: str = "auto"
    isolate: bool = True
    cleanup: bool = True
    run_dir: str = ".memorycheck/runs"
    project_root: Path = field(default_factory=Path.cwd, repr=False)

    def __post_init__(self) -> None:
        for name in ("adapter", "trace_dir", "run_dir"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise AdapterConfigurationError(f"{name} must be nonempty text")
        if self.trace_values not in {"redacted", "synthetic"}:
            raise AdapterConfigurationError("trace_values must be 'redacted' or 'synthetic'; raw payload export is not supported")
        if self.timeout_mode not in {"auto", "signal", "thread"}:
            raise AdapterConfigurationError("timeout_mode must be auto, signal, or thread")
        for name in ("operation_timeout", "restart_timeout", "contract_timeout", "cleanup_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise AdapterConfigurationError(f"{name} must be a finite positive number of seconds")
        for name in ("save_all", "isolate", "cleanup"):
            if not isinstance(getattr(self, name), bool):
                raise AdapterConfigurationError(f"{name} must be a boolean")
        if not isinstance(self.adapter_options, Mapping) or any(not isinstance(k, str) for k in self.adapter_options):
            raise AdapterConfigurationError("adapter_options must be a table with string keys")
        object.__setattr__(self, "adapter_options", MappingProxyType(deepcopy(dict(self.adapter_options))))
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())

    def updated(self, **values: Any) -> Settings:
        unknown = set(values) - {f.name for f in fields(self)}
        if unknown:
            raise AdapterConfigurationError("Unknown MemoryCheck setting(s): " + ", ".join(sorted(unknown)))
        return replace(self, **{k: v for k, v in values.items() if v is not None})

    def path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.project_root / path


_SETTINGS: ContextVar[Settings | None] = ContextVar("memorycheck_settings", default=None)


def get_settings() -> Settings:
    return _SETTINGS.get() or Settings()


def configure(**values: Any) -> Settings:
    """Set defaults for subsequent sessions in the current context; return them."""
    settings = get_settings().updated(**values)
    _SETTINGS.set(settings)
    return settings


def read_settings(path: str | Path | None = None, *, start: str | Path | None = None,
                  overrides: Mapping[str, Any] | None = None) -> Settings:
    root = Path(start or Path.cwd()).resolve()
    selected = Path(path).resolve() if path is not None else None
    if selected is None:
        for parent in (root, *root.parents):
            candidate = parent / "pyproject.toml"
            if candidate.is_file():
                selected = candidate
                break
    settings = get_settings().updated(project_root=root)
    if selected is not None:
        try:
            if selected.stat().st_size > 1_048_576:
                raise AdapterConfigurationError("pyproject.toml exceeds the 1 MiB configuration limit")
            with selected.open("rb") as stream:
                data = tomllib.load(stream)
            table = data.get("tool", {}).get("memorycheck", {})
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            raise AdapterConfigurationError(f"Cannot read MemoryCheck configuration ({type(exc).__name__}); check the TOML file") from None
        if not isinstance(table, Mapping):
            raise AdapterConfigurationError("[tool.memorycheck] must be a TOML table")
        if "project_root" in table:
            raise AdapterConfigurationError("project_root is derived from the configuration file, not a TOML setting")
        settings = settings.updated(**dict(table), project_root=selected.parent)
    return settings.updated(**dict(overrides or {}))
