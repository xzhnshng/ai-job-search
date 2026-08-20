"""Preview-first, workspace-scoped reset planning."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import database_files


class ResetError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResetTarget:
    path: str
    kind: str
    size_bytes: int


@dataclass(frozen=True)
class ResetPlan:
    scope: str
    targets: tuple[ResetTarget, ...]
    preserves: tuple[str, ...]
    requires_backup: bool
    plan_id: str
    confirmation: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def build_reset_plan(paths: RuntimePaths, scope: str) -> ResetPlan:
    if scope == "cache":
        selected = (
            paths.source_cache,
            paths.raw_observations,
            paths.reports,
            paths.exports,
            paths.logs,
        )
        preserves = ("database", "configuration", "backups", "source documents", "applications")
        backup = False
    elif scope == "database":
        selected = database_files(paths.database)
        preserves = ("configuration", "backups", "source documents", "applications", "exports")
        backup = True
    else:
        raise ResetError(f"Unsupported reset scope: {scope}")
    targets = tuple(
        ResetTarget(
            path=str(paths.validate_owned_path(path)),
            kind="directory" if path.is_dir() else "file",
            size_bytes=_size(path),
        )
        for path in selected
        if path.exists()
    )
    canonical = "\n".join(f"{item.path}:{item.size_bytes}" for item in targets)
    plan_id = hashlib.sha256(f"{scope}\n{canonical}".encode()).hexdigest()[:12]
    return ResetPlan(
        scope=scope,
        targets=targets,
        preserves=preserves,
        requires_backup=backup and bool(targets),
        plan_id=plan_id,
        confirmation=f"DELETE:{scope}:{plan_id}",
    )


def execute_reset(paths: RuntimePaths, plan: ResetPlan, confirmation: str) -> dict[str, object]:
    if confirmation != plan.confirmation:
        raise ResetError("Confirmation does not match the current reset plan")
    current = build_reset_plan(paths, plan.scope)
    if current.plan_id != plan.plan_id:
        raise ResetError("Reset plan is stale; preview again")
    removed: list[str] = []
    for item in current.targets:
        target = paths.validate_owned_path(Path(item.path))
        if target.is_dir():
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
        elif target.exists():
            target.unlink()
        removed.append(str(target))
    return {"scope": plan.scope, "removed": removed, "plan_id": plan.plan_id}
