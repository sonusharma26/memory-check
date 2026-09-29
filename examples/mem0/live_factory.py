"""Mem0 adapter for disposable local services or a dedicated cloud test collection.

The caller supplies the Qdrant endpoint through environment variables. No
provider credential is written to a configuration file or trace.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from memorycheck.adapters import Mem0Adapter


def _close_memory(memory) -> None:
    client = getattr(getattr(memory, "vector_store", None), "client", None)
    close = getattr(client, "close", None)
    if callable(close):
        close()


def make_adapter() -> Mem0Adapter:
    url = os.environ.get("MEMORYCHECK_QDRANT_URL", "")
    key = os.environ.get("MEMORYCHECK_QDRANT_API_KEY", "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Set MEMORYCHECK_QDRANT_URL to a dedicated Qdrant endpoint")
    if parsed.hostname not in {"localhost", "127.0.0.1"} and not key:
        raise ValueError("Set MEMORYCHECK_QDRANT_API_KEY for a remote Qdrant endpoint")

    history = Path(os.environ.get("RUNNER_TEMP", ".memorycheck")) / "mem0-history.db"
    history.parent.mkdir(parents=True, exist_ok=True)
    vector_config = {
        "url": url,
        "collection_name": "memorycheck_lifecycle",
        "embedding_model_dims": 384,
    }
    if key:
        vector_config["api_key"] = key

    config = {
        "vector_store": {"provider": "qdrant", "config": vector_config},
        "embedder": {
            "provider": "fastembed",
            "config": {"model": "BAAI/bge-small-en-v1.5", "embedding_dims": 384},
        },
        # Every MemoryCheck write uses infer=False. The LLM is constructed by
        # Mem0 but receives no requests during these lifecycle contracts.
        "llm": {"provider": "openai", "config": {"api_key": "unused-for-lifecycle-tests"}},
        "history_db_path": str(history),
    }
    return Mem0Adapter.from_config(config, close=_close_memory)
