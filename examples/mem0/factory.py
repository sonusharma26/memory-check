"""Explicitly opt in to a dedicated Mem0 configuration; never run on import."""
from __future__ import annotations

import json
import os
from pathlib import Path

from memorycheck.adapters import Mem0Adapter


def make_adapter() -> Mem0Adapter:
    path = os.environ.get("MEMORYCHECK_MEM0_CONFIG")
    if not path:
        raise ValueError("Set MEMORYCHECK_MEM0_CONFIG to your dedicated test configuration JSON")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    history = config.get("history_db_path")
    if isinstance(history, str) and history != ":memory:":
        Path(history).parent.mkdir(parents=True, exist_ok=True)
    # The sample uses a remote vector-store client, not an embedded locked file.
    # Supply a close callback/context-manager factory for configuration-specific
    # resource cleanup when integrating an embedded backend into your application.
    return Mem0Adapter.from_config(config)
