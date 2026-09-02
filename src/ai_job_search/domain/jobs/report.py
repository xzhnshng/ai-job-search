"""Read-only daily monitoring report assembled from persisted source evidence."""

from __future__ import annotations

import sqlite3
from typing import Any

from ai_job_search.domain.jobs.query import list_monitored_jobs


REPORT_CLASSIFICATIONS = (
    "verified_new",
    "newly_detected_date_unknown",
    "date_unknown",
    "recently_updated",
    "reopened",
    "likely_repost",
)


def build_daily_report(
    connection: sqlite3.Connection,
    *,
    since: str,
    before: str | None = None,
) -> dict[str, Any]:
    if not since.strip():
        raise ValueError("Daily report requires a start timestamp")
    jobs = list_monitored_jobs(
        connection,
        classifications=REPORT_CLASSIFICATIONS,
        classified_since=since,
        classified_before=before,
        open_only=True,
        limit=1000,
    )
    groups = {"new": [], "updated": [], "reopened": [], "likely_repost": []}
    for job in jobs:
        classification = job["classification"]
        if classification in {
            "verified_new",
            "newly_detected_date_unknown",
            "date_unknown",
        }:
            groups["new"].append(job)
        elif classification == "recently_updated":
            groups["updated"].append(job)
        elif classification == "reopened":
            groups["reopened"].append(job)
        elif classification == "likely_repost":
            groups["likely_repost"].append(job)

    coverage = connection.execute(
        """
        SELECT
            count(DISTINCT CASE WHEN cpe.active = 1 THEN cpe.company_id END) AS targets,
            count(DISTINCT CASE WHEN cpe.active = 1 AND cs.id IS NOT NULL THEN cpe.company_id END)
                AS registered,
            count(DISTINCT CASE WHEN cpe.active = 1 AND cs.enabled = 1 THEN cpe.company_id END)
                AS enabled,
            count(DISTINCT CASE WHEN cpe.active = 1 AND cs.enabled = 1
                                      AND cs.health_status = 'healthy'
                                THEN cpe.company_id END) AS healthy_enabled
        FROM company_plan_entry cpe
        LEFT JOIN company_source cs ON cs.company_id = cpe.company_id
        """
    ).fetchone()
    source_health = [
        dict(row)
        for row in connection.execute(
            """
            SELECT cs.id AS source_id, c.name AS company_name, cs.adapter_type,
                   cs.enabled, cs.health_status, cs.last_checked_at
            FROM company_source cs
            JOIN company c ON c.id = cs.company_id
            ORDER BY cs.enabled DESC, c.name, cs.id
            """
        )
    ]
    for item in source_health:
        item["enabled"] = bool(item["enabled"])

    return {
        "contract_version": "1",
        "window": {"since": since, "before": before},
        "ranking_status": "not_implemented",
        "counts": {
            "reportable": len(jobs),
            **{name: len(items) for name, items in groups.items()},
        },
        "groups": groups,
        "coverage": dict(coverage),
        "source_health": source_health,
        "warnings": [
            "Baseline-existing jobs are excluded from daily findings",
            "Jobs are not fit-ranked until the matching slice is implemented",
            "Disabled and unhealthy sources are not evidence of zero openings",
        ],
    }
