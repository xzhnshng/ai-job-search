"""Stable text normalization shared across domain services."""

from __future__ import annotations

import re


def normalize_company_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
