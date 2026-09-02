"""Official company-source polling and first-seen freshness persistence."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import quote

from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.http.safe_client import SafeHttpClient
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction


FRESHNESS_RULE_VERSION = "official-first-seen-v1"


def _ashby_endpoint(source_key: str) -> str:
    return (
        "https://api.ashbyhq.com/posting-api/job-board/"
        + quote(source_key, safe="")
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _location(job: dict[str, Any]) -> str | None:
    value = job.get("location")
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def _normalize_ashby_job(job: Any) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise ValueError("Ashby job record must be an object")
    external_id = _text(job.get("id"))
    title = _text(job.get("title"))
    canonical_url = _text(job.get("jobUrl"))
    application_url = _text(job.get("applyUrl"))
    if not external_id or not title or not canonical_url:
        raise ValueError("Ashby job record is missing id, title, or jobUrl")
    if not canonical_url.startswith("https://"):
        raise ValueError("Ashby job URL must use HTTPS")
    if application_url and not application_url.startswith("https://"):
        raise ValueError("Ashby application URL must use HTTPS")
    description = _text(job.get("descriptionPlain"))
    normalized = {
        "contract_version": "1",
        "source": "ashby",
        "external_job_id": external_id,
        "title": title,
        "location": _location(job),
        "canonical_url": canonical_url,
        "application_url": application_url,
        "department": _text(job.get("department")),
        "team": _text(job.get("team")),
        "employment_type": _text(job.get("employmentType")),
        "description": description,
        "source_published_at": _text(job.get("publishedAt")),
        "source_updated_at": None,
        "open": True,
    }
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    normalized["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    normalized["description_sha256"] = (
        hashlib.sha256(description.encode()).hexdigest() if description else None
    )
    return normalized


def _classify_new_job(
    published_at: str | None,
    previous_poll_at: str | None,
    observed_at: str,
) -> tuple[str, str, dict[str, Any]]:
    if previous_poll_at is None:
        return (
            "baseline_existing",
            "high",
            {"reason": "first_successful_poll", "source_published_at": published_at},
        )
    if not published_at:
        return (
            "newly_detected_date_unknown",
            "medium",
            {"reason": "new_official_id_without_publication_time"},
        )
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        window_start = datetime.fromisoformat(previous_poll_at.replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return (
            "date_unknown",
            "low",
            {"reason": "unparseable_source_publication_time", "value": published_at},
        )
    if window_start < published <= window_end:
        return (
            "verified_new",
            "high",
            {"reason": "new_official_id_published_inside_detection_window"},
        )
    return (
        "date_unknown",
        "medium",
        {"reason": "new_official_id_published_outside_detection_window"},
    )


def _record_freshness(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    source_ref_id: str,
    classification: str,
    confidence: str,
    evidence: dict[str, Any],
    window_start: str | None,
    observed_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO job_freshness(
            id, job_id, job_source_ref_id, classification, confidence,
            decision_rule_version, evidence_json, detection_window_start,
            detection_window_end, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("job_freshness"),
            job_id,
            source_ref_id,
            classification,
            confidence,
            FRESHNESS_RULE_VERSION,
            json.dumps(evidence, sort_keys=True),
            window_start,
            observed_at,
            observed_at,
        ),
    )


def poll_company_source(
    connection: sqlite3.Connection,
    source_id: str,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """Poll one enabled official source and persist an immutable comparison run."""
    source = connection.execute(
        """
        SELECT cs.id, cs.company_id, cs.adapter_type, cs.source_key, cs.enabled,
               cs.health_status, c.name AS company_name
        FROM company_source cs
        JOIN company c ON c.id = cs.company_id
        WHERE cs.id = ?
        """,
        (source_id,),
    ).fetchone()
    if source is None:
        raise KeyError(source_id)
    if not source["enabled"]:
        raise ValueError("Company source is not enabled")
    if source["health_status"] != "healthy":
        raise ValueError("Company source is not healthy")
    if source["adapter_type"] != "ashby" or not source["source_key"]:
        raise ValueError("Polling is not implemented for this company-source adapter")

    previous = connection.execute(
        """
        SELECT completed_at
        FROM source_run
        WHERE company_source_id = ? AND status = 'completed'
          AND idempotency_key LIKE 'poll:%'
        ORDER BY completed_at DESC LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    previous_poll_at = previous["completed_at"] if previous else None
    run_id = new_id("source_run")
    started_at = utc_now_text()
    endpoint = _ashby_endpoint(source["source_key"])

    try:
        response = (client or SafeHttpClient()).fetch(
            endpoint,
            allowed_hosts={"api.ashbyhq.com"},
            accepted_content_types={"application/json"},
        )
        if not 200 <= response.status < 300:
            raise ValueError(f"Source returned HTTP {response.status}")
        payload = json.loads(response.body)
        records = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise ValueError("Ashby response did not match the expected jobs schema")
        normalized_jobs = [_normalize_ashby_job(record) for record in records]
        external_ids = [job["external_job_id"] for job in normalized_jobs]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("Ashby response contained duplicate job IDs")
    except Exception as exc:
        completed_at = utc_now_text()
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO source_run(
                    id, company_source_id, idempotency_key, status, started_at,
                    completed_at, error_code, error_message
                ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_id,
                    f"poll:{run_id}",
                    started_at,
                    completed_at,
                    type(exc).__name__.upper(),
                    str(exc)[:500],
                ),
            )
            detail = {
                "contract_version": "1",
                "adapter_type": source["adapter_type"],
                "message": str(exc)[:500],
                "run_kind": "poll",
            }
            connection.execute(
                """
                INSERT INTO source_health_event(
                    id, company_source_id, source_run_id, health_status,
                    detail_json, occurred_at
                ) VALUES (?, ?, ?, 'degraded', ?, ?)
                """,
                (
                    new_id("source_health"),
                    source_id,
                    run_id,
                    json.dumps(detail, sort_keys=True),
                    completed_at,
                ),
            )
            connection.execute(
                """
                UPDATE company_source
                SET health_status = 'degraded', last_checked_at = ?,
                    updated_at = ?, row_version = row_version + 1
                WHERE id = ?
                """,
                (completed_at, completed_at, source_id),
            )
        return {
            "source_id": source_id,
            "run_id": run_id,
            "status": "failed",
            "error": {"code": type(exc).__name__.upper(), "message": str(exc)[:500]},
        }

    observed_at = utc_now_text()
    counts = {
        "baseline_existing": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "closed": 0,
        "reopened": 0,
    }
    observed_external_ids = {job["external_job_id"] for job in normalized_jobs}
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO source_run(
                id, company_source_id, idempotency_key, status, started_at,
                completed_at, cursor_json, record_count
            ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?)
            """,
            (
                run_id,
                source_id,
                f"poll:{run_id}",
                started_at,
                observed_at,
                json.dumps({"kind": "full_snapshot", "adapter": "ashby"}),
                len(normalized_jobs),
            ),
        )
        for job in normalized_jobs:
            external_id = job["external_job_id"]
            content_sha = job["content_sha256"]
            connection.execute(
                """
                INSERT INTO raw_observation(
                    id, source_run_id, source_record_key, external_job_id,
                    content_sha256, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("raw_observation"),
                    run_id,
                    external_id,
                    external_id,
                    content_sha,
                    observed_at,
                ),
            )
            existing = connection.execute(
                """
                SELECT jsr.id AS source_ref_id, jsr.job_id, jsr.current_open,
                       js.content_sha256 AS latest_sha
                FROM job_source_ref jsr
                LEFT JOIN job_snapshot js ON js.id = (
                    SELECT id FROM job_snapshot
                    WHERE job_source_ref_id = jsr.id
                    ORDER BY observed_at DESC LIMIT 1
                )
                WHERE jsr.company_source_id = ? AND jsr.external_job_id = ?
                """,
                (source_id, external_id),
            ).fetchone()
            if existing is None:
                prior_job = connection.execute(
                    """
                    SELECT id FROM job
                    WHERE company_id = ? AND title = ? AND canonical_url IS ?
                    """,
                    (source["company_id"], job["title"], job["canonical_url"]),
                ).fetchone()
                job_id = prior_job["id"] if prior_job else new_id("job")
                if prior_job is None:
                    connection.execute(
                        """
                        INSERT INTO job(
                            id, company_id, title, canonical_url, location,
                            description_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            source["company_id"],
                            job["title"],
                            job["canonical_url"],
                            job["location"],
                            job["description_sha256"],
                            observed_at,
                        ),
                    )
                source_ref_id = new_id("job_source_ref")
                connection.execute(
                    """
                    INSERT INTO job_source_ref(
                        id, job_id, company_source_id, external_job_id,
                        canonical_url, application_url, current_open,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        source_ref_id,
                        job_id,
                        source_id,
                        external_id,
                        job["canonical_url"],
                        job["application_url"],
                        observed_at,
                        observed_at,
                    ),
                )
                classification, confidence, evidence = _classify_new_job(
                    job["source_published_at"], previous_poll_at, observed_at
                )
                _record_freshness(
                    connection,
                    job_id=job_id,
                    source_ref_id=source_ref_id,
                    classification=classification,
                    confidence=confidence,
                    evidence=evidence,
                    window_start=previous_poll_at,
                    observed_at=observed_at,
                )
                if classification == "baseline_existing":
                    counts["baseline_existing"] += 1
                else:
                    counts["new"] += 1
            else:
                job_id = existing["job_id"]
                source_ref_id = existing["source_ref_id"]
                was_closed = existing["current_open"] == 0
                connection.execute(
                    """
                    UPDATE job_source_ref
                    SET canonical_url = ?, application_url = ?, current_open = 1,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        job["canonical_url"],
                        job["application_url"],
                        observed_at,
                        source_ref_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE job
                    SET title = ?, canonical_url = ?, location = ?,
                        description_sha256 = ?
                    WHERE id = ?
                    """,
                    (
                        job["title"],
                        job["canonical_url"],
                        job["location"],
                        job["description_sha256"],
                        job_id,
                    ),
                )
                if was_closed:
                    counts["reopened"] += 1
                    _record_freshness(
                        connection,
                        job_id=job_id,
                        source_ref_id=source_ref_id,
                        classification="reopened",
                        confidence="high",
                        evidence={"reason": "official_id_returned_after_confirmed_absence"},
                        window_start=previous_poll_at,
                        observed_at=observed_at,
                    )
                if existing["latest_sha"] == content_sha:
                    counts["unchanged"] += 1
                    continue
                if not was_closed:
                    counts["updated"] += 1
                    _record_freshness(
                        connection,
                        job_id=job_id,
                        source_ref_id=source_ref_id,
                        classification="recently_updated",
                        confidence="high",
                        evidence={"reason": "official_record_content_changed"},
                        window_start=previous_poll_at,
                        observed_at=observed_at,
                    )

            connection.execute(
                """
                INSERT OR IGNORE INTO job_snapshot(
                    id, job_id, job_source_ref_id, source_run_id,
                    content_sha256, normalized_json, source_published_at,
                    source_updated_at, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("job_snapshot"),
                    job_id,
                    source_ref_id,
                    run_id,
                    content_sha,
                    json.dumps(job, sort_keys=True),
                    job["source_published_at"],
                    job["source_updated_at"],
                    observed_at,
                ),
            )

        open_refs = connection.execute(
            """
            SELECT id, external_job_id
            FROM job_source_ref
            WHERE company_source_id = ? AND current_open = 1
            """,
            (source_id,),
        ).fetchall()
        closed_ref_ids = [
            row["id"]
            for row in open_refs
            if row["external_job_id"] not in observed_external_ids
        ]
        for source_ref_id in closed_ref_ids:
            connection.execute(
                "UPDATE job_source_ref SET current_open = 0 WHERE id = ?",
                (source_ref_id,),
            )
        counts["closed"] = len(closed_ref_ids)

    return {
        "source_id": source_id,
        "run_id": run_id,
        "status": "completed",
        "first_successful_poll": previous_poll_at is None,
        "record_count": len(normalized_jobs),
        "counts": counts,
        "observed_at": observed_at,
    }
