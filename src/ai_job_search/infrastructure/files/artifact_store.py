"""Atomic private artifact storage."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction


def store_artifact(
    connection: sqlite3.Connection,
    paths: RuntimePaths,
    relative_path: str,
    content: bytes,
    *,
    artifact_type: str,
    immutable: bool = False,
) -> dict[str, object]:
    destination = paths.validate_owned_path(paths.private_root / relative_path)
    if destination.exists() and immutable:
        raise FileExistsError(f"Immutable artifact already exists: {relative_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        digest = hashlib.sha256(content).hexdigest()
        os.replace(temporary, destination)
        artifact_id = new_id("artifact")
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO artifact(
                    id, artifact_type, relative_path, sha256, size_bytes,
                    immutable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    artifact_type = excluded.artifact_type,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    created_at = excluded.created_at
                """,
                (
                    artifact_id,
                    artifact_type,
                    relative_path,
                    digest,
                    len(content),
                    int(immutable),
                    utc_now_text(),
                ),
            )
        return {
            "id": artifact_id,
            "path": str(destination),
            "sha256": digest,
            "size_bytes": len(content),
        }
    finally:
        temporary.unlink(missing_ok=True)
