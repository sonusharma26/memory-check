"""Allowlisted diagnostic metadata; no environment dump and no SDK imports."""
from __future__ import annotations

import platform
from importlib import metadata
from typing import Any

from memorycheck._version import __version__


def distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def environment(adapter: Any = None) -> dict[str, str | None]:
    provider = getattr(adapter, "provider_distribution", None)
    return {
        "memorycheck_version": __version__,
        "adapter_version": getattr(adapter, "adapter_version", __version__),
        "provider_version": distribution_version(provider) if isinstance(provider, str) else None,
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
        "pytest_version": distribution_version("pytest"),
    }
