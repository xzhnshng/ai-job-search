"""AI-Driven Job Search command-line interface."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from ai_job_search import __version__
from ai_job_search.application.reset import build_reset_plan, execute_reset
from ai_job_search.contracts.envelope import Envelope, ErrorDetail
from ai_job_search.domain.applications.service import (
    add_event,
    create_application,
    get_application,
    list_applications,
    timeline,
)
from ai_job_search.domain.companies.planning import (
    create_company_plan_proposal,
    get_company_plan_proposal,
)
from ai_job_search.domain.evidence.inventory import inventory_sources
from ai_job_search.domain.evidence.ingestion import (
    approve_evidence_proposal,
    create_evidence_proposal,
    get_evidence_proposal,
    list_sources,
    register_source,
)
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.backup import create_backup
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import (
    apply_migrations,
    integrity,
    migration_status,
)
from ai_job_search.cli.presentation import render


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ai-job-search")
    root.add_argument("--json", action="store_true", dest="as_json")
    root.add_argument("--workspace", type=Path)
    sub = root.add_subparsers(dest="group", required=True)
    sub.add_parser("version")

    db = sub.add_parser("db")
    db_sub = db.add_subparsers(dest="action", required=True)
    for name in ("status", "init", "migrate", "integrity", "backup"):
        db_sub.add_parser(name)

    storage = sub.add_parser("storage")
    storage.add_subparsers(dest="action", required=True).add_parser("status")

    reset = sub.add_parser("reset")
    reset_sub = reset.add_subparsers(dest="action", required=True)
    plan = reset_sub.add_parser("plan")
    plan.add_argument("--scope", required=True, choices=("cache", "database"))
    execute = reset_sub.add_parser("execute")
    execute.add_argument("--scope", required=True, choices=("cache", "database"))
    execute.add_argument("--confirm", required=True)
    execute.add_argument("--skip-backup", action="store_true")

    applications = sub.add_parser("applications")
    app_sub = applications.add_subparsers(dest="action", required=True)
    app_list = app_sub.add_parser("list")
    app_list.add_argument("--stage")
    app_list.add_argument("--result")
    app_show = app_sub.add_parser("show")
    app_show.add_argument("application_id")
    app_timeline = app_sub.add_parser("timeline")
    app_timeline.add_argument("application_id")
    app_create = app_sub.add_parser("create")
    app_create.add_argument("--company", required=True)
    app_create.add_argument("--title", required=True)
    app_create.add_argument("--url")
    app_create.add_argument("--location")
    app_create.add_argument("--role-track")
    app_create.add_argument("--applied-date")
    app_event = app_sub.add_parser("event")
    app_event.add_argument("application_id")
    app_event.add_argument("--type", required=True, dest="event_type")
    app_event.add_argument("--occurred-at", required=True)
    app_event.add_argument("--stage")
    app_event.add_argument("--result")
    app_event.add_argument("--next-action")
    app_event.add_argument("--next-action-date")
    app_event.add_argument("--notes")
    app_event.add_argument("--idempotency-key", required=True)

    evidence = sub.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="action", required=True)
    evidence_sub.add_parser("inventory")
    evidence_ingest = evidence_sub.add_parser("ingest")
    evidence_ingest.add_argument("--path", type=Path, required=True)
    evidence_ingest.add_argument("--type", required=True, dest="source_type")
    evidence_sub.add_parser("sources")
    evidence_propose = evidence_sub.add_parser("propose")
    evidence_propose.add_argument("--input", type=Path, required=True)
    evidence_show = evidence_sub.add_parser("proposal-show")
    evidence_show.add_argument("proposal_id")
    evidence_approve = evidence_sub.add_parser("approve")
    evidence_approve.add_argument("proposal_id")
    evidence_approve.add_argument("--actor", default="user")
    evidence_approve.add_argument("--reason")

    companies = sub.add_parser("companies")
    company_sub = companies.add_subparsers(dest="action", required=True)
    company_import = company_sub.add_parser("plan-import")
    company_import.add_argument("--path", type=Path, required=True)
    company_import.add_argument("--source-id", required=True)
    company_import.add_argument("--market", choices=("technology", "trading"), required=True)
    company_show = company_sub.add_parser("plan-show")
    company_show.add_argument("proposal_id")
    return root


def _storage(paths: RuntimePaths, warning_mb: int) -> tuple[dict[str, object], tuple[str, ...]]:
    categories = {}
    for name, path in (
        ("database", paths.database),
        ("cache", paths.source_cache),
        ("raw_observations", paths.raw_observations),
        ("reports", paths.reports),
        ("exports", paths.exports),
        ("backups", paths.backups),
        ("logs", paths.logs),
    ):
        if path.is_file():
            size = path.stat().st_size
        elif path.is_dir():
            size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        else:
            size = 0
        categories[name] = size
    free = shutil.disk_usage(paths.workspace).free
    warnings = ()
    if free < warning_mb * 1024 * 1024:
        warnings = (f"Low disk space: {free // (1024 * 1024)} MB free",)
    return {
        "private_root": str(paths.private_root),
        "exists": paths.private_root.exists(),
        "bytes_by_category": categories,
        "total_bytes": sum(categories.values()),
        "disk_free_bytes": free,
    }, warnings


def dispatch(args: argparse.Namespace) -> Envelope:
    if args.group == "version":
        return Envelope("version", {"version": __version__})
    config = load_config(args.workspace)
    paths = RuntimePaths.resolve(args.workspace, config.runtime.private_dir)

    if args.group == "storage":
        data, warnings = _storage(paths, config.retention.warn_free_disk_mb)
        return Envelope("storage.status", data, warnings=warnings)

    if args.group == "db":
        if args.action == "status" and not paths.database.exists():
            return Envelope(
                "db.status",
                {"initialized": False, "database": str(paths.database), "pending": True},
                next_actions=("Run db init",),
            )
        if args.action == "backup":
            return Envelope("db.backup", create_backup(paths, config))
        connection = connect(paths, config, readonly=args.action in {"status", "integrity"})
        try:
            if args.action in {"init", "migrate"}:
                applied = apply_migrations(connection, paths.workspace)
                return Envelope(
                    f"db.{args.action}",
                    {
                        "database": str(paths.database),
                        "applied": applied,
                        **migration_status(connection, paths.workspace),
                    },
                )
            if args.action == "status":
                return Envelope(
                    "db.status",
                    {
                        "initialized": True,
                        "database": str(paths.database),
                        **migration_status(connection, paths.workspace),
                    },
                )
            if args.action == "integrity":
                check = integrity(connection)
                if not check["ok"]:
                    raise RuntimeError(f"Integrity check failed: {check}")
                return Envelope("db.integrity", check)
        finally:
            connection.close()
    if args.group == "reset":
        plan = build_reset_plan(paths, args.scope)
        if args.action == "plan":
            return Envelope("reset.plan", plan.to_dict())
        backup = None
        if plan.requires_backup and not args.skip_backup:
            backup = create_backup(paths, config)
        elif plan.requires_backup and args.skip_backup:
            raise ValueError("Database reset backup cannot be skipped in the initial release")
        result = execute_reset(paths, plan, args.confirm)
        if backup:
            result["backup"] = backup
        return Envelope("reset.execute", result)

    if args.group == "applications":
        connection = connect(paths, config)
        try:
            apply_migrations(connection, paths.workspace)
            if args.action == "list":
                items = list_applications(
                    connection, stage=args.stage, result=args.result
                )
                return Envelope("applications.list", {"items": items, "count": len(items)})
            if args.action == "show":
                return Envelope(
                    "applications.show",
                    {"application": get_application(connection, args.application_id)},
                )
            if args.action == "timeline":
                items = timeline(connection, args.application_id)
                return Envelope(
                    "applications.timeline", {"items": items, "count": len(items)}
                )
            if args.action == "create":
                item = create_application(
                    connection,
                    company_name=args.company,
                    title=args.title,
                    url=args.url,
                    location=args.location,
                    role_track=args.role_track,
                    applied_date=args.applied_date,
                )
                return Envelope("applications.create", {"application": item})
            if args.action == "event":
                item = add_event(
                    connection,
                    args.application_id,
                    args.event_type,
                    occurred_at=args.occurred_at,
                    stage=args.stage,
                    result=args.result,
                    next_action=args.next_action,
                    next_action_date=args.next_action_date,
                    notes=args.notes,
                    idempotency_key=args.idempotency_key,
                )
                return Envelope("applications.event", {"application": item})
        finally:
            connection.close()

    if args.group == "evidence":
        if args.action == "inventory":
            data = inventory_sources(paths.workspace)
            next_actions = ()
            if not data["ready_for_ingestion"]:
                next_actions = (
                    "Add the Overleaf source project and compiled PDF under documents/cv/",
                    "Add 3–5 project folders or documents under documents/projects/",
                )
            return Envelope("evidence.inventory", data, next_actions=next_actions)
        connection = connect(paths, config)
        try:
            apply_migrations(connection, paths.workspace)
            if args.action == "ingest":
                source_path = args.path
                if not source_path.is_absolute():
                    source_path = paths.workspace / source_path
                item = register_source(
                    connection, paths, source_path, source_type=args.source_type
                )
                return Envelope(
                    "evidence.ingest",
                    {"source": item},
                    next_actions=(
                        "Review a structured career-fact proposal before approving claims",
                    ),
                )
            if args.action == "sources":
                items = list_sources(connection)
                return Envelope("evidence.sources", {"items": items, "count": len(items)})
            if args.action == "propose":
                input_path = args.input
                if not input_path.is_absolute():
                    input_path = paths.workspace / input_path
                payload = json.loads(input_path.read_text(encoding="utf-8"))
                result = create_evidence_proposal(connection, payload)
                return Envelope(
                    "evidence.propose",
                    {"proposal": result},
                    next_actions=(
                        "Review the full proposal",
                        "Approve or reject it before committing career facts",
                    ),
                )
            if args.action == "proposal-show":
                return Envelope(
                    "evidence.proposal-show",
                    {"proposal": get_evidence_proposal(connection, args.proposal_id)},
                )
            if args.action == "approve":
                result = approve_evidence_proposal(
                    connection,
                    args.proposal_id,
                    actor=args.actor,
                    reason=args.reason,
                )
                return Envelope(
                    "evidence.approve",
                    {"proposal": result},
                    next_actions=(
                        "Use approved project components and metrics when tailoring applications",
                    ),
                )
        finally:
            connection.close()

    if args.group == "companies":
        connection = connect(paths, config)
        try:
            apply_migrations(connection, paths.workspace)
            if args.action == "plan-import":
                table_path = args.path
                if not table_path.is_absolute():
                    table_path = paths.workspace / table_path
                resolved = table_path.resolve()
                documents = (paths.workspace / "documents").resolve()
                if not resolved.is_file() or not resolved.is_relative_to(documents):
                    raise ValueError("Company plan must be a file below documents/")
                result = create_company_plan_proposal(
                    connection,
                    args.source_id,
                    args.market,
                    resolved,
                )
                return Envelope(
                    "companies.plan-import",
                    {"proposal": result},
                    next_actions=(
                        "Review ranks and role targets",
                        "Assign polling tiers when the official-source registry is implemented",
                    ),
                )
            if args.action == "plan-show":
                return Envelope(
                    "companies.plan-show",
                    {"proposal": get_company_plan_proposal(connection, args.proposal_id)},
                )
        finally:
            connection.close()

    raise ValueError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        envelope = dispatch(args)
        code = 0
    except Exception as exc:
        envelope = Envelope(
            command=".".join(filter(None, (getattr(args, "group", None), getattr(args, "action", None)))),
            error=ErrorDetail(type(exc).__name__.upper(), str(exc)),
        )
        code = 1
    stream = sys.stdout if code == 0 else sys.stderr
    stream.write(render(envelope, as_json=args.as_json) + "\n")
    return code
