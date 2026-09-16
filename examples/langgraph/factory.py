"""Persistent LangGraph store factory, loaded only by explicit opt-in."""
from __future__ import annotations

import os

from memorycheck.adapters import LangGraphAdapter


def make_adapter() -> LangGraphAdapter:
    dsn = os.environ.get("MEMORYCHECK_POSTGRES_DSN")
    if not dsn:
        raise ValueError("Set MEMORYCHECK_POSTGRES_DSN to a dedicated PostgreSQL test database")
    return LangGraphAdapter.from_postgres(dsn, setup=True)
