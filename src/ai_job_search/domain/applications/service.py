"""Application tracker event service."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.text import normalize_company_name
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction


STAGES = {
    "planning",
    "applied",
    "recruiter_screen",
    "assessment",
    "interview",
    "final_interview",
    "offer",
    "closed",
}
RESULTS = {None, "rejected", "withdrawn", "no_response", "offer", "hired"}


def create_application(
    connection: sqlite3.Connection,
    *,
    company_name: str,
    title: str,
    url: str | None = None,
    location: str | None = None,
    role_track: str | None = None,
    applied_date: str | None = None,
) -> dict[str, Any]:
    if applied_date:
        date.fromisoformat(applied_date)
    now = utc_now_text()
    normalized = normalize_company_name(company_name)
    if not normalized or not title.strip():
        raise ValueError("Company and title are required")
    with transaction(connection):
        row = connection.execute(
            "SELECT id FROM company WHERE normalized_name = ?", (normalized,)
        ).fetchone()
        company_id = row["id"] if row else new_id("company")
        if row is None:
            connection.execute(
                "INSERT INTO company(id, name, normalized_name, created_at) VALUES (?, ?, ?, ?)",
                (company_id, company_name.strip(), normalized, now),
            )
        job_id = new_id("job")
        connection.execute(
            """
            INSERT INTO job(id, company_id, title, canonical_url, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, company_id, title.strip(), url, location, now),
        )
        application_id = new_id("application")
        stage = "applied" if applied_date else "planning"
        connection.execute(
            """
            INSERT INTO application(
                id, job_id, role_track, applied_date, current_stage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (application_id, job_id, role_track, applied_date, stage, now, now),
        )
        _insert_event(
            connection,
            application_id,
            "application_created",
            now,
            {"stage": stage, "applied_date": applied_date},
            f"created:{application_id}",
        )
    return get_application(connection, application_id)


def _insert_event(
    connection: sqlite3.Connection,
    application_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> str:
    event_id = new_id("event")
    connection.execute(
        """
        INSERT INTO application_event(
            id, application_id, event_type, occurred_at, payload_json,
            idempotency_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            application_id,
            event_type,
            occurred_at,
            json.dumps(payload, sort_keys=True),
            idempotency_key,
            utc_now_text(),
        ),
    )
    return event_id


def add_event(
    connection: sqlite3.Connection,
    application_id: str,
    event_type: str,
    *,
    occurred_at: str,
    stage: str | None = None,
    result: str | None = None,
    next_action: str | None = None,
    next_action_date: str | None = None,
    notes: str | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    if stage is not None and stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    if result not in RESULTS:
        raise ValueError(f"Unknown result: {result}")
    if next_action_date:
        date.fromisoformat(next_action_date)
    payload = {
        key: value
        for key, value in {
            "stage": stage,
            "result": result,
            "next_action": next_action,
            "next_action_date": next_action_date,
            "notes": notes,
        }.items()
        if value is not None
    }
    with transaction(connection):
        exists = connection.execute(
            "SELECT 1 FROM application WHERE id = ?", (application_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(application_id)
        _insert_event(
            connection,
            application_id,
            event_type,
            occurred_at,
            payload,
            idempotency_key,
        )
        fields = ["updated_at = ?", "row_version = row_version + 1"]
        values: list[Any] = [utc_now_text()]
        for column, value in (
            ("current_stage", stage),
            ("current_result", result),
            ("next_action", next_action),
            ("next_action_date", next_action_date),
            ("notes", notes),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        values.append(application_id)
        connection.execute(
            f"UPDATE application SET {', '.join(fields)} WHERE id = ?",
            values,
        )
    return get_application(connection, application_id)


def get_application(connection: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT a.*, j.title, j.canonical_url, j.location, c.name AS company
        FROM application a
        JOIN job j ON j.id = a.job_id
        JOIN company c ON c.id = j.company_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()
    if row is None:
        raise KeyError(application_id)
    return dict(row)


def list_applications(
    connection: sqlite3.Connection,
    *,
    stage: str | None = None,
    result: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[str] = []
    if stage:
        clauses.append("a.current_stage = ?")
        values.append(stage)
    if result:
        clauses.append("a.current_result = ?")
        values.append(result)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT a.*, j.title, j.canonical_url, j.location, c.name AS company
        FROM application a
        JOIN job j ON j.id = a.job_id
        JOIN company c ON c.id = j.company_id
        {where}
        ORDER BY COALESCE(a.applied_date, substr(a.created_at, 1, 10)) DESC, c.name, j.title
        """,
        values,
    )
    return [dict(row) for row in rows]


def timeline(connection: sqlite3.Connection, application_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, event_type, occurred_at, payload_json, created_at
        FROM application_event
        WHERE application_id = ?
        ORDER BY occurred_at, created_at
        """,
        (application_id,),
    )
    return [
        {**dict(row), "payload": json.loads(row["payload_json"])}
        for row in rows
    ]
