"""Read-only query boundary for jobs observed on official company sources."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


FRESHNESS_CLASSIFICATIONS = frozenset(
    {
        "baseline_existing",
        "verified_new",
        "newly_detected_date_unknown",
        "recently_updated",
        "reopened",
        "likely_repost",
        "aggregator_only",
        "date_unknown",
    }
)


def list_monitored_jobs(
    connection: sqlite3.Connection,
    *,
    job_id: str | None = None,
    source_id: str | None = None,
    classifications: tuple[str, ...] = (),
    classified_since: str | None = None,
    classified_before: str | None = None,
    open_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValueError("offset cannot be negative")
    unknown = set(classifications) - FRESHNESS_CLASSIFICATIONS
    if unknown:
        raise ValueError(f"Unknown freshness classification: {sorted(unknown)[0]}")

    clauses = []
    params: list[Any] = []
    if job_id:
        clauses.append("j.id = ?")
        params.append(job_id)
    if source_id:
        clauses.append("jsr.company_source_id = ?")
        params.append(source_id)
    if open_only:
        clauses.append("jsr.current_open = 1")
    if classifications:
        placeholders = ",".join("?" for _ in classifications)
        clauses.append(f"jf.classification IN ({placeholders})")
        params.extend(classifications)
    if classified_since:
        clauses.append("jf.classified_at >= ?")
        params.append(classified_since)
    if classified_before:
        clauses.append("jf.classified_at < ?")
        params.append(classified_before)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend((limit, offset))

    rows = connection.execute(
        f"""
        SELECT j.id, j.title, j.location, c.id AS company_id,
               c.name AS company_name, jsr.company_source_id,
               jsr.external_job_id, jsr.canonical_url, jsr.application_url,
               jsr.current_open, jsr.first_seen_at, jsr.last_seen_at,
               cs.adapter_type, cs.authority, cs.health_status AS source_health,
               jf.classification, jf.confidence, jf.classified_at,
               js.source_published_at, js.source_updated_at, js.normalized_json
        FROM job_source_ref jsr
        JOIN job j ON j.id = jsr.job_id
        JOIN company c ON c.id = j.company_id
        JOIN company_source cs ON cs.id = jsr.company_source_id
        LEFT JOIN job_freshness jf ON jf.id = (
            SELECT id FROM job_freshness
            WHERE job_source_ref_id = jsr.id
            ORDER BY classified_at DESC, id DESC LIMIT 1
        )
        LEFT JOIN job_snapshot js ON js.id = (
            SELECT id FROM job_snapshot
            WHERE job_source_ref_id = jsr.id
            ORDER BY observed_at DESC, id DESC LIMIT 1
        )
        {where}
        ORDER BY jf.classified_at DESC, jsr.last_seen_at DESC, c.name, j.title
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        normalized_json = item.pop("normalized_json")
        normalized = json.loads(normalized_json) if normalized_json else {}
        item["department"] = normalized.get("department")
        item["team"] = normalized.get("team")
        item["employment_type"] = normalized.get("employment_type")
        item["current_open"] = bool(item["current_open"])
        items.append(item)
    return items


def get_monitored_job(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    matches = list_monitored_jobs(
        connection, job_id=job_id, open_only=False, limit=1000
    )
    if not matches:
        raise KeyError(job_id)
    return {"job": matches[0], "source_observations": matches}
