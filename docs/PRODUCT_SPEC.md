<!-- Original user product brief, preserved for provenance. For the implemented release API see README.md and docs/API.md. -->

# MemoryCheck

**MemoryCheck is a pytest-compatible conformance testing toolkit for persistent AI memory systems.**

It tests whether declared memory lifecycle invariants remain true across operations such as creation, correction, deletion, restart, and user isolation.

It is **not** a memory framework, benchmark leaderboard, observability platform, or compliance product.

The core question MemoryCheck answers is:

> After a sequence of memory operations, does every observable layer of the memory system agree with the state the application expects?

---

# 1. Core abstraction

The fundamental unit is a **lifecycle contract**.

A contract consists of:

```text
operations
    ↓
observations
    ↓
invariants

```

Example:

```yaml
name: correction_survives_restart

requires:
  - create
  - update
  - query
  - restart

steps:
  - create:
      id: database
      value: postgres

  - expect:
      query: database
      contains: postgres

  - update:
      id: database
      value: mysql

  - expect:
      query: database
      contains: mysql
      excludes: postgres

  - restart: {}

  - expect:
      query: database
      contains: mysql
      excludes: postgres

```

MemoryCheck executes the sequence, records what happened, evaluates the assertions, and produces a diagnostic trace if an invariant fails.

---

# 2. The four components

## Contract runner

This is the engine.

It executes lifecycle operations in order and records every transition.

Internally, each test becomes a trace similar to:

```text
CREATE fact_1
QUERY database
UPDATE fact_1
QUERY database
RESTART
QUERY database

```

The runner itself should not understand Mem0, LangGraph, Letta, or any other implementation.

It only understands MemoryCheck operations.

---

## Adapter

An adapter translates MemoryCheck operations into framework-specific operations.

Do not create a giant `UniversalMemory` abstraction.

Instead, adapters expose capabilities.

Conceptually:

```python
class MemoryAdapter:

    capabilities = {
        "create",
        "query",
        "delete",
        "update",
        "restart",
    }

    def create(self, value, **scope):
        ...

    def query(self, query, **scope):
        ...

    def update(self, memory_id, value, **scope):
        ...

    def delete(self, memory_id, **scope):
        ...

    def restart(self):
        ...

```

Optional capabilities can include:

```text
inspect_storage
inspect_context
clear_all
consolidate
summarize
tenant_scope
user_scope
session_scope

```

A contract declares what capabilities it requires.

If an adapter cannot support them, MemoryCheck skips that contract rather than pretending the semantics are equivalent.

---

## Oracle

An oracle observes a particular layer of the system.

MemoryCheck v0.1 should support four oracle concepts, although only the first two need to ship initially.

```text
API oracle
What does the public memory/retrieval API return?

Storage oracle
What physically remains in inspectable persistent storage?

Context oracle
What memory entered the agent/model context?

Behavior oracle
Does the resulting agent behave as though it knows the memory?

```

The first two should be deterministic.

Context and behavior should be optional extensions.

A failure might therefore look like:

```text
DELETE fact_123

API        absent      PASS
Storage    present     FAIL
Context    unavailable
Behavior   unavailable

```

Or:

```text
API        absent      PASS
Storage    absent      PASS
Context    present     FAIL
Behavior   recalled    FAIL

```

That distinction is one of MemoryCheck's core values.

---

## Reporter

Every operation produces an event.

Example internal representation:

```json
{
  "timestamp": 4.31,
  "operation": "query",
  "subject": "deployment_region",
  "expected": {
    "contains": ["eu-west"],
    "excludes": ["us-west"]
  },
  "observations": {
    "api": ["eu-west"],
    "storage": ["eu-west", "us-west"]
  }
}

```

The terminal reporter converts those events into something developers can immediately act on.

Example:

