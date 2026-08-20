"""SQLite connection policy."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_job_search.infrastructure.files.config_loader import AppConfig
from ai_job_search.infrastructure.files.paths import RuntimePaths


def connect(
    paths: RuntimePaths,
    config: AppConfig,
    *,
    readonly: bool = False,
) -> sqlite3.Connection:
    if readonly:
        if not paths.database.exists():
            raise FileNotFoundError(paths.database)
        connection = sqlite3.connect(
            f"file:{paths.database}?mode=ro",
            uri=True,
            isolation_level=None,
        )
    else:
        paths.initialize()
        connection = sqlite3.connect(paths.database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {config.database.busy_timeout_ms}")
    if not readonly:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


def database_files(database: Path) -> tuple[Path, Path, Path]:
    return (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    )
