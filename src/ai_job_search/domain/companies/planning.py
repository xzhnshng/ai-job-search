"""Import user-authored Markdown target-company tables as reviewable plans."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ai_job_search.application.proposals import create_proposal


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
