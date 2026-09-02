from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.companies.planning import (
    approve_company_plan_proposal,
    create_company_plan_proposal,
    list_target_companies,
)
from ai_job_search.domain.companies.sources import (
    approve_company_source_enablement_proposal,
    approve_company_source_proposal,
    check_company_source_health,
    create_company_source_enablement_proposal,
    create_company_source_proposal,
    detect_linked_ats_sources,
    detect_adapter,
    get_company_source_proposal,
    normalize_career_url,
)
from ai_job_search.infrastructure.http.safe_client import SafeHttpResponse
from ai_job_search.domain.evidence.ingestion import register_source
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]
TABLE = """\
| Priority | Company | Best roles for you | ML/model proximity | My recommendation |
| --- | --- | --- | ---: | --- |
| **1** | **Example AI** | Inference / ML Infrastructure | ★★★★★ | Apply aggressively |
"""


class CompanySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        self.table = self.workspace / "documents" / "plans" / "companies.md"
        self.table.parent.mkdir(parents=True)
        self.table.write_text(TABLE)
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)
        self.connection = connect(self.paths, self.config)
        apply_migrations(self.connection, self.workspace)
        source = register_source(
            self.connection,
            self.paths,
            self.table,
            source_type="target_company_plan",
        )
        proposal = create_company_plan_proposal(
            self.connection, source["id"], "technology", self.table
        )
        approve_company_plan_proposal(self.connection, proposal["id"])
        self.company = list_target_companies(self.connection)[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _source_proposal(self) -> dict[str, object]:
        return create_company_source_proposal(
            self.connection,
            company_ref=self.company["id"],
            career_url="https://jobs.ashbyhq.com/example-ai/",
            evidence_url="https://example.ai/careers",
            detection_note="Official careers page links to this Ashby organization",
            priority_tier="A",
        )

    def test_url_policy_and_adapter_detection(self) -> None:
        self.assertEqual(
            "https://jobs.ashbyhq.com/example-ai",
            normalize_career_url("https://JOBS.ASHBYHQ.COM/example-ai/"),
        )
        self.assertEqual(
            ("ashby", "example-ai"),
            detect_adapter("https://jobs.ashbyhq.com/example-ai"),
        )
        for unsafe in (
            "http://example.ai/careers",
            "https://localhost/careers",
            "https://127.0.0.1/careers",
            "https://user:secret@example.ai/careers",
            "https://example.ai:8443/careers",
            "https://example.ai/careers?token=secret",
            "https://example.ai/careers#jobs",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    normalize_career_url(unsafe)

    def test_approved_source_is_registered_but_disabled_until_health_check(self) -> None:
        proposal = self._source_proposal()
        stored = get_company_source_proposal(self.connection, proposal["id"])
        self.assertEqual("ashby", stored["payload"]["adapter_type"])
        self.assertEqual("example-ai", stored["payload"]["source_key"])
        self.assertFalse(stored["payload"]["enable_after_approval"])

        result = approve_company_source_proposal(
            self.connection,
            proposal["id"],
            actor="candidate",
            reason="Reviewed synthetic official source",
        )

        source = result["company_source"]
        self.assertFalse(source["enabled"])
        self.assertEqual("not_checked", source["health_status"])
        row = self.connection.execute(
            "SELECT authority, enabled, health_status FROM company_source"
        ).fetchone()
        self.assertEqual(("official", 0, "not_checked"), tuple(row))
        with self.assertRaisesRegex(ValueError, "already approved"):
            approve_company_source_proposal(self.connection, proposal["id"])

    def test_changed_plan_blocks_source_approval_without_partial_write(self) -> None:
        proposal = self._source_proposal()
        self.connection.execute(
            "UPDATE company_plan_entry SET row_version = row_version + 1"
        )

        with self.assertRaisesRegex(ValueError, "plan changed"):
            approve_company_source_proposal(self.connection, proposal["id"])

        count = self.connection.execute("SELECT count(*) FROM company_source").fetchone()[0]
        self.assertEqual(0, count)
        stored = get_company_source_proposal(self.connection, proposal["id"])
        self.assertEqual("pending", stored["state"])

    def test_duplicate_source_registration_is_rejected(self) -> None:
        proposal = self._source_proposal()
        approve_company_source_proposal(self.connection, proposal["id"])
        with self.assertRaisesRegex(ValueError, "already registered"):
            self._source_proposal()

    def test_explicit_adapter_must_match_detected_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            create_company_source_proposal(
                self.connection,
                company_ref="Example AI",
                career_url="https://jobs.ashbyhq.com/example-ai",
                evidence_url="https://example.ai/careers",
                detection_note="Synthetic mismatch",
                adapter_type="lever",
            )

    def test_healthy_ashby_check_records_schema_evidence_but_stays_disabled(self) -> None:
        source = approve_company_source_proposal(
            self.connection, self._source_proposal()["id"]
        )["company_source"]

        class FakeClient:
            def fetch(self, url, *, allowed_hosts, accepted_content_types):
                self.url = url
                self.allowed_hosts = allowed_hosts
                return SafeHttpResponse(
                    200,
                    url,
                    "application/json",
                    b'{"jobs":[{"id":"one"},{"id":"two"}]}',
                )

        client = FakeClient()
        result = check_company_source_health(
            self.connection, source["id"], client=client
        )

        self.assertEqual("healthy", result["health_status"])
        self.assertEqual(2, result["record_count"])
        self.assertFalse(result["enabled"])
        self.assertEqual(
            "https://api.ashbyhq.com/posting-api/job-board/example-ai", client.url
        )
        stored = self.connection.execute(
            "SELECT enabled, health_status, last_checked_at FROM company_source"
        ).fetchone()
        self.assertEqual(0, stored["enabled"])
        self.assertEqual("healthy", stored["health_status"])
        self.assertIsNotNone(stored["last_checked_at"])
        run = self.connection.execute(
            "SELECT status, record_count, error_code FROM source_run"
        ).fetchone()
        self.assertEqual(("completed", 2, None), tuple(run))

    def test_bad_adapter_payload_records_failure_and_preserves_diagnostic(self) -> None:
        source = approve_company_source_proposal(
            self.connection, self._source_proposal()["id"]
        )["company_source"]

        class FakeClient:
            def fetch(self, url, *, allowed_hosts, accepted_content_types):
                return SafeHttpResponse(200, url, "application/json", b'{"items":[]}')

        result = check_company_source_health(
            self.connection, source["id"], client=FakeClient()
        )

        self.assertEqual("failed", result["run_status"])
        self.assertEqual("failed", result["health_status"])
        self.assertEqual("VALUEERROR", result["error"]["code"])
        event = self.connection.execute(
            "SELECT health_status, detail_json FROM source_health_event"
        ).fetchone()
        self.assertEqual("failed", event["health_status"])
        self.assertIn("expected jobs schema", event["detail_json"])

    def test_enablement_requires_health_and_explicit_approval(self) -> None:
        source = approve_company_source_proposal(
            self.connection, self._source_proposal()["id"]
        )["company_source"]
        with self.assertRaisesRegex(ValueError, "successful health check"):
            create_company_source_enablement_proposal(self.connection, source["id"])

        class FakeClient:
            def fetch(self, url, *, allowed_hosts, accepted_content_types):
                return SafeHttpResponse(200, url, "application/json", b'{"jobs":[]}')

        check_company_source_health(self.connection, source["id"], client=FakeClient())
        proposal = create_company_source_enablement_proposal(
            self.connection, source["id"]
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_company_source_enablement_proposal(self.connection, source["id"])
        stored = self.connection.execute(
            "SELECT enabled FROM company_source WHERE id = ?", (source["id"],)
        ).fetchone()
        self.assertEqual(0, stored["enabled"])

        result = approve_company_source_enablement_proposal(
            self.connection,
            proposal["id"],
            actor="candidate",
            reason="Enable synthetic daily monitoring",
        )
        self.assertTrue(result["enabled"])
        stored = self.connection.execute(
            "SELECT enabled FROM company_source WHERE id = ?", (source["id"],)
        ).fetchone()
        self.assertEqual(1, stored["enabled"])

    def test_new_health_result_makes_enablement_proposal_stale(self) -> None:
        source = approve_company_source_proposal(
            self.connection, self._source_proposal()["id"]
        )["company_source"]

        class FakeClient:
            def fetch(self, url, *, allowed_hosts, accepted_content_types):
                return SafeHttpResponse(200, url, "application/json", b'{"jobs":[]}')

        check_company_source_health(self.connection, source["id"], client=FakeClient())
        proposal = create_company_source_enablement_proposal(
            self.connection, source["id"]
        )
        check_company_source_health(self.connection, source["id"], client=FakeClient())

        with self.assertRaisesRegex(ValueError, "changed after"):
            approve_company_source_enablement_proposal(
                self.connection, proposal["id"]
            )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT enabled FROM company_source WHERE id = ?", (source["id"],)
            ).fetchone()["enabled"],
        )

    def test_detects_and_deduplicates_ats_links_from_official_html(self) -> None:
        proposal = create_company_source_proposal(
            self.connection,
            company_ref=self.company["id"],
            career_url="https://example.ai/careers",
            evidence_url="https://example.ai/careers",
            detection_note="Synthetic official careers page",
        )
        source = approve_company_source_proposal(
            self.connection, proposal["id"]
        )["company_source"]

        class FakeClient:
            def fetch(self, url, *, allowed_hosts, accepted_content_types):
                return SafeHttpResponse(
                    200,
                    url,
                    "text/html",
                    b"""
                    <a href="https://jobs.ashbyhq.com/example-ai/one">One</a>
                    <a href="https://jobs.ashbyhq.com/example-ai/two">Two</a>
                    <a href="https://attacker.example/jobs">Ignore</a>
                    """,
                )

        result = detect_linked_ats_sources(
            self.connection, source["id"], client=FakeClient()
        )
        self.assertEqual(
            [
                {
                    "adapter_type": "ashby",
                    "source_key": "example-ai",
                    "career_url": "https://jobs.ashbyhq.com/example-ai",
                    "evidence_url": "https://example.ai/careers",
                }
            ],
            result["candidates"],
        )


if __name__ == "__main__":
    unittest.main()
