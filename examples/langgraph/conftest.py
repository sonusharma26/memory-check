import os

import pytest

from examples.langgraph.factory import make_adapter


@pytest.fixture
def memory_adapter():
    pytest.importorskip("langgraph.store.postgres", reason="Install the optional PostgreSQL store dependency")
    if not os.environ.get("MEMORYCHECK_POSTGRES_DSN"):
        pytest.skip("Set MEMORYCHECK_POSTGRES_DSN to a dedicated test database")
    with make_adapter() as adapter:
        yield adapter
