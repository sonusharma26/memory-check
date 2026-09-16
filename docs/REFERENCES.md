# Integration references

The implementation was grounded in the supplied source archive and two user-provided project/release briefs. External lookup was limited to primary API details needed for the pytest reporting hooks and scoped Mem0 cleanup; it was not a broad product research exercise.

Primary references checked on 2026-09-16:

- Pytest hook reference, including `pytest_runtest_makereport` and `pytest_report_teststatus`: https://docs.pytest.org/en/stable/reference/reference.html
- Pytest plugin packaging/entry-point guide: https://docs.pytest.org/en/stable/how-to/writing_plugins.html
- Mem0 OSS implementation, including scoped `delete_all(user_id, agent_id, run_id)`: https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py

Documentation/source lookup is not equivalent to an installed SDK or live backend integration test. This handoff used API-shaped fakes for Mem0 and LangGraph and makes that limitation explicit in the compatibility matrix.
