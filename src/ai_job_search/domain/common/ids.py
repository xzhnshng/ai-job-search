from __future__ import annotations

import re
import uuid

_ID = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{32}$")


def new_id(prefix: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError(f"Invalid ID prefix: {prefix}")
    return f"{prefix}_{uuid.uuid4().hex}"


def validate_id(value: str, prefix: str | None = None) -> str:
    if not _ID.fullmatch(value):
        raise ValueError(f"Invalid stable ID: {value}")
    if prefix and not value.startswith(f"{prefix}_"):
        raise ValueError(f"Expected {prefix} ID")
    return value
