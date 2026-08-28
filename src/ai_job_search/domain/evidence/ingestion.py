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


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _metric_value(metric: dict[str, Any]) -> str:
    if "before" in metric or "after" in metric:
        return f"{metric.get('before', '?')} -> {metric.get('after', '?')}"
    if "minimum" in metric or "maximum" in metric:
        return f"{metric.get('minimum', '?')}–{metric.get('maximum', '?')}"
    if "value" in metric:
        qualifier = str(metric.get("qualifier", "")).strip()
        value = str(metric["value"])
        return f"{qualifier} {value}".strip()
    details = {key: value for key, value in metric.items() if key not in {"name", "unit"}}
    if not details:
        raise ValueError("Metric must contain a value, range, transition, or contextual details")
    return json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_approval_payload(payload: dict[str, Any]) -> None:
    if payload.get("contract_version") != "1":
        raise ValueError("Unsupported extraction proposal contract version")
    if payload.get("experiences"):
        raise ValueError("Experience normalization is not implemented in this evidence slice")
    if payload.get("education"):
        raise ValueError("Education normalization is not implemented in this evidence slice")
    projects = payload.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("Evidence approval requires at least one project")
    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            raise ValueError(f"projects[{index}] must be an object")
        if not isinstance(project.get("name"), str) or not project["name"].strip():
            raise ValueError(f"projects[{index}].name must be a non-empty string")
        _string_list(project.get("ownership"), f"projects[{index}].ownership")
        _string_list(
            project.get("collaboration_boundaries"),
            f"projects[{index}].collaboration_boundaries",
        )
        _string_list(project.get("role_tags"), f"projects[{index}].role_tags")
        metrics = project.get("metrics", [])
        if not isinstance(metrics, list):
            raise ValueError(f"projects[{index}].metrics must be a list")
        for metric_index, metric in enumerate(metrics):
            if not isinstance(metric, dict):
                raise ValueError(f"projects[{index}].metrics[{metric_index}] must be an object")
            if not isinstance(metric.get("name"), str) or not metric["name"].strip():
                raise ValueError(
                    f"projects[{index}].metrics[{metric_index}].name must be a non-empty string"
                )
            _metric_value(metric)
    _string_list(payload.get("skills"), "skills")
    _string_list(payload.get("uncertainties"), "uncertainties")


def _insert_component(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    name: str,
    component_type: str,
    summary: str,
    ownership_level: str,
    now: str,
) -> str:
    component_id = new_id("component")
    connection.execute(
        """
        INSERT INTO project_component(
            id, project_id, name, component_type, summary, ownership_level,
            verification_state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?)
        """,
        (component_id, project_id, name, component_type, summary, ownership_level, now, now),
    )
    return component_id


def _insert_claim(
    connection: sqlite3.Connection,
    component_id: str,
    source_span_id: str,
    *,
    text: str,
    claim_type: str,
    now: str,
) -> str:
    claim_id = new_id("claim")
    connection.execute(
        """
        INSERT INTO claim(
            id, project_component_id, claim_text, claim_type, verification_state,
            wording_strength, confidentiality, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'verified', 'exact', 'private', ?, ?)
        """,
        (claim_id, component_id, text, claim_type, now, now),
    )
    connection.execute(
        """
        INSERT INTO claim_evidence(claim_id, source_span_id, support_type, confidence)
        VALUES (?, ?, 'candidate_attestation', 'candidate_confirmed')
        """,
        (claim_id, source_span_id),
    )
    return claim_id


