"""Human-readable rendering of the trace; never guesses an unobserved cause."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from memorycheck.trace.schema import Trace


def _safe(value: Any, limit: int | None = None) -> str:
    text = "".join(
        f"\\u{ord(char):04x}" if ord(char) < 32 or 127 <= ord(char) <= 159
        or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" else char
        for char in str(value)
    )
    return text if limit is None or len(text) <= limit else text[:limit - 3] + "..."


def reproduction(events: list[dict[str, Any]]) -> list[str]:
    lines = []
    refs: dict[str, str] = {}
    for event in events:
        op, args = event["operation"], event.get("arguments", {})
        scope = event.get("scope", {})
        scoped = f", scope={scope!r}" if scope else ""
        if op == "create":
            var = f"m{len(refs) + 1}"
            refs[args.get("memory_id", "?")] = var
            alias = f", id={args['alias']!r}" if args.get("alias") is not None else ""
            lines.append(f"{var} = memorycheck.create({args.get('value')!r}{alias}{scoped})")
        elif op == "update":
            ref = refs.get(args.get("memory_id", "?"), repr(args.get("memory_id")))
            lines.append(f"memorycheck.update({ref}, {args.get('value')!r})")
        elif op == "delete":
            ref = refs.get(args.get("memory_id", "?"), repr(args.get("memory_id")))
            lines.append(f"memorycheck.delete({ref})")
        elif op == "restart":
            lines.append("memorycheck.restart()")
        elif op == "query":
            expected = event.get("expected", {})
            options = scoped
            if expected.get("oracles", ["api"]) != ["api"]:
                options += f", oracles={expected['oracles']!r}"
            if expected.get("match", "substring") != "substring":
                options += f", match={expected['match']!r}"
            line = f"memorycheck.expect(query={args.get('query', event.get('subject'))!r}{options})"
            for kind in ("contains", "excludes"):
                if expected.get(kind):
                    line += f".{kind}({', '.join(repr(value) for value in expected[kind])})"
            if "empty" in expected:
                line += ".is_empty()" if expected["empty"] else ".is_not_empty()"
            lines.append(line)
        if event.get("status") in {"failed", "error"}:
            break
    return lines


def render_trace(trace: Trace | Mapping[str, Any], *, artifact_path: str | Path | None = None, verbose: bool = False) -> str:
    data = trace.to_dict() if isinstance(trace, Trace) else dict(trace)
    labels = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "error": "ERROR", "running": "RUNNING"}
    lines = [f"{labels[data['status']]} {_safe(data['contract'])} [{_safe(data['adapter'])}]", ""]
    if data.get("skip_reason"):
        lines.append("Skipped: " + _safe(data["skip_reason"]))
    events = data.get("events", [])
    for event in events if verbose else []:
        args = event.get("arguments", {})
        detail = args.get("value", event.get("subject") or "runtime reconstruction")
        if event["operation"] == "query":
            observed = event.get("observations", {}).get("api", {})
            detail = f"{event.get('subject')!r} -> {observed.get('values', [])!r}"
        lines.append(f"{event['sequence']:02d} {event['operation'].upper():8} "
                     f"{_safe(detail, 98):98} {event.get('status', 'observed').upper()}")
    failing = next((event for event in events if event.get("status") in {"failed", "error"}), None)
    query_event = failing if failing and failing.get("observations") else next(
        (event for event in reversed(events) if event.get("observations")), None)
    if failing:
        lines.extend(["", "Invariant / execution failure:"])
        for name, observation in failing.get("observations", {}).items():
            for check in observation.get("checks", []):
                if not check["passed"]:
                    lines.append(f"  {name.upper()}: {_safe(check['message'])}")
        if failing.get("error"):
            lines.append("  " + _safe(failing["error"]))
    elif data.get("error"):
        lines.extend(["", "Test failure: " + _safe(data["error"])])
    if query_event:
        lines.extend(["", f"Oracle results at event #{query_event['sequence']:02d}:"])
        for name in ("api", "storage", "context", "behavior"):
            observation = query_event.get("observations", {}).get(name, {})
            state = observation.get("status", "unavailable")
            assertion = observation.get("assertion_status", "not_checked")
            label = {"passed": "PASS", "failed": "FAIL", "error": "ERROR"}.get(assertion, "not asserted")
            if state == "unavailable":
                label = "NOT AVAILABLE"
            elif state == "error" and assertion == "not_checked":
                label = "observation ERROR (not asserted)"
            lines.append(f"  {name.upper():9} {state:12} {label}")
            if observation.get("error"):
                lines.append("    " + _safe(observation["error"]))
        storage = query_event.get("observations", {}).get("storage", {})
        if storage.get("status") not in {None, "unavailable", "error"}:
            lines.extend(["", "Storage coverage:", "  " + _safe(storage.get("coverage", "unspecified")),
                          "  Scan complete: " + str(storage.get("complete", False)).lower()])
    divergence = data.get("first_divergence")
    if divergence:
        sentence = f"  First observed at #{divergence['observed_at_sequence']:02d} {divergence['operation'].upper()}"
        if divergence.get("after_sequence"):
            sentence += f", after #{divergence['after_sequence']:02d} {divergence['after_operation'].upper()}"
        lines.extend(["", "First divergence:", sentence])
        if divergence.get("last_verified_sequence"):
            lines.append(f"  Last successful assertion: #{divergence['last_verified_sequence']:02d}")
        lines.append("  This is an observation boundary, not a proven root cause.")
    if verbose and data["status"] in {"failed", "error"} and events:
        lines.extend(["", "Reproduction (recorded prefix; not minimized):"])
        lines.append("  Redacted outline only: rerun the source contract; placeholders are not replayable values.")
        lines.extend("  " + _safe(line) for line in reproduction(events))
    cleanup = data.get("cleanup", {})
    if cleanup.get("status") in {"blocked", "error", "unavailable", "disabled"}:
        lines.extend(["", "Cleanup: " + cleanup["status"].upper() + " - " + _safe(cleanup.get("reason", "inspect the local run ledger"))])
    for note in data.get("notes", []):
        lines.append("Note: " + _safe(note))
    if artifact_path:
        lines.extend(["", "Trace artifact: " + _safe(artifact_path)])
    return "\n".join(lines).rstrip()
