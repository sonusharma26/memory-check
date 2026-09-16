"""Maintainer-only installed-artifact smoke check; not run for the source handoff.

Installing an sdist necessarily builds it. This script is used ONLY by the
explicitly dispatched release workflow, never by source CI or ordinary tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def run(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=180)


def main() -> None:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    artifacts = sorted(source.glob("*.whl")) + sorted(source.glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise SystemExit("Expected exactly one wheel and one sdist")
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="memorycheck-distribution-") as directory:
            root = Path(directory)
            venv.create(root / "venv", with_pip=True)
            python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            working = root / "consumer"
            working.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"}}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            run([str(python), "-m", "pip", "install", str(artifact)], cwd=working, env=env)
            run([str(python), "-c", (
                "from importlib import resources; from memorycheck import builtin_names; "
                "assert len(builtin_names()) == 10; "
                "assert resources.files('memorycheck').joinpath('py.typed').is_file(); "
                "assert resources.files('memorycheck.contracts').joinpath('contract.schema.json').is_file(); "
                "assert resources.files('memorycheck.trace').joinpath('trace.schema.json').is_file()"
            )], cwd=working, env=env)
            command = python.parent / ("memorycheck.exe" if os.name == "nt" else "memorycheck")
            run([str(command), "--version"], cwd=working, env=env)
            run([str(command), "doctor"], cwd=working, env=env)
            run([str(command), "run", "builtin"], cwd=working, env=env)
            run([str(python), "-m", "pytest", "--memorycheck", "-q"], cwd=working, env=env)


if __name__ == "__main__":
    main()
