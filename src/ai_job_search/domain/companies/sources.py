"""Proposal-gated registration of official company career sources."""

from __future__ import annotations

import ipaddress
import json
import sqlite3
from html.parser import HTMLParser
from typing import Any
from urllib.parse import SplitResult, quote, urljoin, urlsplit, urlunsplit

from ai_job_search.application.proposals import create_proposal
from ai_job_search.domain.common.ids import new_id
from ai_job_search.domain.common.text import normalize_company_name
from ai_job_search.domain.common.time import utc_now_text
from ai_job_search.infrastructure.sqlite.unit_of_work import transaction
from ai_job_search.infrastructure.http.safe_client import SafeHttpClient


ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.smartrecruiters.com": "smartrecruiters",
}
ADAPTER_TYPES = frozenset({*ATS_HOSTS.values(), "custom", "unsupported"})


class _AtsLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.links.append(value)


def detect_linked_ats_sources(
    connection: sqlite3.Connection,
    source_id: str,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """Read one official page and return deduplicated supported ATS candidates."""
    source = connection.execute(
        """
        SELECT cs.id, cs.company_id, cs.career_url, c.name AS company_name
        FROM company_source cs JOIN company c ON c.id = cs.company_id
        WHERE cs.id = ?
        """,
        (source_id,),
    ).fetchone()
    if source is None:
        raise KeyError(source_id)
    host = urlsplit(source["career_url"]).hostname
    if not host:
        raise ValueError("Registered source URL has no hostname")
    response = (client or SafeHttpClient()).fetch(
        source["career_url"],
        allowed_hosts={host},
        accepted_content_types={"text/html"},
    )
    parser = _AtsLinkParser()
    parser.feed(response.body.decode("utf-8", errors="replace"))
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for href in parser.links:
        absolute = urljoin(response.url, href)
        parsed = urlsplit(absolute)
        candidate_host = (parsed.hostname or "").casefold().rstrip(".")
        adapter = ATS_HOSTS.get(candidate_host)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not adapter or not segments:
            continue
        key = segments[0]
        career_url = f"https://{candidate_host}/{key}"
        candidates[(adapter, key)] = {
            "adapter_type": adapter,
            "source_key": key,
            "career_url": career_url,
            "evidence_url": source["career_url"],
        }
    return {
        "company_source_id": source_id,
        "company_id": source["company_id"],
        "company_name": source["company_name"],
        "official_page": source["career_url"],
        "candidates": sorted(
            candidates.values(), key=lambda item: (item["adapter_type"], item["source_key"])
        ),
    }


def _health_endpoint(source: sqlite3.Row) -> tuple[str, frozenset[str], tuple[str, ...]]:
    adapter = source["adapter_type"]
    key = quote(source["source_key"] or "", safe="")
    if adapter == "ashby":
        return (
            f"https://api.ashbyhq.com/posting-api/job-board/{key}",
            frozenset({"api.ashbyhq.com"}),
            ("application/json",),
        )
    if adapter == "greenhouse":
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{key}/jobs?content=true",
            frozenset({"boards-api.greenhouse.io"}),
            ("application/json",),
        )
    if adapter == "lever":
        return (
            f"https://api.lever.co/v0/postings/{key}?mode=json",
            frozenset({"api.lever.co"}),
            ("application/json",),
        )
    if adapter == "smartrecruiters":
        return (
            f"https://api.smartrecruiters.com/v1/companies/{key}/postings",
            frozenset({"api.smartrecruiters.com"}),
            ("application/json",),
        )
    host = urlsplit(source["career_url"]).hostname
    if not host:
        raise ValueError("Registered source URL has no hostname")
    return source["career_url"], frozenset({host.casefold()}), ("text/html",)


def _validate_health_payload(adapter: str, body: bytes) -> tuple[str, int | None, str]:
    if adapter == "custom":
        if not body.strip():
            raise ValueError("Career page returned an empty response")
        return "reachable_unverified", None, "Career page is reachable; no ATS schema verified"
    if adapter == "unsupported":
        return "unsupported", None, "No supported polling adapter is configured"
    payload = json.loads(body)
    if adapter == "ashby":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
    elif adapter == "greenhouse":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
    elif adapter == "lever":
        jobs = payload if isinstance(payload, list) else None
    elif adapter == "smartrecruiters":
        jobs = payload.get("content") if isinstance(payload, dict) else None
    else:
        jobs = None
    if not isinstance(jobs, list):
        raise ValueError(f"{adapter} response did not match the expected jobs schema")
    return "healthy", len(jobs), f"Verified {adapter} jobs schema"


