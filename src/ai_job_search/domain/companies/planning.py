"""Import user-authored Markdown target-company tables as reviewable plans."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ai_job_search.application.proposals import create_proposal
from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.text import normalize_company_name
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction


def _clean(value: str) -> str:
    return value.strip().replace("**", "")


def _rating(value: str, symbol: str) -> float | None:
    cleaned = _clean(value)
    count = cleaned.count(symbol)
    if not count:
        return None
    return float(count) + (0.5 if "½" in cleaned else 0.0)


def parse_company_table(text: str, market: str, source_id: str) -> dict[str, Any]:
    if market not in {"technology", "trading"}:
        raise ValueError("market must be technology or trading")
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3:
        raise ValueError("No Markdown company table found")
    headers = [_clean(item).casefold() for item in rows[0].strip().strip("|").split("|")]
    companies: list[dict[str, Any]] = []
    for line in rows[2:]:
        cells = [_clean(item) for item in line.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            raise ValueError(f"Company row has {len(cells)} cells; expected {len(headers)}")
        data = dict(zip(headers, cells, strict=True))
        rank_text = data.get("rank") or data.get("priority") or ""
        match = re.search(r"\d+", rank_text)
        if not match:
            raise ValueError(f"Missing numeric rank in row: {line}")
        roles = data.get("best roles for you") or data.get("best target for you") or ""
        item = {
            "rank": int(match.group()),
            "company": data.get("company", "").strip(),
            "target_roles": roles.strip(),
            "ml_opportunity": _rating(
                data.get("ml/model proximity") or data.get("ml opportunity") or "", "★"
            ),
            "income_potential": _rating(data.get("income potential", ""), "$"),
            "fit": _rating(data.get("fit", ""), "★"),
            "recommendation": data.get("my recommendation") or None,
        }
        if not item["company"]:
            raise ValueError("Company name cannot be empty")
        companies.append(item)
    ranks = [item["rank"] for item in companies]
    if len(ranks) != len(set(ranks)):
        raise ValueError("Company plan contains duplicate ranks")
    return {
        "contract_version": "1",
        "market": market,
        "source_id": source_id,
        "companies": sorted(companies, key=lambda item: item["rank"]),
    }


def create_company_plan_proposal(
    connection: sqlite3.Connection,
    source_id: str,
    market: str,
    table_path: Path,
) -> dict[str, Any]:
    source = connection.execute(
        "SELECT sha256 FROM source_artifact WHERE id = ?", (source_id,)
    ).fetchone()
    if source is None:
        raise ValueError("Company plan references an unknown source")
    payload = parse_company_table(table_path.read_text(encoding="utf-8"), market, source_id)
    proposal = create_proposal(
        connection,
        "target_company_plan",
        payload,
        expected_versions={"source_sha256": source["sha256"]},
    )
    return {
        "id": proposal["id"],
        "state": proposal["state"],
        "market": market,
        "company_count": len(payload["companies"]),
        "companies": payload["companies"],
    }


def get_company_plan_proposal(connection: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, state, payload_json, payload_sha256, expected_versions_json,
               created_at, decided_at
        FROM proposal WHERE id = ? AND proposal_type = 'target_company_plan'
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    result["expected_versions"] = json.loads(result.pop("expected_versions_json"))
    return result


def list_company_plan_proposals(
    connection: sqlite3.Connection,
    *,
    state: str | None = None,
    market: str | None = None,
) -> list[dict[str, Any]]:
    if state is not None and state not in {"pending", "approved", "rejected"}:
        raise ValueError("state must be pending, approved, or rejected")
    if market is not None and market not in {"technology", "trading"}:
        raise ValueError("market must be technology or trading")
    clauses = ["p.proposal_type = 'target_company_plan'"]
    values: list[Any] = []
    if state:
        clauses.append("p.state = ?")
        values.append(state)
    rows = connection.execute(
        f"""
        SELECT p.id, p.state, p.payload_json, p.expected_versions_json,
               p.created_at, p.decided_at
        FROM proposal p
        WHERE {' AND '.join(clauses)}
        ORDER BY p.created_at DESC, p.id DESC
        """,
        values,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        if market and payload.get("market") != market:
            continue
        expected = json.loads(row["expected_versions_json"])
        source = connection.execute(
            "SELECT sha256 FROM source_artifact WHERE id = ?", (payload.get("source_id"),)
        ).fetchone()
        expected_sha = expected.get("source_sha256")
        items.append(
            {
                "id": row["id"],
                "state": row["state"],
                "market": payload.get("market"),
                "company_count": len(payload.get("companies", [])),
                "source_id": payload.get("source_id"),
                "source_current": bool(
                    source is not None and expected_sha and source["sha256"] == expected_sha
                ),
                "created_at": row["created_at"],
                "decided_at": row["decided_at"],
            }
        )
    return items


def _validate_company_plan(payload: dict[str, Any]) -> None:
    if payload.get("contract_version") != "1":
        raise ValueError("Unsupported company-plan contract version")
    if payload.get("market") not in {"technology", "trading"}:
        raise ValueError("Company-plan market must be technology or trading")
    companies = payload.get("companies")
    if not isinstance(companies, list) or not companies:
        raise ValueError("Company plan requires at least one company")
    ranks: set[int] = set()
    names: set[str] = set()
    for index, company in enumerate(companies):
        if not isinstance(company, dict):
            raise ValueError(f"companies[{index}] must be an object")
        name = company.get("company")
        normalized = normalize_company_name(name) if isinstance(name, str) else ""
        if not normalized:
            raise ValueError(f"companies[{index}].company must be non-empty")
        rank = company.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError(f"companies[{index}].rank must be a positive integer")
        if normalized in names:
            raise ValueError("Company plan contains duplicate normalized names")
        if rank in ranks:
            raise ValueError("Company plan contains duplicate ranks")
        names.add(normalized)
        ranks.add(rank)
        for field in ("ml_opportunity", "income_potential", "fit"):
            value = company.get(field)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                or value > 5
            ):
                raise ValueError(f"companies[{index}].{field} must be between 0 and 5")


def approve_company_plan_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    actor: str = "user",
    reason: str | None = None,
) -> dict[str, Any]:
    """Atomically replace one market's active target-company plan."""

    proposal = connection.execute(
        """
        SELECT state, payload_json, expected_versions_json
        FROM proposal
        WHERE id = ? AND proposal_type = 'target_company_plan'
        """,
        (proposal_id,),
    ).fetchone()
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["state"] != "pending":
        raise ValueError(f"Proposal is already {proposal['state']}")
    payload = json.loads(proposal["payload_json"])
    expected_versions = json.loads(proposal["expected_versions_json"])
    _validate_company_plan(payload)
    market = payload["market"]
    source_id = payload.get("source_id")
    expected_sha = expected_versions.get("source_sha256")
    now = utc_now_text()
    created_companies = 0
    updated_companies = 0

    with transaction(connection):
        current = connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        if current is None or current["state"] != "pending":
            state = current["state"] if current else "missing"
            raise ValueError(f"Proposal is already {state}")
        source = connection.execute(
            "SELECT sha256 FROM source_artifact WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise ValueError("Company-plan source no longer exists")
        if not expected_sha or source["sha256"] != expected_sha:
            raise ValueError("Company-plan source changed after proposal creation")

        previous = {
            row["company_id"]
            for row in connection.execute(
                "SELECT company_id FROM company_plan_entry WHERE market = ? AND active = 1",
                (market,),
            )
        }
        connection.execute(
            """
            UPDATE company_plan_entry
            SET active = 0, row_version = row_version + 1
            WHERE market = ? AND active = 1
            """,
            (market,),
        )
        selected_ids: set[str] = set()
        for item in payload["companies"]:
            name = item["company"].strip()
            normalized = normalize_company_name(name)
            row = connection.execute(
                "SELECT id FROM company WHERE normalized_name = ?", (normalized,)
            ).fetchone()
            if row is None:
                company_id = new_id("company")
                connection.execute(
                    """
                    INSERT INTO company(id, name, normalized_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (company_id, name, normalized, now),
                )
                created_companies += 1
            else:
                company_id = row["id"]
                connection.execute(
                    "UPDATE company SET name = ? WHERE id = ?", (name, company_id)
                )
                updated_companies += 1
            selected_ids.add(company_id)
            connection.execute(
                """
                INSERT INTO company_plan_entry(
                    company_id, market, priority_rank, target_roles,
                    ml_opportunity, income_potential, fit, recommendation,
                    active, source_artifact_id, source_sha256,
                    approved_proposal_id, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(company_id, market) DO UPDATE SET
                    priority_rank = excluded.priority_rank,
                    target_roles = excluded.target_roles,
                    ml_opportunity = excluded.ml_opportunity,
                    income_potential = excluded.income_potential,
                    fit = excluded.fit,
                    recommendation = excluded.recommendation,
                    active = 1,
                    source_artifact_id = excluded.source_artifact_id,
                    source_sha256 = excluded.source_sha256,
                    approved_proposal_id = excluded.approved_proposal_id,
                    approved_at = excluded.approved_at,
                    row_version = company_plan_entry.row_version + 1
                """,
                (
                    company_id,
                    market,
                    item["rank"],
                    item.get("target_roles", ""),
                    item.get("ml_opportunity"),
                    item.get("income_potential"),
                    item.get("fit"),
                    item.get("recommendation"),
                    source_id,
                    source["sha256"],
                    proposal_id,
                    now,
                ),
            )

        connection.execute(
            "UPDATE proposal SET state = 'approved', decided_at = ? WHERE id = ?",
            (now, proposal_id),
        )
        connection.execute(
            """
            INSERT INTO approval_event(id, proposal_id, decision, actor, reason, created_at)
            VALUES (?, ?, 'approved', ?, ?, ?)
            """,
            (
                new_id("approval"),
                proposal_id,
                actor,
                reason or "User approved the target-company plan",
                now,
            ),
        )

    return {
        "id": proposal_id,
        "state": "approved",
        "market": market,
        "company_count": len(payload["companies"]),
        "companies_created": created_companies,
        "companies_reused": updated_companies,
        "companies_deactivated": len(previous - selected_ids),
        "decided_at": now,
    }


def list_target_companies(
    connection: sqlite3.Connection,
    *,
    market: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    if market is not None and market not in {"technology", "trading"}:
        raise ValueError("market must be technology or trading")
    clauses: list[str] = []
    values: list[Any] = []
    if market:
        clauses.append("p.market = ?")
        values.append(market)
    if active_only:
        clauses.append("p.active = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT c.id, c.name, p.market, p.priority_rank, p.priority_tier,
                   p.target_roles, p.ml_opportunity, p.income_potential, p.fit,
                   p.recommendation, p.active, p.row_version,
                   count(cs.id) AS official_source_count,
                   CASE
                       WHEN count(cs.id) = 0 THEN 'not_registered'
                       WHEN count(DISTINCT cs.health_status) = 1 THEN max(cs.health_status)
                       ELSE 'mixed'
                   END AS source_health
            FROM company_plan_entry p
            JOIN company c ON c.id = p.company_id
            LEFT JOIN company_source cs ON cs.company_id = c.id
            {where}
            GROUP BY c.id, p.market
            ORDER BY p.market, p.priority_rank, c.name
            """,
            values,
        )
    ]


def get_target_company(connection: sqlite3.Connection, company_id: str) -> dict[str, Any]:
    rows = [
        item
        for item in list_target_companies(connection, active_only=False)
        if item["id"] == company_id
    ]
    if not rows:
        raise KeyError(company_id)
    result = {
        "id": rows[0]["id"],
        "name": rows[0]["name"],
        "plans": [
            {key: value for key, value in row.items() if key not in {"id", "name"}}
            for row in rows
        ],
    }
    result["sources"] = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, career_url, adapter_type, source_key, authority,
                   priority_tier, enabled, health_status, last_checked_at,
                   row_version, created_at, updated_at
            FROM company_source
            WHERE company_id = ?
            ORDER BY career_url
            """,
            (company_id,),
        )
    ]
    return result
