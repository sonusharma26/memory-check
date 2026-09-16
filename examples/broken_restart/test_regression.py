"""Deliberately fails; excluded from the default self-test paths."""


def test_deleted_canary_resurfaces(memorycheck):
    memorycheck.check("builtin:delete_survives_restart")
