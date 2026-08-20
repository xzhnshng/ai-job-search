from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.applications.service import (
    add_event,
    create_application,
    list_applications,
    timeline,
)
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]


class ApplicationTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)
        self.connection = connect(self.paths, self.config)
        apply_migrations(self.connection, self.workspace)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_application_event_updates_projection_and_keeps_history(self) -> None:
        application = create_application(
            self.connection,
            company_name="Example Labs",
            title="Senior Distributed Systems Engineer",
            role_track="distributed_systems",
            applied_date="2026-07-26",
        )
        updated = add_event(
            self.connection,
            application["id"],
            "interview_scheduled",
            occurred_at="2026-08-31T10:00:00-07:00",
            stage="interview",
            next_action="Prepare system design examples",
            next_action_date="2026-08-30",
            idempotency_key="example-interview-1",
        )
        self.assertEqual("interview", updated["current_stage"])
        self.assertEqual(2, updated["row_version"])
        self.assertEqual(2, len(timeline(self.connection, application["id"])))

    def test_duplicate_event_is_rejected(self) -> None:
        application = create_application(
            self.connection, company_name="Example Labs", title="ML Engineer"
        )
        kwargs = dict(
            occurred_at="2026-07-26T12:00:00-07:00",
            stage="applied",
            idempotency_key="submitted-once",
        )
        add_event(self.connection, application["id"], "application_submitted", **kwargs)
        with self.assertRaises(Exception):
            add_event(self.connection, application["id"], "application_submitted", **kwargs)
        self.assertEqual(2, len(timeline(self.connection, application["id"])))

    def test_filters_current_stage(self) -> None:
        create_application(
            self.connection,
            company_name="Example A",
            title="Backend Engineer",
            applied_date="2026-07-26",
        )
        create_application(
            self.connection,
            company_name="Example B",
            title="Researcher",
        )
        self.assertEqual(1, len(list_applications(self.connection, stage="applied")))


if __name__ == "__main__":
    unittest.main()