```text
FAILED correction_survives_restart

01 CREATE   postgres                  ✓
02 QUERY    postgres                  ✓
03 UPDATE   mysql                     ✓
04 QUERY    mysql                     ✓
05 RESTART                            ✓
06 QUERY    mysql + postgres          ✗

Invariant violated:
  postgres must not be observable after correction

Oracle results:
  API       postgres present          FAIL
  Storage   postgres present          FAIL

First divergence:
  after restart

Suspected surviving state:
  persisted summary

Minimal reproduction:
  create(postgres)
  update(mysql)
  restart()
  query(database)

```

That report is the actual product experience.

---

# 3. Exactly what v0.1 should test

MemoryCheck v0.1 should focus on four lifecycle properties.

## Correction

Old state must stop affecting retrieval after a correction.

```text
remember A
correct A → B
query

expected:
B present
A absent

```

Then repeat after restart.

---

## Deletion

Deleted state must remain absent.

```text
remember A
delete A
query
restart
query

```

This should test both API visibility and inspectable storage.

---

## Restart durability

Expected state must survive runtime reconstruction.

```text
remember A
restart
query A

```

And importantly:

```text
remember A
delete A
restart
query A

```

The second case is more interesting.

---

## Isolation

Memory must remain inside its declared scope.

Example:

```text
user alice:
  remember ORION

user bob:
  query project_codename

assert:
  ORION absent

```

Support at least `user_id`.

Design the scope representation so tenant/session namespaces can be added later.

---

# 4. The initial built-in contract corpus

Ship approximately 10 high-quality contracts.

The initial suite should include:

```text
remember_recall
remember_restart_recall

update_replaces_old_value
update_survives_restart
update_does_not_resurface_old_value

delete_removes_value
delete_survives_restart
delete_removes_storage_artifacts

user_isolation
user_isolation_after_restart

```

Add a few real historical regression reproductions once the infrastructure works.

Do not ship 100 generic tests just to make the repository look substantial.

---

# 5. Pytest should be the primary interface

MemoryCheck should feel like pytest infrastructure.

A user should be able to write:

```python
def test_deleted_fact_does_not_return(memorycheck):
    fact = memorycheck.create(
        "MEMORYCHECK_CANARY_8317",
        user_id="alice",
    )

    memorycheck.expect(
        query="canary",
        user_id="alice",
    ).contains("MEMORYCHECK_CANARY_8317")

    memorycheck.delete(fact)

    memorycheck.restart()

    memorycheck.expect(
        query="canary",
        user_id="alice",
    ).excludes("MEMORYCHECK_CANARY_8317")

```

And run:

```bash
pytest

```

There can also be a CLI:

```bash
memorycheck run contracts/delete_restart.yaml

```

But pytest is the integration surface developers should encounter first.

---

# 6. Deterministic canaries

MemoryCheck should strongly encourage exact test values.

Instead of:

```text
I prefer Python

```

use something like:

```text
MEMORYCHECK_REGION_8E31 = us-west

```

or:

```text
MEMORYCHECK_SECRET_91AF2

```

This avoids making deterministic lifecycle tests depend on semantic interpretation by an LLM.

The core test suite should not require LLM-as-a-judge.

---

# 7. Reference implementations

Build a tiny reference memory backend inside the repository.

It should have both correct and deliberately broken implementations.

For example:

```text
ReferenceMemory
BrokenDeleteMemory
BrokenRestartMemory
BrokenIsolationMemory
BrokenUpdateMemory

```

`BrokenRestartMemory` might keep deleted records in its persisted representation.

That gives you deterministic integration tests for MemoryCheck itself.

You can then demonstrate:

```bash
$ pytest examples/broken_restart

FAILED

Deleted memory resurfaced after restart.

```

before depending on external frameworks.

---

# 8. First real adapters

Ship two.

### Mem0

Use its normal memory operations as the API oracle.

Where feasible, provide an optional storage inspector.

### LangGraph

Target its persistent store rather than trying to test every LangGraph agent architecture.

The important reason to choose two adapters is architectural validation.

If the same deletion/restart contract works against both without filling the core with framework-specific conditionals, MemoryCheck's abstraction is probably sound.

