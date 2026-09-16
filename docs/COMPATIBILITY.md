# Compatibility and validation evidence

This matrix distinguishes implemented interfaces from executed integration evidence. Dependency ranges are resolver bounds, not a claim that every version in a range was tested against a live service. No Mem0 or LangGraph provider SDK was installed or contacted for this handoff.

| Backend | Integration target | API oracle | Storage oracle | Restart | Isolation | Evidence in this handoff |
| --- | --- | --- | --- | --- | --- | --- |
| Bundled reference | JSON active snapshot, schema 1 | Deterministic query | Independent file read | Re-reads same file | User, tenant, session | Ten built-in contracts plus focused regression checks executed locally. |
| Four broken references | Deliberate delete/restart/update/isolation faults | Fault-specific | Independent file read | Where declared | Fault-specific | Each expected lifecycle divergence detected by core regression tests. |
| Mem0 OSS | Synchronous add/search/update/delete; configured factory | Normalized public retrieval | Caller-supplied inspector only | Fresh factory runtime, same target | User/session | Legacy and filters-style API-shaped fakes only; **live SDK/service not tested**. |
| LangGraph | Synchronous persistent BaseStore; optional PostgreSQL store | Lexical scan by default, optional semantic query | Caller-supplied inspector only | Fresh owned connection, same persistent target | User/tenant/session | API-shaped store fake with pagination and reopen behavior; **live SDK/PostgreSQL not tested**. |

Mem0 scoped cleanup is conditional on synchronous `delete_all`. LangGraph/reference support generated-scope cleanup. No row promises deletion of provider histories, backups, logs, or uninspectable storage. Borrowed runtimes have no restart capability; absence of an inspector is NOT AVAILABLE, not a storage pass.

## Python and pytest

The declared Python range is **3.10–3.13**, matching the source CI matrix. Linux and Windows runners are configured. The local executed environment was **Python 3.13.5, pytest 9.0.2, PyYAML 6.0.3, jsonschema 4.26.0 on Linux**. Other matrix jobs were not executed in this session; CI remains a release gate.

Runtime dependency bounds are pytest `>=7.4,<10`, PyYAML `>=6,<7`, and tomli `>=2,<3` only on Python 3.10. Optional bounds are in `pyproject.toml`; they deliberately avoid implying validated per-version provider support. The CI includes a separate minimum-core-dependency job, also unexecuted here.

Python 3.14+, async SDKs, full agent/checkpointer stacks, native hard process cancellation, and arbitrary providers are not claimed support in 0.1.0. Context/behavior oracles are extension concepts, not shipped judge integrations.

Before publishing, run the source matrix, installed wheel/sdist checks, and the same nontrivial contracts against the exact Mem0 and LangGraph/PostgreSQL versions you intend to claim. Record those versions and results in this matrix rather than replacing “not tested” with an unsupported check mark.
