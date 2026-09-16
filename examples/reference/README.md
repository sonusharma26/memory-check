# Reference backend

After installing this checkout, run `pytest examples/reference` to exercise one fluent Python test and one automatically collected YAML contract. No model, network, or external database is required. Each test receives its own JSON snapshot and generated test scope; cleanup happens after observations.

Run the complete ten-contract corpus with `pytest --memorycheck -q` or `memorycheck run builtin`. To use an entirely source-only environment with dependencies already installed, run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p memorycheck.pytest_plugin examples/reference -q` from this repository. Explicit plugin loading replaces installed entry-point discovery in that command.

Use `memorycheck doctor --probe` to diagnose setup and exercise the typed adapter boundary. Use `--save-all` on the CLI, or `--memorycheck-save-all` in pytest, to retain successful redacted traces as well as failures.
