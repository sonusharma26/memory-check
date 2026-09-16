# Mem0 OSS lifecycle example

Use a dedicated test configuration, never a production collection. This example writes synthetic records, not real personal data. It does not provision infrastructure or credentials and never issues a collection-wide reset.

```bash
python -m pip install -e ".[mem0]"
cp examples/mem0/config.example.json mem0.local.json
export MEMORYCHECK_MEM0_CONFIG=mem0.local.json
pytest examples/mem0
```

Edit `mem0.local.json` for your running persistent backend and installed SDK's embedding/LLM configuration. The sample names a local Qdrant service and an on-disk history path. It is not an offline model configuration. The example factory creates the history directory; for the named adapter, create the configured directory yourself before probing. MemoryCheck calls `add(..., infer=False)` and does not use an LLM judge; the provider may still need models/embeddings.

```bash
memorycheck doctor --adapter mem0 --probe
memorycheck run builtin:delete_survives_restart --adapter mem0
```

The fixture skips when the dependency or configuration variable is absent. Configured connection failures are errors. For customized runtime ownership or a storage inspector, use `--adapter examples.mem0.factory:make_adapter` from this source checkout or supply your own factory.

Restart constructs a fresh SDK runtime from the same persistent configuration and retains its native namespace. Embedded handles may need an explicit close callback/context-manager factory before reconstruction; consult [adapter documentation](../../docs/ADAPTERS.md).

The lifecycle preserves a control record while testing deletion, then cleanup removes generated test scopes where synchronous `delete_all` is available. Otherwise it attempts known live IDs only, which is not a guarantee that histories or hidden artifacts were erased. Review cleanup status and use dedicated disposable storage. Timed-out operations are quarantined, not automatically cleaned up.

No independent storage inspector is configured by default. Storage-required contracts skip until a read-only inspector with honest coverage is supplied. Live Mem0 was not installed or exercised for this source handoff; API-shaped fakes are not provider certification. Record exact live versions/results in [the compatibility matrix](../../docs/COMPATIBILITY.md).
