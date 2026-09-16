"""Versioned, location-aware safe YAML. Validate the whole contract before writes."""
from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import get_close_matches
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from memorycheck.adapters.conformance import CAPABILITIES, valid_capability
from memorycheck.contracts.model import Contract, Step
from memorycheck.errors import ContractError
from memorycheck.oracles.base import ORACLE_CAPABILITIES
from memorycheck.types import Scope

MAX_CONTRACT_BYTES = 1_048_576
MAX_STEPS = 10_000
SCHEMA_VERSION = "0.1"
SCOPE_KEYS = {"user_id", "tenant_id", "session_id"}
OPERATIONS = {"create", "update", "delete", "query", "restart", "expect"}


class _MarkedDict(dict):
    def __init__(self, *args, mark=None, marks=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark, self.marks = mark, marks or {}


class _UniqueSafeLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._node_count, self._depth = 0, 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            mark = self.peek_event().start_mark
            raise ContractError("YAML aliases are not supported; expand values inline", line=mark.line + 1, column=mark.column + 1)
        self._node_count += 1
        self._depth += 1
        if self._node_count > 100_000 or self._depth > 60:
            raise ContractError("Contract is excessively nested or complex")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _unique_mapping(loader, node, deep=False):
    result = _MarkedDict(mark=node.start_mark)
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ContractError("Mapping keys must be strings", line=key_node.start_mark.line + 1, column=key_node.start_mark.column + 1)
        if key in result:
            raise ContractError(f"Duplicate YAML key {key!r}", line=key_node.start_mark.line + 1, column=key_node.start_mark.column + 1)
        result[key] = loader.construct_object(value_node, deep=deep)
        result.marks[key] = key_node.start_mark
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractError(f"{where} must be a mapping with string keys")
    return _MarkedDict(value, mark=getattr(value, "mark", None), marks=getattr(value, "marks", {}))


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where} must be a nonempty string; quote YAML booleans and numbers")
    # Deliberately not a general-purpose template language.
    remaining = re.sub(r"\{\{canary:[A-Za-z][A-Za-z0-9_]{0,47}\}\}", "", value)
    if "{{" in remaining or "}}" in remaining:
        raise ContractError(f"{where} contains an invalid template; use {{{{canary:LABEL}}}} only")
    return value


def _strings(value: Any, where: str, *, scalar: bool = False) -> list[str]:
    if scalar and isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"{where} must be {'a string or ' if scalar else ''}a list of strings")
    return [_text(item, where) for item in value]


def _problem(message: str, data=None, key=None, *, choices=None) -> None:
    mark = getattr(data, "marks", {}).get(key) or getattr(data, "mark", None)
    hint = None
    if choices:
        alias = {"remove": "delete", "remember": "create", "correct": "update", "recall": "query"}.get(str(key))
        close = [alias] if alias in choices else get_close_matches(str(key), sorted(choices), n=1, cutoff=0.55)
        hint = (f"Did you mean {close[0]!r}?\n" if close else "") + "Valid choices: " + ", ".join(sorted(choices))
    raise ContractError(message, line=mark.line + 1 if mark else None,
                        column=mark.column + 1 if mark else None, hint=hint)


def _unknown(data: dict, allowed: set[str], where: str) -> None:
    extra = set(data) - allowed
    if extra:
        key = sorted(extra)[0]
        _problem(f"Unknown {where} field: {key!r}", data, key, choices=allowed)


def _scope(value: Any, where: str) -> Scope:
    try:
        return Scope.from_mapping(_mapping(value, where))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(f"{where}: all scope keys and values must be nonempty strings") from None


def parse_contract(text: str, *, source: str | None = None) -> Contract:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise ContractError("Contract must be UTF-8 text of at most 1 MiB", source=source)
    data = None
    try:
        data = yaml.load(text, Loader=_UniqueSafeLoader)
        return contract_from_mapping(data, source=source)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        error = ContractError("Malformed YAML; check indentation, quoting, and supported scalar types",
                              source=source, line=mark.line + 1 if mark else None,
                              column=mark.column + 1 if mark else None)
    except ContractError as exc:
        error = exc
        error.source = source or error.source
        if error.line is None and isinstance(data, Mapping):
            match = re.search(r"step (\d+)", error.message)
            steps = data.get("steps")
            node = steps[int(match.group(1)) - 1] if match and isinstance(steps, list) else data
            mark = getattr(node, "mark", None)
            if mark:
                error.line, error.column = mark.line + 1, mark.column + 1
    except RecursionError:
        error = ContractError("Recursive or excessively nested YAML is not supported", source=source)
    if error.line is not None:
        lines = text.splitlines()
        if 1 <= error.line <= len(lines):
            excerpt = lines[error.line - 1]
            # Diagnostics show structure, not accidental authored payloads or credentials.
            excerpt = re.sub(r"((?:value|query|contains|excludes|user_id|tenant_id|session_id|api_key|password)\s*:).*",
                             r"\1 <redacted>", excerpt)
            error.excerpt = excerpt[:240].replace("\x1b", "\\x1b")
            error.column = min(error.column or 1, 241)
    raise error from None


