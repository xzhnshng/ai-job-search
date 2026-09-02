from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.companies.planning import (
    approve_company_plan_proposal,
    create_company_plan_proposal,
    list_target_companies,
)
from ai_job_search.domain.companies.polling import poll_company_source
from ai_job_search.domain.companies.sources import (
    approve_company_source_enablement_proposal,
    approve_company_source_proposal,
    check_company_source_health,
    create_company_source_enablement_proposal,
    create_company_source_proposal,
)
from ai_job_search.domain.evidence.ingestion import register_source
from ai_job_search.domain.jobs.query import get_monitored_job, list_monitored_jobs
from ai_job_search.domain.jobs.report import build_daily_report
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.http.safe_client import SafeHttpResponse
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]
TABLE = """\
| Priority | Company | Best roles for you | ML/model proximity | My recommendation |
| --- | --- | --- | ---: | --- |
| **1** | **Example AI** | ML Infrastructure | ★★★★★ | Apply aggressively |
"""


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def fetch(self, url, *, allowed_hosts, accepted_content_types):  # noqa: ANN001
        return SafeHttpResponse(
            200,
            url,
            "application/json",
            json.dumps(self.payload).encode(),
        )


def ashby_job(identifier: str, title: str, *, published_at: str | None = None) -> dict:
    return {
        "id": identifier,
        "title": title,
        "jobUrl": f"https://jobs.ashbyhq.com/example-ai/{identifier}",
        "applyUrl": f"https://jobs.ashbyhq.com/example-ai/{identifier}/application",
        "location": "New York, NY",
        "descriptionPlain": f"Description for {title}",
        "publishedAt": published_at,
    }


class CompanyPollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        table = self.workspace / "documents" / "companies.md"
        table.parent.mkdir(parents=True)
        table.write_text(TABLE)
        self.paths = RuntimePaths.resolve(self.workspace)
        self.connection = connect(self.paths, load_config(self.workspace))
        apply_migrations(self.connection, self.workspace)
        artifact = register_source(
            self.connection, self.paths, table, source_type="target_company_plan"
        )
        plan = create_company_plan_proposal(
            self.connection, artifact["id"], "technology", table
        )
        approve_company_plan_proposal(self.connection, plan["id"])
        company = list_target_companies(self.connection)[0]
        source_proposal = create_company_source_proposal(
            self.connection,
            company_ref=company["id"],
            career_url="https://jobs.ashbyhq.com/example-ai",
            evidence_url="https://example.ai/careers",
            detection_note="Synthetic official link",
        )
        self.source = approve_company_source_proposal(
            self.connection, source_proposal["id"]
        )["company_source"]
        health_client = FakeClient({"jobs": []})
        check_company_source_health(
            self.connection, self.source["id"], client=health_client
        )
        enablement = create_company_source_enablement_proposal(
            self.connection, self.source["id"]
        )
        approve_company_source_enablement_proposal(
            self.connection, enablement["id"]
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_first_poll_is_entirely_baseline_existing(self) -> None:
        result = poll_company_source(
            self.connection,
            self.source["id"],
            client=FakeClient(
                {"jobs": [ashby_job("one", "Engineer"), ashby_job("two", "Scientist")]}
            ),
        )

        self.assertTrue(result["first_successful_poll"])
        self.assertEqual(2, result["counts"]["baseline_existing"])
        self.assertEqual(0, result["counts"]["new"])
        classifications = self.connection.execute(
            "SELECT classification FROM job_freshness ORDER BY classification"
        ).fetchall()
        self.assertEqual(
            ["baseline_existing", "baseline_existing"],
            [r[0] for r in classifications],
        )
        self.assertEqual(
            2,
            self.connection.execute("SELECT count(*) FROM job_snapshot").fetchone()[0],
        )
        self.assertEqual(
            2,
            self.connection.execute("SELECT count(*) FROM raw_observation").fetchone()[0],
        )
        listed = list_monitored_jobs(
            self.connection, classifications=("baseline_existing",)
        )
        self.assertEqual(2, len(listed))
        self.assertEqual("official", listed[0]["authority"])
        shown = get_monitored_job(self.connection, listed[0]["id"])
        self.assertEqual(listed[0]["id"], shown["job"]["id"])

    def test_incremental_poll_separates_new_updated_and_unchanged(self) -> None:
        first_jobs = [ashby_job("one", "Engineer"), ashby_job("two", "Scientist")]
        poll_company_source(
            self.connection, self.source["id"], client=FakeClient({"jobs": first_jobs})
        )
        second_jobs = [
            ashby_job("one", "Senior Engineer"),
            ashby_job("two", "Scientist"),
            ashby_job("three", "Research Engineer"),
        ]
        result = poll_company_source(
            self.connection, self.source["id"], client=FakeClient({"jobs": second_jobs})
        )

        self.assertFalse(result["first_successful_poll"])
        self.assertEqual(1, result["counts"]["new"])
        self.assertEqual(1, result["counts"]["updated"])
        self.assertEqual(1, result["counts"]["unchanged"])
        classifications = {
            row[0]: row[1]
            for row in self.connection.execute(
                """
                SELECT j.title, jf.classification
                FROM job_freshness jf JOIN job j ON j.id = jf.job_id
                WHERE jf.classification <> 'baseline_existing'
                """
            )
        }
        self.assertEqual("recently_updated", classifications["Senior Engineer"])
        self.assertEqual(
            "newly_detected_date_unknown", classifications["Research Engineer"]
        )
        self.assertEqual(
            4,
            self.connection.execute("SELECT count(*) FROM job_snapshot").fetchone()[0],
        )
        report = build_daily_report(self.connection, since="1970-01-01T00:00:00+00:00")
        self.assertEqual("not_implemented", report["ranking_status"])
        self.assertEqual(2, report["counts"]["reportable"])
        self.assertEqual(1, report["counts"]["new"])
        self.assertEqual(1, report["counts"]["updated"])
        self.assertFalse(
            any(
                item["classification"] == "baseline_existing"
                for group in report["groups"].values()
                for item in group
            )
        )

    def test_failed_poll_is_visible_and_does_not_create_observations(self) -> None:
        result = poll_company_source(
            self.connection, self.source["id"], client=FakeClient({"unexpected": []})
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual(
            1,
            self.connection.execute(
                "SELECT count(*) FROM source_run WHERE status = 'failed'"
            ).fetchone()[0],
        )
        self.assertEqual(0, self.connection.execute("SELECT count(*) FROM job").fetchone()[0])
        source = self.connection.execute(
            "SELECT health_status, enabled FROM company_source WHERE id = ?",
            (self.source["id"],),
        ).fetchone()
        self.assertEqual("degraded", source["health_status"])
        self.assertEqual(1, source["enabled"])

    def test_complete_snapshot_marks_missing_job_closed_and_return_reopened(self) -> None:
        jobs = [ashby_job("one", "Engineer"), ashby_job("two", "Scientist")]
        poll_company_source(
            self.connection, self.source["id"], client=FakeClient({"jobs": jobs})
        )
        second = poll_company_source(
            self.connection,
            self.source["id"],
            client=FakeClient({"jobs": [jobs[0]]}),
        )
        self.assertEqual(1, second["counts"]["closed"])
        states = {
            row[0]: row[1]
            for row in self.connection.execute(
                """
                SELECT external_job_id, current_open
                FROM job_source_ref ORDER BY external_job_id
                """
            )
        }
        self.assertEqual({"one": 1, "two": 0}, states)
        self.assertEqual(1, len(list_monitored_jobs(self.connection)))
        self.assertEqual(
            2, len(list_monitored_jobs(self.connection, open_only=False))
        )

        third = poll_company_source(
            self.connection, self.source["id"], client=FakeClient({"jobs": jobs})
        )
        self.assertEqual(1, third["counts"]["reopened"])
        self.assertEqual(
            1,
            self.connection.execute(
                "SELECT count(*) FROM job_freshness WHERE classification = 'reopened'"
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
