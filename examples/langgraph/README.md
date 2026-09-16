# LangGraph persistent-store lifecycle example

The adapter targets the synchronous persistent store, not an agent graph or checkpoint saver. Provision a dedicated PostgreSQL test database yourself.

```bash
python -m pip install -e ".[postgres]"
export MEMORYCHECK_POSTGRES_DSN='postgresql://test_user:test_password@localhost:5432/memorycheck_test'
pytest examples/langgraph
```

Credentials above are placeholders. Do not use a production database. The factory calls store setup and opens a fresh persistent-store connection on restart; it does not restart the database server. Generated native scopes and a stable-per-adapter namespace separate tests. Successful cleanup removes those generated scopes only after observations finish. Cleanup failures remain errors with private local ledgers; timed-out writes require backend-side verification.

```bash
memorycheck doctor --adapter langgraph --probe
memorycheck run builtin:delete_survives_restart --adapter langgraph
```

For a project-specific runtime/inspector, use `--adapter examples.langgraph.factory:make_adapter` from this source checkout, or provide your own factory module. `adapter_options.connection_env` changes the environment variable used by the named adapter.

Default lexical retrieval paginates the real store search API and matches canaries without an embedder. To test semantic retrieval, configure the index and select `query_mode="semantic"`. No model behavior oracle is implied. Public store search is not relabeled as physical storage inspection; storage-required contracts skip unless an independent read-only inspector is supplied.

The fixture skips only without its dependency or environment variable. A configured connection/migration failure is an error, not a skip. This example was not run against live PostgreSQL or an installed LangGraph SDK for the source handoff. Record exact live versions/results in [the compatibility matrix](../../docs/COMPATIBILITY.md) before claiming them.
