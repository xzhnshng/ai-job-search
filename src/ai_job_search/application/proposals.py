"""Proposal/approval boundary for consequential changes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction


def create_proposal(
    connection: sqlite3.Connection,
    proposal_type: str,
    payload: dict[str, Any],
    expected_versions: dict[str, int] | None = None,
) -> dict[str, Any]:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    proposal_id = new_id("prop")
    created_at = utc_now_text()
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO proposal(
                id, proposal_type, state, payload_json, payload_sha256,
                expected_versions_json, created_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                proposal_id,
                proposal_type,
                canonical,
                hashlib.sha256(canonical.encode()).hexdigest(),
                json.dumps(expected_versions or {}, sort_keys=True),
                created_at,
            ),
        )
    return {"id": proposal_id, "state": "pending", "created_at": created_at, "payload": payload}


def decide_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    decision: str,
    *,
    actor: str = "user",
    reason: str | None = None,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    now = utc_now_text()
    with transaction(connection):
        row = connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        if row["state"] != "pending":
            raise ValueError(f"Proposal is already {row['state']}")
        connection.execute(
            "UPDATE proposal SET state = ?, decided_at = ? WHERE id = ?",
            (decision, now, proposal_id),
        )
        connection.execute(
            """
            INSERT INTO approval_event(id, proposal_id, decision, actor, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("approval"), proposal_id, decision, actor, reason, now),
        )
    return {"id": proposal_id, "state": decision, "decided_at": now}
