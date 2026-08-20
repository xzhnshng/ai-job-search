"""Register and extract career source files without creating candidate claims."""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
import json
from pathlib import Path
from typing import Any

from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.domain.evidence.inventory import SUPPORTED_SUFFIXES
from ai_job_search.infrastructure.files.artifact_store import store_artifact
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction
from ai_job_search.application.proposals import create_proposal


class ExtractionUnavailable(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_text(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".tex", ".md", ".txt", ".json", ".yaml", ".yml"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ExtractionUnavailable(
                "PDF extraction requires pypdf; install the declared project dependencies"
            ) from exc
        reader = PdfReader(path)
        return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    raise ExtractionUnavailable(f"Text extraction is not implemented for {suffix or 'this file type'}")


def register_source(
    connection: sqlite3.Connection,
    paths: RuntimePaths,
    source: Path,
    *,
    source_type: str,
) -> dict[str, Any]:
    workspace = paths.workspace.resolve()
    documents = (workspace / "documents").resolve()
    resolved = source.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(documents):
        raise ValueError("Career sources must be files below documents/")
    relative = str(resolved.relative_to(workspace))
    digest = _sha256(resolved)
    now = utc_now_text()
    existing = connection.execute(
        "SELECT id, sha256, extraction_status FROM source_artifact WHERE relative_path = ?",
        (relative,),
    ).fetchone()
    source_id = existing["id"] if existing else new_id("source")
    changed = existing is None or existing["sha256"] != digest
    with transaction(connection):
        if existing:
            connection.execute(
                """
                UPDATE source_artifact
                SET source_type = ?, sha256 = ?, size_bytes = ?, media_type = ?,
                    extraction_status = ?, last_seen_at = ?
                WHERE id = ?
                """,
                (
                    source_type,
                    digest,
                    resolved.stat().st_size,
                    mimetypes.guess_type(resolved.name)[0],
                    "pending" if changed else existing["extraction_status"],
                    now,
                    source_id,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO source_artifact(
                    id, source_type, relative_path, sha256, size_bytes, media_type,
                    extraction_status, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    source_id,
                    source_type,
                    relative,
                    digest,
                    resolved.stat().st_size,
                    mimetypes.guess_type(resolved.name)[0],
                    now,
                    now,
                ),
            )

    extraction_error = None
    text_path = None
    text_characters = 0
    if resolved.suffix.casefold() in SUPPORTED_SUFFIXES:
        try:
            text = _extract_text(resolved)
            text_characters = len(text)
            relative_text = f"source-cache/{digest}.txt"
            target = paths.private_root / relative_text
            if not target.exists():
                store_artifact(
                    connection,
                    paths,
                    relative_text,
                    (text + "\n").encode(),
                    artifact_type="source_extraction",
                    immutable=True,
                )
            text_path = str(target)
            with transaction(connection):
                connection.execute(
                    """
                    UPDATE source_artifact
                    SET extraction_status = 'extracted', extraction_version = 'text-v1'
                    WHERE id = ?
                    """,
                    (source_id,),
                )
        except ExtractionUnavailable as exc:
            extraction_error = str(exc)
            with transaction(connection):
                connection.execute(
                    "UPDATE source_artifact SET extraction_status = 'unsupported' WHERE id = ?",
                    (source_id,),
                )
    return {
        "id": source_id,
        "relative_path": relative,
        "sha256": digest,
        "changed": changed,
        "extraction_status": "unsupported" if extraction_error else "extracted",
        "extracted_text_path": text_path,
        "text_characters": text_characters,
        "warning": extraction_error,
    }


def list_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, source_type, relative_path, sha256, size_bytes, media_type,
                   extraction_status, extraction_version, first_seen_at, last_seen_at
            FROM source_artifact
            ORDER BY source_type, relative_path
            """
        )
    ]


def create_evidence_proposal(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
) -> dict[str, Any]:
    required = {"contract_version", "source_id", "experiences", "projects", "skills", "education"}
    missing = required - set(payload)
    unknown = set(payload) - (required | {"uncertainties"})
    if missing:
        raise ValueError(f"Extraction proposal missing field(s): {sorted(missing)}")
    if unknown:
        raise ValueError(f"Extraction proposal has unknown field(s): {sorted(unknown)}")
    if payload["contract_version"] != "1":
        raise ValueError("Unsupported extraction proposal contract version")
    source = connection.execute(
        "SELECT id, sha256 FROM source_artifact WHERE id = ?", (payload["source_id"],)
    ).fetchone()
    if source is None:
        raise ValueError("Extraction proposal references an unknown source")
    for field in ("experiences", "projects", "skills", "education"):
        if not isinstance(payload[field], list):
            raise ValueError(f"Extraction proposal field {field} must be a list")
    proposal = create_proposal(
        connection,
        "career_evidence_extraction",
        payload,
        expected_versions={"source_sha256": source["sha256"]},
    )
    return {
        "id": proposal["id"],
        "state": proposal["state"],
        "source_id": payload["source_id"],
        "summary": {
            "experiences": len(payload["experiences"]),
            "projects": len(payload["projects"]),
            "skills": len(payload["skills"]),
            "education": len(payload["education"]),
            "uncertainties": len(payload.get("uncertainties", [])),
        },
    }


def get_evidence_proposal(connection: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, state, payload_json, payload_sha256, expected_versions_json,
               created_at, decided_at
        FROM proposal
        WHERE id = ? AND proposal_type = 'career_evidence_extraction'
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    result["expected_versions"] = json.loads(result.pop("expected_versions_json"))
    return result