def approve_evidence_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    actor: str = "user",
    reason: str | None = None,
) -> dict[str, Any]:
    """Atomically approve a reviewed project-evidence proposal and normalize its facts."""

    proposal = connection.execute(
        """
        SELECT id, state, payload_json, expected_versions_json
        FROM proposal
        WHERE id = ? AND proposal_type = 'career_evidence_extraction'
        """,
        (proposal_id,),
    ).fetchone()
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["state"] != "pending":
        raise ValueError(f"Proposal is already {proposal['state']}")
    payload = json.loads(proposal["payload_json"])
    expected_versions = json.loads(proposal["expected_versions_json"])
    _validate_approval_payload(payload)
    source_id = payload["source_id"]
    expected_sha = expected_versions.get("source_sha256")
    now = utc_now_text()
    counts = {
        "projects": 0,
        "components": 0,
        "claims": 0,
        "metrics": 0,
        "skills": 0,
        "evidence_links": 0,
    }

    with transaction(connection):
        current_proposal = connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        if current_proposal is None or current_proposal["state"] != "pending":
            state = current_proposal["state"] if current_proposal else "missing"
            raise ValueError(f"Proposal is already {state}")
        source = connection.execute(
            "SELECT id, relative_path, sha256 FROM source_artifact WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise ValueError("Evidence proposal source no longer exists")
        if not expected_sha or source["sha256"] != expected_sha:
            raise ValueError("Evidence proposal source changed after proposal creation")

        locator = json.dumps(
            {
                "relative_path": source["relative_path"],
                "scope": "whole_document",
                "source_sha256": source["sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        source_span_id = new_id("span")
        connection.execute(
            """
            INSERT INTO source_span(
                id, source_artifact_id, locator_type, locator_json, text_sha256, created_at
            ) VALUES (?, ?, 'whole_document', ?, ?, ?)
            """,
            (source_span_id, source_id, locator, source["sha256"], now),
        )

        for project in payload["projects"]:
            project_id = new_id("project")
            metadata = {
                "evidence_status": project.get("evidence_status"),
                "resume_metric_policy": project.get("resume_metric_policy"),
                "role_tags": project.get("role_tags", []),
            }
            connection.execute(
                """
                INSERT INTO project(
                    id, name, project_type, summary, confidentiality, verification_state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'private', 'verified', ?, ?)
                """,
                (
                    project_id,
                    project["name"].strip(),
                    project.get("project_key"),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            counts["projects"] += 1

            ownership = _string_list(project.get("ownership"), "ownership")
            if ownership:
                component_id = _insert_component(
                    connection,
                    project_id,
                    name="Ownership and delivery",
                    component_type="ownership",
                    summary="Candidate-confirmed personal ownership",
                    ownership_level="owner",
                    now=now,
                )
                counts["components"] += 1
                for statement in ownership:
                    _insert_claim(
                        connection,
                        component_id,
                        source_span_id,
                        text=statement,
                        claim_type="ownership",
                        now=now,
                    )
                    counts["claims"] += 1
                    counts["evidence_links"] += 1

            boundaries = _string_list(
                project.get("collaboration_boundaries"), "collaboration_boundaries"
            )
            if boundaries:
                component_id = _insert_component(
                    connection,
                    project_id,
                    name="Collaboration boundaries",
                    component_type="boundary",
                    summary="Candidate-confirmed teammate and collaboration boundaries",
                    ownership_level="collaborator",
                    now=now,
                )
                counts["components"] += 1
                for statement in boundaries:
                    _insert_claim(
                        connection,
                        component_id,
                        source_span_id,
                        text=statement,
                        claim_type="boundary",
                        now=now,
                    )
                    counts["claims"] += 1
                    counts["evidence_links"] += 1

            metrics = project.get("metrics", [])
            if metrics:
                component_id = _insert_component(
                    connection,
                    project_id,
                    name="Scale and outcomes",
                    component_type="impact",
                    summary="Candidate-confirmed metrics with semantic safeguards",
                    ownership_level="owner",
                    now=now,
                )
                counts["components"] += 1
                for metric in metrics:
                    value_text = _metric_value(metric)
                    unit = metric.get("unit")
                    claim_text = f"{metric['name'].replace('_', ' ')}: {value_text}"
                    if unit:
                        claim_text += f" {unit}"
                    claim_id = _insert_claim(
                        connection,
                        component_id,
                        source_span_id,
                        text=claim_text,
                        claim_type="metric",
                        now=now,
                    )
                    metric_id = new_id("metric")
                    context = {
                        key: value
                        for key, value in metric.items()
                        if key not in {"name", "value", "unit", "before", "after", "minimum", "maximum"}
                    }
                    connection.execute(
                        """
                        INSERT INTO metric(
                            id, name, value_text, unit, context, verification_state, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'verified', ?)
                        """,
                        (
                            metric_id,
                            metric["name"],
                            value_text,
                            unit,
                            json.dumps(context, ensure_ascii=False, sort_keys=True),
                            now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO claim_metric(claim_id, metric_id) VALUES (?, ?)",
                        (claim_id, metric_id),
                    )
                    counts["claims"] += 1
                    counts["metrics"] += 1
                    counts["evidence_links"] += 1

        for skill_name in _string_list(payload.get("skills"), "skills"):
            existing = connection.execute(
                "SELECT id FROM skill WHERE canonical_name = ?", (skill_name,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO skill(id, canonical_name, created_at) VALUES (?, ?, ?)",
                    (new_id("skill"), skill_name, now),
                )
                counts["skills"] += 1

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
                reason or "Candidate approved the reviewed evidence proposal",
                now,
            ),
        )

    return {
        "id": proposal_id,
        "state": "approved",
        "source_id": source_id,
        "decided_at": now,
        "created": counts,
    }
