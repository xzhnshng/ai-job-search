from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.companies.planning import (
    approve_company_plan_proposal,
    create_company_plan_proposal,
    get_company_plan_proposal,
    get_target_company,
    list_company_plan_proposals,
    list_target_companies,
    parse_company_table,
)
from ai_job_search.domain.evidence.ingestion import register_source
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]
TABLE = """\
| Rank | Company | Best target for you | Income potential | ML opportunity | Fit |
| --- | --- | --- | ---: | ---: | ---: |
| **1** | **Example Trading** | SWE / ML Infrastructure | $$$$½ | ★★★★★ | ★★★★½ |
"""
TECH_TABLE = """\
| Priority | Company | Best roles for you | ML/model proximity | My recommendation |
| --- | --- | --- | ---: | --- |
| **1** | **Example AI** | Inference / ML Infrastructure | ★★★★★ | Apply aggressively |
| **2** | **Example Systems** | Distributed Systems | ★★★ | Strong systems option |
"""


class CompanyPlanningTests(unittest.TestCase):
    def test_parser_preserves_market_specific_scores(self) -> None:
        payload = parse_company_table(TABLE, "trading", "source_" + "a" * 32)
        company = payload["companies"][0]
        self.assertEqual("Example Trading", company["company"])
        self.assertEqual(4.5, company["income_potential"])
        self.assertEqual(5.0, company["ml_opportunity"])
        self.assertEqual(4.5, company["fit"])

    def test_source_bound_company_plan_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".git").mkdir()
            (workspace / "AGENTS.md").write_text("# test\n")
            shutil.copytree(ROOT / "config", workspace / "config")
            shutil.copytree(ROOT / "migrations", workspace / "migrations")
            table = workspace / "documents" / "plans" / "companies.md"
            table.parent.mkdir(parents=True)
            table.write_text(TABLE)
            paths = RuntimePaths.resolve(workspace)
            config = load_config(workspace)
            connection = connect(paths, config)
            try:
                apply_migrations(connection, workspace)
                source = register_source(
                    connection, paths, table, source_type="target_company_plan"
                )
                proposal = create_company_plan_proposal(
                    connection, source["id"], "trading", table
                )
                self.assertEqual("pending", proposal["state"])
                stored = get_company_plan_proposal(connection, proposal["id"])
                self.assertEqual("trading", stored["payload"]["market"])
            finally:
                connection.close()


class CompanyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        self.table = self.workspace / "documents" / "plans" / "companies.md"
        self.table.parent.mkdir(parents=True)
        self.table.write_text(TECH_TABLE)
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)
        self.connection = connect(self.paths, self.config)
        apply_migrations(self.connection, self.workspace)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _proposal(self) -> dict[str, object]:
        source = register_source(
            self.connection,
            self.paths,
            self.table,
            source_type="target_company_plan",
        )
        return create_company_plan_proposal(
            self.connection, source["id"], "technology", self.table
        )

    def test_approval_atomically_builds_queryable_company_registry(self) -> None:
        proposal = self._proposal()
        pending = list_company_plan_proposals(
            self.connection, state="pending", market="technology"
        )
        self.assertEqual(proposal["id"], pending[0]["id"])
        self.assertTrue(pending[0]["source_current"])
        result = approve_company_plan_proposal(
            self.connection,
            proposal["id"],
            actor="candidate",
            reason="Reviewed synthetic technology plan",
        )

        self.assertEqual("approved", result["state"])
        self.assertEqual(2, result["companies_created"])
        items = list_target_companies(self.connection, market="technology")
        self.assertEqual(["Example AI", "Example Systems"], [item["name"] for item in items])
        self.assertEqual([1, 2], [item["priority_rank"] for item in items])
        self.assertEqual("not_registered", items[0]["source_health"])
        detail = get_target_company(self.connection, items[0]["id"])
        self.assertEqual("Example AI", detail["name"])
        self.assertEqual(1, len(detail["plans"]))
        self.assertEqual([], detail["sources"])

        with self.assertRaisesRegex(ValueError, "already approved"):
            approve_company_plan_proposal(self.connection, proposal["id"])
        self.assertEqual(2, len(list_target_companies(self.connection)))

    def test_stale_source_is_rejected_without_partial_registry(self) -> None:
        proposal = self._proposal()
        self.table.write_text(TECH_TABLE.replace("Example AI", "Changed AI"))
        register_source(
            self.connection,
            self.paths,
            self.table,
            source_type="target_company_plan",
        )

        with self.assertRaisesRegex(ValueError, "source changed"):
            approve_company_plan_proposal(self.connection, proposal["id"])

        self.assertEqual([], list_target_companies(self.connection))
        state = self.connection.execute(
            "SELECT state FROM proposal WHERE id = ?", (proposal["id"],)
        ).fetchone()[0]
        self.assertEqual("pending", state)
        pending = list_company_plan_proposals(self.connection, state="pending")
        self.assertFalse(pending[0]["source_current"])

    def test_new_approved_plan_deactivates_removed_entries_without_deleting_company(self) -> None:
        first = self._proposal()
        approve_company_plan_proposal(self.connection, first["id"])
        self.table.write_text(
            TECH_TABLE.replace(
                "| **2** | **Example Systems** | Distributed Systems | ★★★ | Strong systems option |\n",
                "",
            )
        )
        second = self._proposal()
        result = approve_company_plan_proposal(self.connection, second["id"])

        self.assertEqual(1, result["companies_deactivated"])
        self.assertEqual(1, len(list_target_companies(self.connection)))
        all_entries = list_target_companies(self.connection, active_only=False)
        self.assertEqual(2, len(all_entries))
        inactive = [item for item in all_entries if not item["active"]]
        self.assertEqual(["Example Systems"], [item["name"] for item in inactive])
        company_count = self.connection.execute("SELECT count(*) FROM company").fetchone()[0]
        self.assertEqual(2, company_count)


if __name__ == "__main__":
    unittest.main()
