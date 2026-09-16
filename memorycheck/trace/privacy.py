"""Field-by-field redaction. Provider raw data and metadata never leave process."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any


class Redactor:
    def __init__(self, mode: str = "redacted"):
        self.mode = mode
        self._key = secrets.token_bytes(32)  # Not persisted: prevents cross-run correlation.
        self.safe_values: set[str] = set()

    def mask(self, value: Any) -> str:
        text = str(value)
        if self.mode == "synthetic" and text in self.safe_values:
            return text
        digest = hmac.new(self._key, text.encode("utf-8", errors="replace"), hashlib.sha256).hexdigest()[:12]
        return f"<redacted:{digest}>"

    def label(self, value: str, fallback: str) -> str:
        return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,119}", value) else fallback

    def scope(self, data: dict[str, str]) -> dict[str, str]:
        return {self.label(key, "custom_scope"): self.mask(value) for key, value in data.items()}

    @staticmethod
    def error(value: Any) -> str | None:
        if value is None:
            return None
        match = re.match(r"([A-Za-z][A-Za-z0-9_]{0,63}(?:Error|Exception)):", str(value))
        kind = match.group(1) if match else "ExecutionError"
        if kind == "OperationTimeoutError":
            return "OperationTimeoutError: deadline expired; invariant was not evaluated. Check configured timeouts and use a fresh adapter."
        if kind == "OracleError":
            return "OracleError: a required observation was unavailable, malformed, or incomplete; invariant was not evaluated."
        if kind == "TraceSerializationError":
            return "TraceSerializationError: artifact could not be persisted; check directory permissions and available storage."
        return kind + ": detail omitted by trace privacy policy; check backend configuration and availability."

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["contract"] = self.label(payload["contract"], "memory_lifecycle")
        payload["adapter"] = self.label(payload["adapter"], "custom_adapter")
        payload["error"] = self.error(payload.get("error"))
        payload["privacy"] = {"trace_values": self.mode, "metadata": "omitted", "raw": "omitted"}
        for event in payload["events"]:
            if event.get("subject") is not None:
                event["subject"] = self.mask(event["subject"])
            event["scope"] = self.scope(event.get("scope", {}))
            event["arguments"] = {key: self.mask(value) if value is not None else None
                                  for key, value in event.get("arguments", {}).items()
                                  if key in {"value", "query", "memory_id", "alias"}}
            expected = event.get("expected", {})
            for key in ("contains", "excludes"):
                if key in expected:
                    expected[key] = [self.mask(value) for value in expected[key]]
            event["error"] = self.error(event.get("error"))
            for name, observation in event.get("observations", {}).items():
                observation["values"] = [self.mask(value) for value in observation.get("values", [])]
                observation["records"] = [
                    {"id": self.mask(row.get("id")), "value": self.mask(row.get("value")),
                     "scope": self.scope(row.get("scope", {})), "metadata": {}}
                    for row in observation.get("records", [])
                ]
                # Coverage descriptions and notes from arbitrary inspectors may contain
                # connection strings. The live observation retains them; artifacts don't.
                observation["coverage"] = (
                    "Public retrieval response; query absence is not physical erasure" if name == "api" else
                    "Adapter-declared storage boundary; see adapter documentation" if name == "storage" else
                    "Optional oracle boundary; see adapter documentation"
                )
                if observation.get("notes"):
                    observation["notes"] = ["Inspector notes omitted by trace privacy policy"]
                observation["error"] = self.error(observation.get("error"))
                for check in observation.get("checks", []):
                    if isinstance(check.get("expected"), str):
                        check["expected"] = self.mask(check["expected"])
                    action = {"contains": "Required value present", "excludes": "Excluded value absent", "empty": "Emptiness invariant"}.get(check["kind"], "Invariant")
                    check["message"] = f"{action}: {'satisfied' if check['passed'] else 'not established'} ({check['expected']!r})"
        return payload
