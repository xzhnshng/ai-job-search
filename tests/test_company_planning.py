from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.companies.planning import (
    create_company_plan_proposal,
    get_company_plan_proposal,
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


if __name__ == "__main__":
    unittest.main()
