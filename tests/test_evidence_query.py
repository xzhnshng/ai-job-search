from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.evidence.ingestion import (
    approve_evidence_proposal,
    create_evidence_proposal,
    register_source,
)
from ai_job_search.domain.evidence.query import (
    EligibilityContext,
    eligible_for_final_application,
    get_claim,
    list_claims,
)
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]


class EvidenceQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        self.document = self.workspace / "documents" / "projects" / "platform.md"
        self.document.parent.mkdir(parents=True)
        self.document.write_text("# Synthetic platform\n")
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)
        self.connection = connect(self.paths, self.config)
        apply_migrations(self.connection, self.workspace)
        source = register_source(
            self.connection,
            self.paths,
            self.document,
            source_type="candidate_attestation",
        )
        proposal = create_evidence_proposal(
            self.connection,
            {
                "contract_version": "1",
                "source_id": source["id"],
                "experiences": [],
                "projects": [
                    {
                        "name": "Synthetic Inference Platform",
                        "project_key": "synthetic_inference",
                        "ownership": ["Built a distributed streaming runtime"],
                        "collaboration_boundaries": ["A teammate built the UI"],
                        "metrics": [
                            {
                                "name": "average_total_time",
                                "before": 230,
                                "after": 125,
                                "unit": "milliseconds",
                            }
                        ],
                        "role_tags": ["senior_distributed_system_sde"],
                    }
                ],
                "skills": ["Java"],
                "education": [],
                "uncertainties": [],
            },
        )
        approve_evidence_proposal(self.connection, proposal["id"], actor="candidate")

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _claim(self, claim_type: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM claim WHERE claim_type = ?", (claim_type,)
        ).fetchone()
        self.assertIsNotNone(row)
        return dict(row)

    def test_approved_grounded_claim_is_eligible_and_traceable(self) -> None:
        claim = self._claim("metric")
        result = get_claim(self.connection, str(claim["id"]))

        self.assertTrue(result["eligibility"]["allowed"])
        self.assertEqual([], result["eligibility"]["reasons"])
        self.assertEqual(1, len(result["evidence"]))
        self.assertEqual("230 -> 125", result["metrics"][0]["value_text"])
        self.assertEqual("Synthetic Inference Platform", result["project_name"])

    def test_policy_explains_every_blocking_condition(self) -> None:
        claim = self._claim("ownership")
        self.connection.execute(
            """
            UPDATE claim
            SET verification_state = 'proposed', confidentiality = 'confidential',
                wording_strength = 'generalized', superseded_by_id = id
            WHERE id = ?
            """,
            (claim["id"],),
        )
        self.connection.execute("DELETE FROM claim_evidence WHERE claim_id = ?", (claim["id"],))
        current = self.connection.execute(
            "SELECT * FROM claim WHERE id = ?", (claim["id"],)
        ).fetchone()

        decision = eligible_for_final_application(
            self.connection,
            current,
            EligibilityContext(requested_wording_strength="exact"),
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(
            [
                "claim_not_approved",
                "claim_superseded",
                "confidentiality_blocked",
                "missing_active_evidence",
                "wording_strength_exceeded",
            ],
            decision["reasons"],
        )

    def test_collaboration_boundary_is_visible_but_not_application_eligible(self) -> None:
        claim = self._claim("boundary")
        result = get_claim(self.connection, str(claim["id"]))
        self.assertFalse(result["eligibility"]["allowed"])
        self.assertEqual(
            ["claim_type_not_application_evidence"], result["eligibility"]["reasons"]
        )

    def test_metric_claim_requires_an_approved_metric_record(self) -> None:
        claim = self._claim("metric")
        self.connection.execute(
            """
            UPDATE metric SET verification_state = 'proposed'
            WHERE id IN (SELECT metric_id FROM claim_metric WHERE claim_id = ?)
            """,
            (claim["id"],),
        )
        current = self.connection.execute(
            "SELECT * FROM claim WHERE id = ?", (claim["id"],)
        ).fetchone()
        decision = eligible_for_final_application(self.connection, current)
        self.assertEqual(["metric_not_approved"], decision["reasons"])

        self.connection.execute("DELETE FROM claim_metric WHERE claim_id = ?", (claim["id"],))
        decision = eligible_for_final_application(self.connection, current)
        self.assertEqual(["metric_claim_has_no_metric_record"], decision["reasons"])

    def test_search_filters_by_text_project_tag_and_eligibility(self) -> None:
        text_items = list_claims(self.connection, query="streaming", eligible_only=True)
        project_items = list_claims(self.connection, project="Inference Platform")
        tag_items = list_claims(self.connection, tag="senior_distributed_system_sde")

        self.assertEqual(1, len(text_items))
        self.assertEqual("ownership", text_items[0]["claim_type"])
        self.assertEqual(3, len(project_items))
        self.assertEqual(3, len(tag_items))

    def test_search_uses_normalized_skill_and_alias_links(self) -> None:
        claim = self._claim("ownership")
        skill = self.connection.execute(
            "SELECT id FROM skill WHERE canonical_name = 'Java'"
        ).fetchone()
        self.connection.execute(
            "INSERT INTO claim_skill(claim_id, skill_id, relation) VALUES (?, ?, 'direct')",
            (claim["id"], skill["id"]),
        )
        self.connection.execute(
            "INSERT INTO skill_alias(skill_id, alias) VALUES (?, 'JVM')",
            (skill["id"],),
        )

        by_name = list_claims(self.connection, skill="java")
        by_alias = list_claims(self.connection, skill="jvm")

        self.assertEqual([claim["id"]], [item["id"] for item in by_name])
        self.assertEqual([claim["id"]], [item["id"] for item in by_alias])
        self.assertEqual("Java", by_alias[0]["skills"][0]["canonical_name"])

    def test_claim_lookup_rejects_unknown_id(self) -> None:
        with self.assertRaises(KeyError):
            get_claim(self.connection, "claim_missing")

    def test_eligible_pagination_is_applied_after_policy_filtering(self) -> None:
        first = list_claims(self.connection, eligible_only=True, limit=1, offset=0)
        second = list_claims(self.connection, eligible_only=True, limit=1, offset=1)

        self.assertEqual(1, len(first))
        self.assertEqual(1, len(second))
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertNotEqual("boundary", first[0]["claim_type"])
        self.assertNotEqual("boundary", second[0]["claim_type"])

    def test_query_limits_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            list_claims(self.connection, limit=201)
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            list_claims(self.connection, offset=-1)


if __name__ == "__main__":
    unittest.main()
