"""The exact same nontrivial contract used by the LangGraph example."""


def test_delete_restart_contract(memorycheck):
    memorycheck.check("builtin:delete_survives_restart")