Do not add Letta, Mastra, Cognee, Redis, Postgres, etc. until these two work cleanly.

---

# 9. Trace format

Define a provider-neutral event schema from day one.

Something roughly like:

```json
{
  "schema_version": "0.1",
  "contract": "delete_survives_restart",
  "events": [
    {
      "sequence": 1,
      "operation": "create",
      "subject": "fact_123"
    },
    {
      "sequence": 2,
      "operation": "delete",
      "subject": "fact_123"
    },
    {
      "sequence": 3,
      "operation": "restart"
    },
    {
      "sequence": 4,
      "operation": "query",
      "observations": {
        "api": {
          "status": "absent"
        },
        "storage": {
          "status": "present"
        }
      }
    }
  ]
}

```

Store this JSON as a test artifact whenever a contract fails.

The terminal report is simply one renderer of this trace.

Later an HTML timeline, CI integration, or external debugger can consume exactly the same artifact.

This format may eventually become more valuable than the runner itself.

---

# 10. Repository structure

```text
memorycheck/
├── memorycheck/
│   ├── contracts/
│   │   ├── model.py
│   │   ├── parser.py
│   │   └── builtin/
│   │
│   ├── runner/
│   │   ├── executor.py
│   │   └── result.py
│   │
│   ├── adapters/
│   │   ├── base.py
│   │   ├── reference.py
│   │   ├── mem0.py
│   │   └── langgraph.py
│   │
│   ├── oracles/
│   │   ├── api.py
│   │   └── storage.py
│   │
│   ├── trace/
│   │   ├── schema.py
│   │   └── serializer.py
│   │
│   ├── reporting/
│   │   └── terminal.py
│   │
│   ├── pytest_plugin.py
│   └── cli.py
│
├── contracts/
│   ├── correction/
│   ├── deletion/
│   ├── restart/
│   └── isolation/
│
├── examples/
│   ├── reference/
│   ├── mem0/
│   └── langgraph/
│
└── tests/

```

---

# 11. Do not build these yet

Do not build a dashboard, hosted service, universal memory SDK, benchmark leaderboard, automated GDPR checker, large adapter marketplace, complex graph visualization, LLM judge framework, or your own property-testing engine.

Those all distract from validating the fundamental abstraction.

---

# 12. Definition of v0.1 success

MemoryCheck v0.1 is complete when all of these are true:

1. A developer can install it with `pip install memorycheck`.
2. It works naturally inside pytest.
3. Contracts can also be represented as YAML.
4. Adapters advertise capabilities.
5. The reference backend demonstrates correction, deletion, restart and isolation failures.
6. At least Mem0 and LangGraph adapters exist.
7. The same nontrivial lifecycle contract runs against both.
8. API and storage observations are reported separately where available.
9. Failed tests produce a persisted machine-readable trace.
10. Terminal output identifies the first lifecycle point where expected and observed state diverged.

The most important acceptance test is:

```text
Given two independent AI-memory systems,

MemoryCheck can execute the same
delete → restart → retrieve contract

and explain a failure without the contract
containing framework-specific logic.

```

If you achieve that, MemoryCheck has proven the central architecture.

---

# 13. v0.2

After v0.1 works, add stateful property testing using Hypothesis.

The system should generate sequences such as:

```text
create A
create B
update A
delete B
restart
create C
restart
query A
query B

```

When an invariant fails, Hypothesis shrinks the sequence.

MemoryCheck turns the result into:

```text
Original failure:
31 operations

Minimal reproduction:
1. create A
2. update A → B
3. restart
4. query

Expected:
B

Observed:
A

```

That is likely to become the feature that makes MemoryCheck distinctly more powerful than a collection of integration tests.

---

# Product sentence

**MemoryCheck is a pytest-compatible lifecycle conformance testing toolkit that detects stale, resurrected, leaked, and inconsistent state in persistent AI memory systems.**

And the README headline should probably be:

> **Your memory API said it was deleted. MemoryCheck found it after restart.**