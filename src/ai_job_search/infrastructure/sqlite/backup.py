"""Verified SQLite online backup and restore helpers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.files.config_loader import AppConfig
from ai_job_search.infrastructure.sqlite.migrations import integrity, migration_status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(paths: RuntimePaths, config: AppConfig) -> dict[str, object]:
    if not paths.database.exists():
        raise FileNotFoundError("Database does not exist")
    paths.initialize()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = paths.backups / f"state-{stamp}.sqlite3"
    manifest = target.with_suffix(".manifest.json")
    source = connect(paths, config, readonly=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    verify = sqlite3.connect(target)
    verify.row_factory = sqlite3.Row
    try:
        check = integrity(verify)
        status = migration_status(verify, paths.workspace)
    finally:
        verify.close()
    if not check["ok"]:
        target.unlink(missing_ok=True)
        raise RuntimeError("Backup failed integrity verification")
    payload = {
        "database": target.name,
        "sha256": _sha256(target),
        "size_bytes": target.stat().st_size,
        "created_at": datetime.now(UTC).isoformat(),
        "schema": status["current"],
    }
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _enforce_retention(paths, config.retention.backup_count)
    return {**payload, "path": str(target), "manifest": str(manifest)}


def _enforce_retention(paths: RuntimePaths, keep: int) -> None:
    backups = sorted(paths.backups.glob("state-*.sqlite3"), reverse=True)
    for old in backups[max(keep, 0) :]:
        old.unlink(missing_ok=True)
        old.with_suffix(".manifest.json").unlink(missing_ok=True)
