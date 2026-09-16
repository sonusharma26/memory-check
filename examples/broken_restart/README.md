# Deliberate restart defect

Run `pytest examples/broken_restart` after local installation. The test is
**intentionally not marked xfail**: the product demo should show a real pytest
failure, readable oracle diagnostics, and a saved JSON trace.

`BrokenRestartMemory` deletes from its runtime representation but not the active
persistent snapshot. Its API initially hides the target. Restart reloads the
old snapshot and resurrects it. An unrelated control remains visible throughout.

This is a synthetic fault-injection example, not an attributed vendor incident.
It is not included in the default `pytest` self-test paths.