def contract_from_mapping(data: Any, *, source: str | None = None) -> Contract:
    data = _mapping(data, "contract")
    _unknown(data, {"schema_version", "name", "description", "requires", "scope", "steps"}, "contract")
    if data.get("schema_version") != SCHEMA_VERSION:
        _problem('schema_version must be the quoted string "0.1"; add schema_version: "0.1" at the top', data, "schema_version")
    name = _text(data.get("name"), "name")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,119}", name):
        _problem("name must be a 1-120 character ASCII identifier without paths or whitespace", data, "name")
    requirements = _strings(data.get("requires", []), "requires")
    if len(requirements) != len(set(requirements)):
        _problem("requires must not contain duplicate capabilities", data, "requires")
    for requirement in requirements:
        if not valid_capability(requirement):
            _problem(f"Unknown required capability: {requirement!r}", data, "requires", choices=CAPABILITIES)
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ContractError("description must be a string")
    default_scope = _scope(data.get("scope", {}), "scope")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > MAX_STEPS:
        _problem(f"steps must be a nonempty list of at most {MAX_STEPS} operations", data, "steps")
    steps = []
    created: dict[str, Scope] = {}
    deleted: set[str] = set()
    for index, raw in enumerate(raw_steps, 1):
        where = f"step {index}"
        raw = _mapping(raw, where)
        if len(raw) != 1:
            _problem(f"{where} must contain exactly one operation", raw)
        operation, value = next(iter(raw.items()))
        if operation not in OPERATIONS:
            _problem(f"Unknown operation: {operation!r} at {where}", raw, operation, choices=OPERATIONS)
        args = _mapping(value, f"{where}.{operation}")
        common = {"scope"} | SCOPE_KEYS
        allowed = {
            "create": {"id", "value"} | common, "update": {"id", "value"} | common,
            "delete": {"id"} | common, "restart": set(), "query": {"query"} | common,
            "expect": {"query", "contains", "excludes", "empty", "match", "oracles"} | common,
        }[operation]
        _unknown(args, allowed, f"{where}.{operation}")
        if operation != "restart":
            scoped = _scope(args.pop("scope", {}), f"{where}.scope").as_dict()
            for key in SCOPE_KEYS:
                if key in args:
                    if key in scoped:
                        raise ContractError(f"{where}: {key} is specified both directly and in scope")
                    scoped[key] = args.pop(key)
            args["scope"] = _scope(scoped, f"{where}.scope").as_dict()
        if operation in {"create", "update", "delete"}:
            alias = _text(args.get("id"), f"{where}.id")
            if operation == "create":
                if alias in created:
                    raise ContractError(f"{where}: duplicate logical memory ID")
                created[alias] = default_scope.merged(args["scope"])
            elif alias not in created or alias in deleted:
                raise ContractError(f"{where}: the logical ID must be created and not already deleted before {operation}")
            elif created[alias].merged(args["scope"]) != created[alias]:
                raise ContractError(f"{where}: a mutation cannot change its created reference's scope")
            if operation == "delete":
                deleted.add(alias)
        if operation in {"create", "update"}:
            args["value"] = _text(args.get("value"), f"{where}.value")
        if operation in {"expect", "query"}:
            args["query"] = _text(args.get("query"), f"{where}.query")
        if operation == "expect":
            args["contains"] = _strings(args.get("contains", []), f"{where}.contains", scalar=True)
            args["excludes"] = _strings(args.get("excludes", []), f"{where}.excludes", scalar=True)
            if set(args["contains"]) & set(args["excludes"]):
                raise ContractError(f"{where}: a value cannot be both required and excluded")
            if "empty" in args and not isinstance(args["empty"], bool):
                raise ContractError(f"{where}.empty must be a boolean")
            if not args["contains"] and not args["excludes"] and "empty" not in args:
                raise ContractError(f"{where}: expect needs contains, excludes, or empty")
            if args.get("empty") is True and args["contains"]:
                raise ContractError(f"{where}: empty: true contradicts contains")
            args["match"] = args.get("match", "substring")
            if args["match"] not in {"exact", "substring"}:
                raise ContractError(f"{where}.match must be exact or substring")
            names = _strings(args.get("oracles", ["api"]), f"{where}.oracles")
            if not names or len(names) != len(set(names)) or set(names) - ORACLE_CAPABILITIES.keys():
                raise ContractError(f"{where}.oracles must name unique api/storage/context/behavior layers")
            args["oracles"] = names
        steps.append(Step(operation, dict(args)))
    if not any(step.operation == "expect" for step in steps):
        _problem("A lifecycle contract needs at least one expect invariant; operations alone cannot prove conformance", data, "steps")
    return Contract(name, tuple(steps), frozenset(requirements), description, default_scope, source, SCHEMA_VERSION)


def builtin_names() -> list[str]:
    root = resources.files("memorycheck.contracts").joinpath("builtin")
    return sorted(item.name.removesuffix(".yaml") for item in root.iterdir() if item.name.endswith(".yaml"))


def load_builtin(name: str) -> Contract:
    if name not in builtin_names():
        raise ContractError("Unknown built-in contract; use 'memorycheck list'")
    text = resources.files("memorycheck.contracts").joinpath("builtin", name + ".yaml").read_text(encoding="utf-8")
    return parse_contract(text, source="builtin:" + name)


def load_contract(source: str | Path | Contract) -> Contract:
    if isinstance(source, Contract):
        return contract_from_mapping(source.to_dict(), source=source.source)
    source_text = str(source)
    if source_text.startswith("builtin:"):
        return load_builtin(source_text.removeprefix("builtin:"))
    path = Path(source)
    try:
        if path.stat().st_size > MAX_CONTRACT_BYTES:
            raise ContractError("Contract exceeds the 1 MiB size limit", source=str(path))
        return parse_contract(path.read_text(encoding="utf-8"), source=str(path))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"Cannot read contract ({type(exc).__name__}); check the file path and encoding", source=str(path)) from None
