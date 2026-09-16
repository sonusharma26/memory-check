"""Run with: pytest examples/reference (after local installation)."""
import pytest


@pytest.mark.memorycheck
@pytest.mark.memorycheck_restart
@pytest.mark.memorycheck_destructive
def test_deleted_fact_does_not_return(memorycheck):
    value = memorycheck.canary("REGION")
    fact = memorycheck.create(value, user_id="alice")
    memorycheck.expect(query="MEMORYCHECK_REGION", user_id="alice").contains(value)
    memorycheck.delete(fact)
    memorycheck.restart()
    memorycheck.expect(query="MEMORYCHECK_REGION", user_id="alice").excludes(value)
