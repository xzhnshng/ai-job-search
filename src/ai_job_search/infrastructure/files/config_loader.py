"""Layered TOML configuration with deterministic hashing."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_job_search.infrastructure.files.paths import RuntimePaths


@dataclass(frozen=True)
class RuntimeConfig:
    private_dir: str = ".ai-job-search"
    timezone: str = "America/Los_Angeles"


@dataclass(frozen=True)
class DatabaseConfig:
    busy_timeout_ms: int = 5000


@dataclass(frozen=True)
class RetentionConfig:
    raw_observations_days: int = 30
    logs_days: int = 14
    reports_days: int = 30
    exports_days: int = 30
    backup_count: int = 5
    cache_max_mb: int = 250
    warn_free_disk_mb: int = 1024


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    database: DatabaseConfig
    retention: RetentionConfig

    @property
    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(workspace: Path | None = None) -> AppConfig:
    paths = RuntimePaths.resolve(workspace)
    defaults_path = paths.workspace / "config" / "defaults.toml"
    data = tomllib.loads(defaults_path.read_text(encoding="utf-8"))
    if paths.config.exists():
        private = tomllib.loads(paths.config.read_text(encoding="utf-8"))
        unknown = set(private) - {"runtime", "database", "retention"}
        if unknown:
            raise ValueError(f"Unknown configuration section(s): {sorted(unknown)}")
        data = _merge(data, private)

    runtime = RuntimeConfig(**data.get("runtime", {}))
    database = DatabaseConfig(**data.get("database", {}))
    retention = RetentionConfig(**data.get("retention", {}))
    try:
        ZoneInfo(runtime.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {runtime.timezone}") from exc
    if database.busy_timeout_ms < 0:
        raise ValueError("database.busy_timeout_ms cannot be negative")
    if any(value < 0 for value in asdict(retention).values()):
        raise ValueError("retention values cannot be negative")
    return AppConfig(runtime=runtime, database=database, retention=retention)
