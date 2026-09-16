"""Atomic, private-by-default JSON artifacts; no network or telemetry."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from memorycheck.errors import MemoryCheckError, TraceSerializationError
from memorycheck.trace.schema import SCHEMA_VERSION, Trace


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [json_safe(item) for item in value]
    # Preserve the fact that a provider metadata value was not JSON-native.
    return {"__python_type__": type(value).__name__}


def write_json_atomic(path: str | Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".memorycheck-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(json_safe(payload), stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def save_trace(trace: Trace, directory: str | Path) -> Path:
    try:
        payload = trace.to_dict()
        validate_trace(payload)
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", payload["contract"]).strip("._-")[:80] or "contract"
        # Do not accept a caller-supplied run ID as a filesystem path.
        identifier = re.sub(r"[^a-zA-Z0-9-]", "_", trace.run_id)[:80] or "run"
        return write_json_atomic(Path(directory) / f"{slug}--{identifier}.json", payload)
    except Exception as exc:
        raise TraceSerializationError(f"Trace could not be written ({type(exc).__name__}); check filesystem permissions") from None


def load_trace(path: str | Path) -> dict[str, Any]:
    try:
        if Path(path).stat().st_size > 16_777_216:
            raise ValueError("Trace exceeds the 16 MiB reader limit")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result
        def invalid_constant(value):
            raise ValueError("Non-finite JSON number")
        payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                             parse_constant=invalid_constant)
    except (OSError, ValueError, RecursionError) as exc:
        raise MemoryCheckError(f"Cannot load trace ({type(exc).__name__}); check path, JSON syntax, and size") from None
    validate_trace(payload)
    return payload


def validate_trace(payload: Any) -> None:
    """Check the renderer's full structural boundary without a schema dependency.

    The bundled Draft 2020-12 schema supplies exhaustive semantic validation
    for third-party consumers; this loader rejects malformed renderer inputs.
    """
    def reject(message: str) -> None:
        raise MemoryCheckError("Invalid trace: " + message)

    def string_list(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    def scope_valid(value: Any) -> bool:
        return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in value.items())

    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        reject(f"expected schema_version {SCHEMA_VERSION!r}")
    required = {"schema_version", "contract", "adapter", "run_id", "started_at", "finished_at",
                "status", "capabilities", "missing_capabilities", "notes", "events", "error",
                "first_divergence", "skip_reason", "memorycheck_version", "adapter_version",
                "provider_version", "python_version", "platform", "pytest_version", "namespace",
                "privacy", "cleanup"}
    if set(payload) != required:
        reject("missing or unknown envelope fields")
    for key in ("memorycheck_version", "adapter_version", "provider_version", "python_version", "platform", "pytest_version"):
        if payload[key] is not None and not isinstance(payload[key], str):
            reject("environment metadata must be text or null")
    privacy = payload["privacy"]
    if (not isinstance(privacy, dict) or set(privacy) != {"trace_values", "metadata", "raw"}
            or privacy["trace_values"] not in {"redacted", "synthetic"}
            or privacy["metadata"] != "omitted" or privacy["raw"] != "omitted"):
        reject("unsupported privacy envelope")
    cleanup = payload["cleanup"]
    if (not isinstance(cleanup, dict) or set(cleanup) - {"status", "reason", "scopes", "error_type"}
            or cleanup.get("status") not in {"not_started", "not_needed", "disabled", "blocked", "completed", "best_effort", "unavailable", "error"}):
        reject("invalid cleanup envelope")
    if "scopes" in cleanup and (type(cleanup["scopes"]) is not int or cleanup["scopes"] < 0):
        reject("cleanup scopes must be a nonnegative integer")
    for key in ("reason", "error_type"):
        if key in cleanup and not isinstance(cleanup[key], str):
            reject("cleanup diagnostic fields must be text")
    for key in ("contract", "adapter", "run_id", "started_at", "status", "namespace"):
        if not isinstance(payload.get(key), str):
            reject(f"{key!r} must be a string")
    if payload["status"] not in {"running", "passed", "failed", "error", "skipped"}:
        reject("unknown run status")
    for key in ("capabilities", "missing_capabilities", "notes"):
        if not string_list(payload.get(key, [])):
            reject(f"{key!r} must be a string list")
    for key in ("finished_at", "skip_reason", "error"):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            reject(f"{key!r} must be text or null")
    divergence = payload.get("first_divergence")
    if divergence is not None:
        if not isinstance(divergence, dict) or not isinstance(divergence.get("operation"), str):
            reject("invalid first_divergence")
        for key in ("observed_at_sequence", "after_sequence", "last_verified_sequence"):
            value = divergence.get(key)
            if value is None and key != "observed_at_sequence":
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                reject(f"invalid divergence {key}")
        if divergence.get("after_sequence") is not None and not isinstance(divergence.get("after_operation"), str):
            reject("after_operation is required for an after_sequence")
    events = payload.get("events")
    if not isinstance(events, list):
        reject("events must be a list")
    for sequence, event in enumerate(events, 1):
        if not isinstance(event, dict) or type(event.get("sequence")) is not int or event["sequence"] != sequence:
            reject("event sequences must be contiguous and start at 1")
        if set(event) != {"sequence", "timestamp", "operation", "subject", "scope", "arguments", "expected", "observations", "status", "error"}:
            reject("missing or unknown event fields")
        timestamp = event["timestamp"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            reject("event timestamp must be a finite nonnegative number")
        for key in ("subject", "error"):
            if event[key] is not None and not isinstance(event[key], str):
                reject("event subject and error must be text or null")
        if event.get("operation") not in {"create", "query", "update", "delete", "restart"}:
            reject("unknown event operation")
        if event.get("status") not in {"running", "observed", "passed", "failed", "error"}:
            reject("unknown event status")
        if not scope_valid(event.get("scope", {})):
            reject("event scope must map strings to strings")
        if not isinstance(event.get("arguments", {}), dict) or not isinstance(event.get("expected", {}), dict):
            reject("arguments and expected must be mappings")
        expected = event.get("expected", {})
        if set(expected) - {"contains", "excludes", "oracles", "match", "empty"}:
            reject("unknown expectation field")
        if "empty" in expected and not isinstance(expected["empty"], bool):
            reject("expected.empty must be boolean")
        if "match" in expected and expected["match"] not in {"exact", "substring"}:
            reject("unknown matching mode")
        for key in ("contains", "excludes", "oracles"):
            if not string_list(expected.get(key, [])):
                reject(f"expected.{key} must be a string list")
        observations = event.get("observations")
        if not isinstance(observations, dict):
            reject("observations must be a mapping")
        for observation in observations.values():
            if not isinstance(observation, dict):
                reject("observation must be a mapping")
            if set(observation) != {"status", "values", "records", "coverage", "complete", "assertion_status", "checks", "error", "notes"}:
                reject("missing or unknown observation fields")
            if not isinstance(observation["complete"], bool) or not isinstance(observation["coverage"], str):
                reject("invalid observation completeness or coverage")
            if observation["assertion_status"] not in {"not_checked", "passed", "failed", "error"}:
                reject("unknown assertion status")
            if observation["error"] is not None and not isinstance(observation["error"], str):
                reject("observation error must be text or null")
            if not isinstance(observation["records"], list):
                reject("observation records must be a list")
            for record in observation["records"]:
                if (not isinstance(record, dict) or set(record) != {"id", "value", "scope", "metadata"}
                        or not isinstance(record["id"], str) or not record["id"]
                        or not isinstance(record["value"], str) or not scope_valid(record["scope"])
                        or not isinstance(record["metadata"], dict)):
                    reject("invalid normalized trace record")
            if observation.get("status") not in {"present", "absent", "error", "unavailable"}:
                reject("unknown observation status")
            if not string_list(observation.get("values", [])) or not string_list(observation.get("notes", [])):
                reject("observation values and notes must be string lists")
            checks = observation.get("checks", [])
            if not isinstance(checks, list):
                reject("checks must be a list")
            for check in checks:
                if (not isinstance(check, dict) or not isinstance(check.get("passed"), bool)
                        or not isinstance(check.get("message"), str)
                        or set(check) != {"kind", "expected", "passed", "message"}
                        or check["kind"] not in {"contains", "excludes", "empty"}
                        or not isinstance(check["expected"], (str, bool))):
                    reject("checks require a supported kind, expected value, boolean result and text message")
    if divergence is not None and divergence["observed_at_sequence"] > len(events):
        reject("first divergence must reference an existing event")
