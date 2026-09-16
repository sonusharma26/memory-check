import pytest

from memorycheck.adapters import BrokenRestartMemory


@pytest.fixture
def memory_adapter(tmp_path):
    with BrokenRestartMemory(tmp_path / "broken-memory.json") as adapter:
        yield adapter
