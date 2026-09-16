import os

import pytest

from examples.mem0.factory import make_adapter


@pytest.fixture
def memory_adapter():
    pytest.importorskip("mem0", reason="Install the optional Mem0 dependency")
    if not os.environ.get("MEMORYCHECK_MEM0_CONFIG"):
        pytest.skip("Set MEMORYCHECK_MEM0_CONFIG to a dedicated test backend configuration")
    with make_adapter() as adapter:
        yield adapter
