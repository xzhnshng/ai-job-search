from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_job_search.application.proposals import create_proposal, decide_proposal
from ai_job_search.application.reset import ResetError, build_reset_plan, execute_reset
from ai_job_search.cli.main import main
from ai_job_search.infrastructure.files.artifact_store import store_artifact
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import PathPolicyError, RuntimePaths
from ai_job_search.infrastructure.sqlite.backup import create_backup
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import (
    MigrationError,
    apply_migrations,
    integrity,
    migration_status,
)


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n", encoding="utf-8")
        (self.workspace / "config").mkdir()
        shutil.copy(ROOT / "config" / "defaults.toml", self.workspace / "config")
        (self.workspace / "migrations").mkdir()
        shutil.copy(ROOT / "migrations" / "0001_foundation.sql", self.workspace / "migrations")
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def initialize_database(self) -> sqlite3.Connection:
        connection = connect(self.paths, self.config)
        apply_migrations(connection, self.workspace)
        return connection


class RuntimePathTests(WorkspaceTestCase):
    def test_initialization_stays_private(self) -> None:
        self.paths.initialize()
        self.assertTrue(self.paths.private_root.is_dir())
        self.assertTrue(self.paths.database.parent.is_relative_to(self.workspace.resolve()))

    def test_external_private_path_is_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            RuntimePaths.resolve(self.workspace, "../outside")

    def test_workspace_root_cannot_be_private_root(self) -> None:
        with self.assertRaises(PathPolicyError):
            RuntimePaths.resolve(self.workspace, ".")


class ConfigTests(WorkspaceTestCase):
    def test_config_hash_is_stable(self) -> None:
        self.assertEqual(self.config.sha256, load_config(self.workspace).sha256)

    def test_private_override_is_validated(self) -> None:
        self.paths.initialize()
        self.paths.config.write_text("[database]\nbusy_timeout_ms = -1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            load_config(self.workspace)


class MigrationTests(WorkspaceTestCase):
    def test_empty_database_initializes_idempotently(self) -> None:
        connection = connect(self.paths, self.config)
        try:
            self.assertEqual(["0001_foundation.sql"], apply_migrations(connection, self.workspace))
            self.assertEqual([], apply_migrations(connection, self.workspace))
            self.assertTrue(integrity(connection)["ok"])
            self.assertEqual(
                "0001_foundation.sql",
                migration_status(connection, self.workspace)["current"],
            )
        finally:
            connection.close()

    def test_changed_applied_migration_is_rejected(self) -> None:
        connection = self.initialize_database()
        try:
            migration = self.workspace / "migrations" / "0001_foundation.sql"
            migration.write_text(migration.read_text() + "\n-- changed\n", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "changed"):
                apply_migrations(connection, self.workspace)
        finally:
            connection.close()


class GovernanceAndArtifactTests(WorkspaceTestCase):
    def test_proposal_has_single_decision(self) -> None:
        connection = self.initialize_database()
        try:
            proposal = create_proposal(connection, "profile.change", {"name": "Example"})
            result = decide_proposal(connection, proposal["id"], "approved")
            self.assertEqual("approved", result["state"])
            with self.assertRaisesRegex(ValueError, "already"):
                decide_proposal(connection, proposal["id"], "rejected")
        finally:
            connection.close()

    def test_artifact_is_atomic_and_registered(self) -> None:
        connection = self.initialize_database()
        try:
            result = store_artifact(
                connection,
                self.paths,
                "reports/example.md",
                b"example\n",
                artifact_type="report",
            )
            self.assertEqual(b"example\n", Path(result["path"]).read_bytes())
            row = connection.execute("SELECT sha256 FROM artifact").fetchone()
            self.assertEqual(result["sha256"], row["sha256"])
        finally:
            connection.close()


class BackupAndResetTests(WorkspaceTestCase):
    def test_backup_is_verified(self) -> None:
        connection = self.initialize_database()
        connection.close()
        result = create_backup(self.paths, self.config)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertTrue(Path(result["manifest"]).is_file())

    def test_reset_preview_does_not_mutate(self) -> None:
        self.paths.initialize()
        cache_file = self.paths.source_cache / "cached.txt"
        cache_file.write_text("cached", encoding="utf-8")
        plan = build_reset_plan(self.paths, "cache")
        self.assertTrue(cache_file.exists())
        with self.assertRaises(ResetError):
            execute_reset(self.paths, plan, "wrong")
        self.assertTrue(cache_file.exists())
        result = execute_reset(self.paths, plan, plan.confirmation)
        self.assertIn(str(self.paths.source_cache), result["removed"])
        self.assertFalse(cache_file.exists())


class CliTests(WorkspaceTestCase):
    def call(self, *arguments: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--json", "--workspace", str(self.workspace), *arguments])
        output = stdout.getvalue() if code == 0 else stderr.getvalue()
        return code, json.loads(output)

    def test_status_before_and_after_init(self) -> None:
        code, before = self.call("db", "status")
        self.assertEqual(0, code)
        self.assertFalse(before["data"]["initialized"])
        code, initialized = self.call("db", "init")
        self.assertEqual(0, code)
        self.assertEqual(["0001_foundation.sql"], initialized["data"]["applied"])
        code, after = self.call("db", "integrity")
        self.assertEqual(0, code)
        self.assertTrue(after["data"]["ok"])

    def test_error_is_json_on_stderr(self) -> None:
        code, result = self.call("db", "backup")
        self.assertEqual(1, code)
        self.assertEqual("FILENOTFOUNDERROR", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
