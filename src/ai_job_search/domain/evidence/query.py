"""Read-only career-evidence queries and final-application eligibility policy."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable


APPROVED_STATES = frozenset({"approved", "verified"})
WORDING_STRENGTH = {
    "generalized": 0,
    "exact": 1,
    "strong": 2,
}


@dataclass(frozen=True)
class EligibilityContext:
    """Policy inputs for deciding whether a claim may enter an application plan.

    ``verified`` is accepted as a compatibility alias for ``approved`` because
    the first evidence-approval release stored candidate-approved claims with
    that state. New callers should treat both states as user-approved.
    """

    allowed_confidentiality: tuple[str, ...] = ("public", "private")
    requested_wording_strength: str = "exact"
    excluded_claim_types: tuple[str, ...] = ("boundary",)

    def __post_init__(self) -> None:
        if not self.allowed_confidentiality:
            raise ValueError("At least one confidentiality label must be allowed")
        if any(not item.strip() for item in self.allowed_confidentiality):
            raise ValueError("Confidentiality labels must be non-empty")
        if self.requested_wording_strength not in WORDING_STRENGTH:
            raise ValueError(
                f"Unsupported wording strength: {self.requested_wording_strength}"
            )
        if any(not item.strip() for item in self.excluded_claim_types):
            raise ValueError("Excluded claim types must be non-empty")


def _metric_issues(
    connection: sqlite3.Connection,
    claim_id: str,
    claim_type: str | None,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT m.name, m.value_text, m.verification_state
        FROM claim_metric cm
        JOIN metric m ON m.id = cm.metric_id
        WHERE cm.claim_id = ?
        ORDER BY m.id
        """,
        (claim_id,),
    ).fetchall()
    issues: list[str] = []
    if claim_type == "metric" and not rows:
        issues.append("metric_claim_has_no_metric_record")
    for row in rows:
        if row["verification_state"] not in APPROVED_STATES:
            issues.append("metric_not_approved")
        if not row["name"].strip() or not row["value_text"].strip():
            issues.append("metric_value_invalid")
    return sorted(set(issues))


def eligible_for_final_application(
    connection: sqlite3.Connection,
    claim: sqlite3.Row | dict[str, Any],
    context: EligibilityContext | None = None,
) -> dict[str, Any]:
    """Return a stable, explainable eligibility decision for one claim."""

    policy = context or EligibilityContext()
    reasons: list[str] = []
    claim_id = str(claim["id"])

    if claim["verification_state"] not in APPROVED_STATES:
        reasons.append("claim_not_approved")
    if claim["claim_type"] in policy.excluded_claim_types:
        reasons.append("claim_type_not_application_evidence")
    if claim["superseded_by_id"] is not None:
        reasons.append("claim_superseded")
    if claim["confidentiality"] not in policy.allowed_confidentiality:
        reasons.append("confidentiality_blocked")

    approved_strength = WORDING_STRENGTH.get(str(claim["wording_strength"]))
    requested_strength = WORDING_STRENGTH[policy.requested_wording_strength]
    if approved_strength is None or requested_strength > approved_strength:
        reasons.append("wording_strength_exceeded")

    evidence_count = connection.execute(
        """
        SELECT count(*)
        FROM claim_evidence ce
        JOIN source_span ss ON ss.id = ce.source_span_id
        JOIN source_artifact sa ON sa.id = ss.source_artifact_id
        WHERE ce.claim_id = ?
        """,
        (claim_id,),
    ).fetchone()[0]
    if evidence_count == 0:
        reasons.append("missing_active_evidence")

    reasons.extend(_metric_issues(connection, claim_id, claim["claim_type"]))
    reasons = sorted(set(reasons))
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "evidence_count": evidence_count,
        "policy": {
            "allowed_confidentiality": list(policy.allowed_confidentiality),
            "requested_wording_strength": policy.requested_wording_strength,
            "excluded_claim_types": list(policy.excluded_claim_types),
        },
    }


def _evidence(connection: sqlite3.Connection, claim_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT ce.support_type, ce.confidence,
                   ss.id AS source_span_id, ss.locator_type, ss.locator_json,
                   sa.id AS source_id, sa.source_type, sa.relative_path, sa.sha256
            FROM claim_evidence ce
            JOIN source_span ss ON ss.id = ce.source_span_id
            JOIN source_artifact sa ON sa.id = ss.source_artifact_id
            WHERE ce.claim_id = ?
            ORDER BY sa.relative_path, ss.id
            """,
            (claim_id,),
        )
    ]


def _metrics(connection: sqlite3.Connection, claim_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT m.id, m.name, m.value_text, m.unit, m.context,
                   m.verification_state
            FROM claim_metric cm
            JOIN metric m ON m.id = cm.metric_id
            WHERE cm.claim_id = ?
            ORDER BY m.name, m.id
            """,
            (claim_id,),
        )
    ]


