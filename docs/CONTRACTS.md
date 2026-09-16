# Lifecycle contract reference

Every user-authored YAML contract starts with the quoted `schema_version: "0.1"`, an ASCII identifier `name`, and a nonempty `steps` list containing at least one `expect`. `description`, declared `requires`, and a default `scope` are optional. The parser computes effective requirements from both declarations and operations, so omitting `requires` cannot weaken a test.

```yaml
schema_version: "0.1"
name: correction_example
scope: {user_id: alice}
steps:
  - create: {id: region, value: "{{canary:OLD_REGION}}"}
  - expect: {query: MEMORYCHECK, contains: "{{canary:OLD_REGION}}"}
  - update: {id: region, value: "{{canary:NEW_REGION}}"}
  - restart: {}
  - expect:
      query: MEMORYCHECK
      contains: "{{canary:NEW_REGION}}"
      excludes: "{{canary:OLD_REGION}}"
      oracles: [api]
```

`create` requires a unique logical `id` and nonempty `value`. `update` requires the ID of a live created reference and its replacement `value`. `delete` requires a live created reference. Mutation scope cannot change from the scope in which the reference was created. Reusing a deleted logical ID for mutation is invalid in this schema; create a new logical ID instead.

`restart: {}` has no arguments. `query` captures an API observation without adding an invariant, and accepts `query` plus scope. `expect` captures observations once and checks `contains`, `excludes`, and/or `empty`. Repeated fluent assertion chaining also evaluates the same observation, never silently re-queries.

`contains` and `excludes` accept a string or list of nonempty strings. Matching is case-sensitive `substring` by default; use `match: exact` to compare complete returned values. A value cannot appear in both lists. `empty: true` contradicts a positive contains requirement. Storage absence/emptiness is never established by an incomplete scan.

`oracles` defaults to `[api]`. An explicit `[api, storage]` requires storage inspection before any write can start. `context` and `behavior` are extension names with no bundled implementations; declaring a capability alone does not supply an oracle. Available non-required oracles may be observed for diagnosis but are labeled not asserted, not PASS. An error in a required oracle is ERROR. A timeout in any attempted observation stops the run and quarantines the adapter.

## Scopes and canaries

Standard scope keys are `user_id`, `tenant_id`, and `session_id`, either within `scope` or directly on an operation, but not both. Additional string keys can be represented through `Scope.extra` and require matching `scope:<name>` capabilities. Nulls, empty strings, duplicate keys, and cross-scope mutations are rejected.

A session translates logical scopes into unique generated native boundaries. The user does not need to manually interpolate a run ID. Logical references retain their original logical scope; adapters receive the generated native scope. Neither query nor storage results are locally filtered to hide leaks.

`{{canary:LABEL}}` is the only expansion syntax. Labels use 1–48 ASCII letters/digits/underscores, starting with a letter. Each label is stable inside a contract run and unique across runs. Literal braces for other templates are not evaluated; unsupported template syntax is rejected. There is no environment, Python, Jinja, file, or network interpolation.

Use a canary in both creation and the precondition assertion before testing deletion/correction. The built-in deletion contracts include an unrelated live control so a backend that accidentally deletes the whole test scope cannot look correct.

## Validation and collection

```bash
memorycheck validate contracts/
memorycheck show delete_survives_restart --yaml
pytest examples/reference/correction.memorycheck.yaml
```

The complete selected CLI corpus is validated before the first adapter is constructed. Errors include the source, line/column when available, a structural excerpt with payload-bearing fields redacted, and typo suggestions. Python-created `Contract` objects are also revalidated before execution.

The parser rejects unsafe YAML tags, aliases, duplicate keys, unknown fields/operations/capabilities, incorrect scalar types, invalid reference order, and operation-only contracts. Limits are 1 MiB of UTF-8 input, 10,000 operations, 100,000 YAML nodes, and nesting depth 60. It does not provide a sandbox for arbitrary caller-supplied Python objects.

The editor schema ships as `memorycheck/contracts/contract.schema.json`. It describes syntax; cross-operation reference lifetimes, capability inference, and logical contradictions are enforced by the parser.

Pytest conventionally collects `*.memorycheck.yaml`/`*.memorycheck.yml`; other YAML requires explicit selection or `--memorycheck-collect-yaml`. `--memorycheck` adds the ten bundled contracts. Built-ins and YAML receive `memorycheck`, `memorycheck_destructive`, and, where needed, `memorycheck_restart` markers. Fluent tests receive the first two automatically; add a restart marker explicitly when appropriate.
