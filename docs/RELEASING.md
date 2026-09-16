# Maintainer release checklist

This source candidate implements the requested productization work and has a focused local regression result. It is **not** a statement that an unbuilt wheel, every supported Python runtime, or live external backends have already passed. The handoff deliberately did not run builds or publish anything.

## Before a public 0.1.0 release

1. Confirm ownership/availability of the `memorycheck` distribution name and configure the actual repository's private security contact, protected release environment, and trusted publisher. No ownership or PyPI publication is implied by this archive.
2. Run `.github/workflows/ci.yml` across Python 3.10–3.13 on Linux/Windows and the lower-core-dependency job. Record outcomes in `VALIDATION.md`; merely having workflow YAML is not evidence it ran.
3. In dedicated test environments, run `doctor --probe` and the same deletion/restart, correction, and isolation contracts against exact Mem0 and LangGraph/PostgreSQL versions. Record credentials-free version/coverage information in `COMPATIBILITY.md`. Storage coverage remains optional and explicitly documented.
4. When authorized to build, run the manually dispatched release workflow on the matching version tag. It builds wheel/sdist, checks distribution metadata, inspects package resources, installs each artifact in clean environments, and smoke-tests CLI/pytest entry points outside the source tree. Review dependency/license/security checks for the actual resolved environment too.
5. Review sanitized artifacts, confirm release notes/version agreement, and publish first to TestPyPI where appropriate. Require protected-environment approval for PyPI. Verify installed behavior after publication.

## Workflow design

The source workflow installs only external development dependencies from binary distributions and runs the focused source suite. The reusable workflow runs again as a release prerequisite. It does not build MemoryCheck.

The release workflow is manual, requires a tag and explicit version confirmation, and separates build validation from publishing. It is included for the maintainer's future use and was not dispatched for this handoff. Configure the `testpypi` and `pypi` GitHub environments and trusted publishers before attempting it. Artifacts are uploaded separately from credential-free failure traces; private cleanup ledgers are never uploaded.

## Known boundaries to retain in release notes

Synchronous adapters only; typed query observations; Python 3.10–3.13; optional independent storage inspection; no hidden-state erasure claim; caller-side rather than transactional cancellation; blocked cleanup after timeout; source/fake validation distinguished from exact live-provider compatibility. The redacted verbose operation outline is not an executable or minimized counterexample.

The original user requested minimal tests and no builds. This delivery preserves those constraints: ten focused test functions and no executed build. Sol subagents were requested, but no Sol/subagent tool or connector was available after discovery; the modifications and checks were performed directly, not falsely attributed to delegated agents.