def _skills(connection: sqlite3.Connection, claim_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT s.id, s.canonical_name, s.category, cs.relation
            FROM claim_skill cs
            JOIN skill s ON s.id = cs.skill_id
            WHERE cs.claim_id = ?
            ORDER BY s.canonical_name, s.id
            """,
            (claim_id,),
        )
    ]


def get_claim(
    connection: sqlite3.Connection,
    claim_id: str,
    *,
    context: EligibilityContext | None = None,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT c.id, c.claim_text, c.claim_type, c.verification_state,
               c.wording_strength, c.confidentiality, c.superseded_by_id,
               c.row_version, c.created_at, c.updated_at,
               pc.id AS component_id, pc.name AS component_name,
               pc.component_type, pc.summary AS component_summary,
               pc.ownership_level,
               p.id AS project_id, p.name AS project_name,
               p.project_type, p.summary AS project_summary,
               e.id AS experience_id, e.organization, e.title AS experience_title
        FROM claim c
        LEFT JOIN project_component pc ON pc.id = c.project_component_id
        LEFT JOIN project p ON p.id = pc.project_id
        LEFT JOIN experience e ON e.id = c.experience_id
        WHERE c.id = ?
        """,
        (claim_id,),
    ).fetchone()
    if row is None:
        raise KeyError(claim_id)
    result = dict(row)
    result["evidence"] = _evidence(connection, claim_id)
    result["metrics"] = _metrics(connection, claim_id)
    result["skills"] = _skills(connection, claim_id)
    result["eligibility"] = eligible_for_final_application(connection, row, context)
    return result


def _append_in_filter(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    values: Iterable[str] | None,
) -> None:
    normalized = tuple(value for value in (values or ()) if value)
    if not normalized:
        return
    placeholders = ", ".join("?" for _ in normalized)
    clauses.append(f"{column} IN ({placeholders})")
    parameters.extend(normalized)


def list_claims(
    connection: sqlite3.Connection,
    *,
    query: str | None = None,
    project: str | None = None,
    skill: str | None = None,
    tag: str | None = None,
    claim_types: Iterable[str] | None = None,
    verification_states: Iterable[str] | None = None,
    confidentiality: Iterable[str] | None = None,
    eligible_only: bool = False,
    context: EligibilityContext | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search normalized claims without relying on optional SQLite FTS5."""

    if limit < 1 or limit > 200:
        raise ValueError("Evidence query limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("Evidence query offset cannot be negative")

    clauses = ["1 = 1"]
    parameters: list[Any] = []
    if query:
        token = f"%{query.strip().casefold()}%"
        clauses.append(
            """(
                lower(c.claim_text) LIKE ? OR lower(pc.name) LIKE ? OR
                lower(coalesce(pc.summary, '')) LIKE ? OR lower(p.name) LIKE ? OR
                lower(coalesce(p.summary, '')) LIKE ? OR
                EXISTS (
                    SELECT 1 FROM claim_skill csq
                    JOIN skill sq ON sq.id = csq.skill_id
                    LEFT JOIN skill_alias saq ON saq.skill_id = sq.id
                    WHERE csq.claim_id = c.id
                      AND (lower(sq.canonical_name) LIKE ? OR lower(saq.alias) LIKE ?)
                )
            )"""
        )
        parameters.extend([token] * 7)
    if project:
        token = f"%{project.strip().casefold()}%"
        clauses.append("(p.id = ? OR lower(p.name) LIKE ?)")
        parameters.extend((project, token))
    if skill:
        normalized_skill = skill.strip().casefold()
        clauses.append(
            """EXISTS (
                SELECT 1 FROM claim_skill cs
                JOIN skill s ON s.id = cs.skill_id
                LEFT JOIN skill_alias sa ON sa.skill_id = s.id
                WHERE cs.claim_id = c.id
                  AND (lower(s.canonical_name) = ? OR lower(sa.alias) = ?)
            )"""
        )
        parameters.extend((normalized_skill, normalized_skill))
    if tag:
        clauses.append("lower(coalesce(p.summary, '')) LIKE ?")
        parameters.append(f'%"{tag.strip().casefold()}"%')
    _append_in_filter(clauses, parameters, "c.claim_type", claim_types)
    _append_in_filter(clauses, parameters, "c.verification_state", verification_states)
    _append_in_filter(clauses, parameters, "c.confidentiality", confidentiality)

    pagination = "" if eligible_only else "LIMIT ? OFFSET ?"
    query_parameters = parameters if eligible_only else [*parameters, limit, offset]
    rows = connection.execute(
        f"""
        SELECT DISTINCT c.id, p.name AS project_name, pc.name AS component_name,
                        c.claim_text
        FROM claim c
        LEFT JOIN project_component pc ON pc.id = c.project_component_id
        LEFT JOIN project p ON p.id = pc.project_id
        WHERE {' AND '.join(clauses)}
        ORDER BY coalesce(p.name, ''), coalesce(pc.name, ''), c.claim_text, c.id
        {pagination}
        """,
        query_parameters,
    ).fetchall()
    items = [get_claim(connection, row["id"], context=context) for row in rows]
    if eligible_only:
        items = [item for item in items if item["eligibility"]["allowed"]]
        items = items[offset : offset + limit]
    return items