def check_company_source_health(
    connection: sqlite3.Connection,
    source_id: str,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """Run and persist one bounded health check without enabling the source."""
    source = connection.execute(
        """
        SELECT id, company_id, career_url, adapter_type, source_key, enabled,
               health_status, row_version
        FROM company_source WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    if source is None:
        raise KeyError(source_id)

    run_id = new_id("source_run")
    now = utc_now_text()
    endpoint, allowed_hosts, content_types = _health_endpoint(source)
    status = "failed"
    health_status = "failed"
    record_count = None
    error_code = None
    error_message = None
    detail: dict[str, Any] = {
        "contract_version": "1",
        "adapter_type": source["adapter_type"],
        "endpoint_host": urlsplit(endpoint).hostname,
    }

    try:
        if source["adapter_type"] == "unsupported":
            health_status, record_count, message = _validate_health_payload(
                source["adapter_type"], b""
            )
            detail["message"] = message
            status = "completed"
        else:
            response = (client or SafeHttpClient()).fetch(
                endpoint,
                allowed_hosts=allowed_hosts,
                accepted_content_types=content_types,
            )
            if not 200 <= response.status < 300:
                raise ValueError(f"Source returned HTTP {response.status}")
            health_status, record_count, message = _validate_health_payload(
                source["adapter_type"], response.body
            )
            detail.update(
                {
                    "message": message,
                    "http_status": response.status,
                    "content_type": response.content_type,
                    "response_bytes": len(response.body),
                }
            )
            status = "completed"
    except Exception as exc:  # Persist health failures instead of losing diagnostic state.
        error_code = type(exc).__name__.upper()
        error_message = str(exc)[:500]
        detail["message"] = error_message

    completed_at = utc_now_text()
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO source_run(
                id, company_source_id, idempotency_key, status, started_at,
                completed_at, record_count, error_code, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_id,
                f"health:{run_id}",
                status,
                now,
                completed_at,
                record_count,
                error_code,
                error_message,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_health_event(
                id, company_source_id, source_run_id, health_status,
                detail_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("source_health"),
                source_id,
                run_id,
                health_status,
                json.dumps(detail, sort_keys=True),
                completed_at,
            ),
        )
        connection.execute(
            """
            UPDATE company_source
            SET health_status = ?, last_checked_at = ?, updated_at = ?,
                row_version = row_version + 1
            WHERE id = ?
            """,
            (health_status, completed_at, completed_at, source_id),
        )

    return {
        "source_id": source_id,
        "run_id": run_id,
        "run_status": status,
        "health_status": health_status,
        "record_count": record_count,
        "enabled": bool(source["enabled"]),
        "checked_at": completed_at,
        "detail": detail,
        "error": (
            {"code": error_code, "message": error_message} if error_code else None
        ),
    }


def create_company_source_enablement_proposal(
    connection: sqlite3.Connection, source_id: str
) -> dict[str, Any]:
    source = connection.execute(
        """
        SELECT cs.id, cs.company_id, cs.career_url, cs.adapter_type,
               cs.source_key, cs.enabled, cs.health_status, cs.last_checked_at,
               cs.row_version, c.name AS company_name
        FROM company_source cs
        JOIN company c ON c.id = cs.company_id
        WHERE cs.id = ?
        """,
        (source_id,),
    ).fetchone()
    if source is None:
        raise KeyError(source_id)
    if source["enabled"]:
        raise ValueError("Company source is already enabled")
    if source["adapter_type"] not in ATS_HOSTS.values():
        raise ValueError("Company source has no supported polling adapter")
    if source["health_status"] != "healthy" or not source["last_checked_at"]:
        raise ValueError("Company source requires a successful health check")
    pending = connection.execute(
        """
        SELECT id FROM proposal
        WHERE proposal_type = 'company_source_enablement' AND state = 'pending'
          AND json_extract(payload_json, '$.company_source_id') = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    if pending is not None:
        raise ValueError(f"Pending enablement proposal already exists: {pending['id']}")

    payload = {
        "contract_version": "1",
        "company_source_id": source["id"],
        "company_id": source["company_id"],
        "company_name": source["company_name"],
        "career_url": source["career_url"],
        "adapter_type": source["adapter_type"],
        "source_key": source["source_key"],
        "health_status": source["health_status"],
        "health_checked_at": source["last_checked_at"],
        "requested_enabled": True,
    }
    proposal = create_proposal(
        connection,
        "company_source_enablement",
        payload,
        expected_versions={"company_source_row_version": source["row_version"]},
    )
    return {"id": proposal["id"], "state": proposal["state"], **payload}


def get_company_source_enablement_proposal(
    connection: sqlite3.Connection, proposal_id: str
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, state, payload_json, expected_versions_json, created_at, decided_at
        FROM proposal
        WHERE id = ? AND proposal_type = 'company_source_enablement'
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    result["expected_versions"] = json.loads(result.pop("expected_versions_json"))
    return result


def approve_company_source_enablement_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    actor: str = "user",
    reason: str | None = None,
) -> dict[str, Any]:
    proposal = get_company_source_enablement_proposal(connection, proposal_id)
    if proposal["state"] != "pending":
        raise ValueError(f"Proposal is already {proposal['state']}")
    payload = proposal["payload"]
    expected_version = proposal["expected_versions"]["company_source_row_version"]
    now = utc_now_text()

    with transaction(connection):
        current_proposal = connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        if current_proposal is None or current_proposal["state"] != "pending":
            state = current_proposal["state"] if current_proposal else "missing"
            raise ValueError(f"Proposal is already {state}")
        source = connection.execute(
            """
            SELECT enabled, health_status, last_checked_at, row_version, adapter_type
            FROM company_source WHERE id = ?
            """,
            (payload["company_source_id"],),
        ).fetchone()
        if source is None:
            raise ValueError("Company source no longer exists")
        if source["row_version"] != expected_version:
            raise ValueError("Company source changed after enablement proposal creation")
        if source["enabled"]:
            raise ValueError("Company source is already enabled")
        if source["health_status"] != "healthy" or not source["last_checked_at"]:
            raise ValueError("Company source no longer has healthy evidence")
        if source["adapter_type"] not in ATS_HOSTS.values():
            raise ValueError("Company source no longer has a supported polling adapter")

        connection.execute(
            """
            UPDATE company_source
            SET enabled = 1, updated_at = ?, row_version = row_version + 1
            WHERE id = ?
            """,
            (now, payload["company_source_id"]),
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
                reason or "User approved official company-source monitoring",
                now,
            ),
        )

    return {
        "id": proposal_id,
        "state": "approved",
        "company_source_id": payload["company_source_id"],
        "enabled": True,
        "decided_at": now,
    }


def normalize_career_url(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() != "https":
        raise ValueError("Official career sources must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Official career source URL has an invalid authority")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Local hosts cannot be registered as career sources")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Non-public IP addresses cannot be career sources")
    if parsed.port not in {None, 443}:
        raise ValueError("Official career source URL cannot use a custom port")
    if parsed.query or parsed.fragment:
        raise ValueError("Official career source URL cannot contain query or fragment data")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(SplitResult("https", hostname, path, "", ""))


def detect_adapter(career_url: str) -> tuple[str, str | None]:
    parsed = urlsplit(career_url)
    adapter_type = ATS_HOSTS.get(parsed.hostname or "", "custom")
    segments = [segment for segment in parsed.path.split("/") if segment]
    source_key = segments[0] if adapter_type != "custom" and segments else None
    if adapter_type != "custom" and not source_key:
        raise ValueError("ATS career source URL must include its organization key")
    return adapter_type, source_key


def _resolve_company(connection: sqlite3.Connection, company_ref: str) -> dict[str, Any]:
    normalized = normalize_company_name(company_ref)
    rows = connection.execute(
        """
        SELECT c.id, c.name, p.market, p.row_version, p.priority_tier
        FROM company c
        JOIN company_plan_entry p ON p.company_id = c.id AND p.active = 1
        WHERE c.id = ? OR c.normalized_name = ?
        ORDER BY p.market
        """,
        (company_ref, normalized),
    ).fetchall()
    if not rows:
        raise KeyError(company_ref)
    return {
        "id": rows[0]["id"],
        "name": rows[0]["name"],
        "plan_versions": {row["market"]: row["row_version"] for row in rows},
        "priority_tier": next(
            (row["priority_tier"] for row in rows if row["priority_tier"]), None
        ),
    }


def create_company_source_proposal(
    connection: sqlite3.Connection,
    *,
    company_ref: str,
    career_url: str,
    evidence_url: str,
    detection_note: str,
    adapter_type: str | None = None,
    source_key: str | None = None,
    priority_tier: str | None = None,
) -> dict[str, Any]:
    company = _resolve_company(connection, company_ref)
    normalized_url = normalize_career_url(career_url)
    normalized_evidence_url = normalize_career_url(evidence_url)
    detected_adapter, detected_key = detect_adapter(normalized_url)
    selected_adapter = adapter_type or detected_adapter
    selected_key = source_key or detected_key
    if selected_adapter not in ADAPTER_TYPES:
        raise ValueError(f"Unsupported company-source adapter: {selected_adapter}")
    if selected_adapter != detected_adapter and selected_adapter != "unsupported":
        raise ValueError(
            f"Adapter {selected_adapter} does not match URL-detected adapter {detected_adapter}"
        )
    if selected_adapter in ATS_HOSTS.values() and not selected_key:
        raise ValueError("ATS company source requires an organization key")
    if priority_tier is not None and priority_tier not in {"A", "B", "C"}:
        raise ValueError("priority tier must be A, B, or C")
    if not detection_note.strip():
        raise ValueError("Official-source proposal requires a detection note")
    duplicate = connection.execute(
        "SELECT id FROM company_source WHERE company_id = ? AND career_url = ?",
        (company["id"], normalized_url),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("Official company source is already registered")

    payload = {
        "contract_version": "1",
        "company_id": company["id"],
        "company_name": company["name"],
        "career_url": normalized_url,
        "evidence_url": normalized_evidence_url,
        "detection_note": detection_note.strip(),
        "adapter_type": selected_adapter,
        "source_key": selected_key,
        "priority_tier": priority_tier or company["priority_tier"],
        "enable_after_approval": False,
    }
    proposal = create_proposal(
        connection,
        "company_source_registration",
        payload,
        expected_versions={"company_plan_versions": company["plan_versions"]},
    )
    return {
        "id": proposal["id"],
        "state": proposal["state"],
        **payload,
    }


def get_company_source_proposal(
    connection: sqlite3.Connection, proposal_id: str
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, state, payload_json, expected_versions_json, created_at, decided_at
        FROM proposal
        WHERE id = ? AND proposal_type = 'company_source_registration'
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    result["expected_versions"] = json.loads(result.pop("expected_versions_json"))
    return result


def approve_company_source_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    actor: str = "user",
    reason: str | None = None,
) -> dict[str, Any]:
    proposal = get_company_source_proposal(connection, proposal_id)
    if proposal["state"] != "pending":
        raise ValueError(f"Proposal is already {proposal['state']}")
    payload = proposal["payload"]
    expected = proposal["expected_versions"].get("company_plan_versions", {})
    normalized_url = normalize_career_url(payload["career_url"])
    detected_adapter, detected_key = detect_adapter(normalized_url)
    if payload["adapter_type"] not in {detected_adapter, "unsupported"}:
        raise ValueError("Company-source adapter no longer matches its URL")
    if payload["adapter_type"] != "unsupported" and payload.get("source_key") != detected_key:
        raise ValueError("Company-source key no longer matches its URL")
    now = utc_now_text()
    source_id = new_id("company_source")

    with transaction(connection):
        current = connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        if current is None or current["state"] != "pending":
            state = current["state"] if current else "missing"
            raise ValueError(f"Proposal is already {state}")
        plans = {
            row["market"]: row["row_version"]
            for row in connection.execute(
                """
                SELECT market, row_version
                FROM company_plan_entry
                WHERE company_id = ? AND active = 1
                """,
                (payload["company_id"],),
            )
        }
        if plans != expected:
            raise ValueError("Company plan changed after source proposal creation")
        duplicate = connection.execute(
            "SELECT id FROM company_source WHERE company_id = ? AND career_url = ?",
            (payload["company_id"], normalized_url),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("Official company source is already registered")
        connection.execute(
            """
            INSERT INTO company_source(
                id, company_id, career_url, adapter_type, source_key, authority,
                priority_tier, enabled, health_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'official', ?, 0, 'not_checked', ?, ?)
            """,
            (
                source_id,
                payload["company_id"],
                normalized_url,
                payload["adapter_type"],
                payload.get("source_key"),
                payload.get("priority_tier"),
                now,
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
                reason or "User approved official company-source registration",
                now,
            ),
        )

    return {
        "id": proposal_id,
        "state": "approved",
        "company_source": {
            "id": source_id,
            "company_id": payload["company_id"],
            "career_url": normalized_url,
            "adapter_type": payload["adapter_type"],
            "source_key": payload.get("source_key"),
            "priority_tier": payload.get("priority_tier"),
            "enabled": False,
            "health_status": "not_checked",
        },
        "decided_at": now,
    }
