"""Stable CLI response envelopes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Envelope:
    command: str
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    warnings: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    run_id: str | None = None
    contract_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        return {key: value for key, value in result.items() if value not in (None, (), [])}
