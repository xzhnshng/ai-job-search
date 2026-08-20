"""Workspace-scoped private runtime paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathPolicyError(ValueError):
    """Raised when a runtime path escapes the workspace."""


def find_workspace(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() and (candidate / "AGENTS.md").is_file():
            return candidate
    raise PathPolicyError("No AI-Driven Job Search workspace found")


def _assert_below(path: Path, root: Path, *, allow_root: bool = False) -> Path:
    resolved = path.resolve()
    resolved_root = root.resolve()
    if resolved == resolved_root and not allow_root:
        raise PathPolicyError(f"Refusing workspace-root target: {resolved}")
    if not resolved.is_relative_to(resolved_root):
        raise PathPolicyError(f"Path escapes workspace: {resolved}")
    return resolved


@dataclass(frozen=True)
class RuntimePaths:
    workspace: Path
    private_root: Path

    @classmethod
    def resolve(
        cls,
        workspace: Path | None = None,
        private_dir: str = ".ai-job-search",
    ) -> "RuntimePaths":
        root = find_workspace(workspace)
        raw_private = root / private_dir
        private = _assert_below(raw_private, root)
        return cls(workspace=root, private_root=private)

    @property
    def database(self) -> Path:
        return self.private_root / "state.sqlite3"

    @property
    def config(self) -> Path:
        return self.private_root / "config.toml"

    @property
    def backups(self) -> Path:
        return self.private_root / "backups"

    @property
    def reports(self) -> Path:
        return self.private_root / "reports"

    @property
    def exports(self) -> Path:
        return self.private_root / "exports"

    @property
    def logs(self) -> Path:
        return self.private_root / "logs"

    @property
    def source_cache(self) -> Path:
        return self.private_root / "source-cache"

    @property
    def raw_observations(self) -> Path:
        return self.private_root / "raw-observations"

    def initialize(self) -> None:
        directories = (
            self.private_root,
            self.backups,
            self.reports,
            self.exports,
            self.logs,
            self.source_cache,
            self.raw_observations,
        )
        for directory in directories:
            _assert_below(directory, self.workspace)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    def validate_owned_path(self, path: Path) -> Path:
        return _assert_below(path, self.private_root)
