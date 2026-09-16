"""Synthetic values: random across runs, reproducible within a lifecycle."""
from __future__ import annotations

import hashlib
import re
from uuid import uuid4


class Canary(str):
    """Text deliberately generated as safe synthetic test data."""


def canary(label: str = "canary", *, seed: str | None = None) -> Canary:
    """Use non-sensitive labels. Supply a seed only for deliberate replay."""
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", label):
        raise ValueError("A canary label must be 1-48 ASCII letters/digits/underscores, starting with a letter")
    if seed is not None and not isinstance(seed, str):
        raise ValueError("canary seed must be text")
    entropy = uuid4().hex if seed is None else hashlib.sha256((seed + "\0" + label).encode()).hexdigest()
    return Canary(f"MEMORYCHECK_{label.upper()}_{entropy[:16].upper()}")
