"""Forward-only, hash-verified SQL migrations."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ai_job_search.domain.common.time import utc_now_text


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    name: str
    path: Path
    sha256: str
    sql: str


def discover_migrations(workspace: Path) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted((workspace / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                name=path.name,
                path=path,
                sha256=hashlib.sha256(sql.encode()).hexdigest(),
                sql=sql,
            )
        )
    if not migrations:
        raise MigrationError("No migrations found")
    return migrations


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("Incomplete SQL statement in migration")
    return statements


def _bootstrap(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            name TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def apply_migrations(connection: sqlite3.Connection, workspace: Path) -> list[str]:
    _bootstrap(connection)
    migrations = discover_migrations(workspace)
    applied = {
        row["name"]: row["sha256"]
        for row in connection.execute("SELECT name, sha256 FROM schema_migration")
    }
    available = {migration.name for migration in migrations}
    unknown = set(applied) - available
    if unknown:
        raise MigrationError(f"Database has migration(s) unavailable to this code: {sorted(unknown)}")

    completed: list[str] = []
    for migration in migrations:
        previous_hash = applied.get(migration.name)
        if previous_hash:
            if previous_hash != migration.sha256:
                raise MigrationError(f"Applied migration changed: {migration.name}")
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migration(name, sha256, applied_at) VALUES (?, ?, ?)",
                (migration.name, migration.sha256, utc_now_text()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        completed.append(migration.name)
    return completed


def migration_status(connection: sqlite3.Connection, workspace: Path) -> dict[str, object]:
    _bootstrap(connection)
    available = discover_migrations(workspace)
    applied = {
        row["name"]: row["sha256"]
        for row in connection.execute("SELECT name, sha256 FROM schema_migration")
    }
    return {
        "applied": sorted(applied),
        "pending": [item.name for item in available if item.name not in applied],
        "current": sorted(applied)[-1] if applied else None,
    }


def integrity(connection: sqlite3.Connection) -> dict[str, object]:
    integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_rows = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "ok": integrity_rows == ["ok"] and not foreign_rows,
        "integrity": integrity_rows,
        "foreign_key_violations": foreign_rows,
    }
