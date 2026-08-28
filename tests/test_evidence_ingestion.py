from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.evidence.ingestion import (
    approve_evidence_proposal,
    create_evidence_proposal,
    get_evidence_proposal,
    list_sources,
    register_source,
)
from ai_job_search.infrastructure.files.config_loader import load_config
from ai_job_search.infrastructure.files.paths import RuntimePaths
from ai_job_search.infrastructure.sqlite.connection import connect
from ai_job_search.infrastructure.sqlite.migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[1]


class EvidenceIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".git").mkdir()
        (self.workspace / "AGENTS.md").write_text("# test\n")
        shutil.copytree(ROOT / "config", self.workspace / "config")
        shutil.copytree(ROOT / "migrations", self.workspace / "migrations")
        self.document = self.workspace / "documents" / "cv" / "resume.md"
        self.document.parent.mkdir(parents=True)
        self.document.write_text("# Synthetic Resume\n\nNo personal data.\n")
        self.paths = RuntimePaths.resolve(self.workspace)
        self.config = load_config(self.workspace)
        self.connection = connect(self.paths, self.config)
        apply_migrations(self.connection, self.workspace)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_register_extract_and_rerun_are_idempotent(self) -> None:
        first = register_source(
            self.connection, self.paths, self.document, source_type="resume"
        )
        second = register_source(
            self.connection, self.paths, self.document, source_type="resume"
        )
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("extracted", second["extraction_status"])
        self.assertEqual(1, len(list_sources(self.connection)))
        self.assertTrue(Path(second["extracted_text_path"]).is_file())

    def test_source_outside_documents_is_rejected(self) -> None:
        outside = self.workspace / "outside.md"
        outside.write_text("outside")
        with self.assertRaisesRegex(ValueError, "below documents"):
            register_source(self.connection, self.paths, outside, source_type="resume")

    def test_extraction_proposal_is_pending_and_source_bound(self) -> None:
        source = register_source(
            self.connection, self.paths, self.document, source_type="resume"
        )
        proposal = create_evidence_proposal(
            self.connection,
            {
                "contract_version": "1",
                "source_id": source["id"],
                "experiences": [],
                "projects": [],
                "skills": ["Python"],
                "education": [],
                "uncertainties": ["Synthetic fixture"],
            },
        )
        self.assertEqual("pending", proposal["state"])
        stored = get_evidence_proposal(self.connection, proposal["id"])
        self.assertEqual(source["sha256"], stored["expected_versions"]["source_sha256"])
        self.assertEqual(["Python"], stored["payload"]["skills"])

    def _project_payload(self, source_id: str) -> dict[str, object]:
        return {
            "contract_version": "1",
            "source_id": source_id,
            "experiences": [],
            "projects": [
                {
                    "name": "Synthetic Inference Platform",
                    "project_key": "synthetic_inference",
                    "evidence_status": "candidate_attested",
                    "resume_metric_policy": "use_all_truthful_helpful_numbers",
                    "ownership": ["Built the streaming runtime"],
                    "collaboration_boundaries": ["A teammate built the UI"],
                    "metrics": [
                        {
                            "name": "average_total_time",
                            "before": 230,
                            "after": 125,
                            "unit": "milliseconds",
                            "warning": "Do not label as TTFT",
                        }
                    ],
                    "role_tags": ["senior_backend_sde"],
                }
            ],
            "skills": ["Java", "AWS CDK"],
            "education": [],
            "uncertainties": ["Synthetic fixture"],
        }

    def test_approval_atomically_normalizes_reviewed_project_evidence(self) -> None:
        source = register_source(
            self.connection, self.paths, self.document, source_type="candidate_attestation"
        )
        proposal = create_evidence_proposal(
            self.connection, self._project_payload(source["id"])
        )

        result = approve_evidence_proposal(
            self.connection,
            proposal["id"],
            actor="candidate",
            reason="Reviewed synthetic proposal",
        )

        self.assertEqual("approved", result["state"])
        self.assertEqual(
            {
                "projects": 1,
                "components": 3,
                "claims": 3,
                "metrics": 1,
                "skills": 2,
                "evidence_links": 3,
            },
            result["created"],
        )
        self.assertEqual(1, self.connection.execute("SELECT count(*) FROM project").fetchone()[0])
        self.assertEqual(
            3, self.connection.execute("SELECT count(*) FROM project_component").fetchone()[0]
        )
        self.assertEqual(3, self.connection.execute("SELECT count(*) FROM claim").fetchone()[0])
        self.assertEqual(1, self.connection.execute("SELECT count(*) FROM metric").fetchone()[0])
        self.assertEqual(2, self.connection.execute("SELECT count(*) FROM skill").fetchone()[0])
        self.assertEqual(
            3, self.connection.execute("SELECT count(*) FROM claim_evidence").fetchone()[0]
        )
        self.assertEqual(
            "230 -> 125",
            self.connection.execute("SELECT value_text FROM metric").fetchone()[0],
        )
        approval = self.connection.execute(
            "SELECT decision, actor, reason FROM approval_event WHERE proposal_id = ?",
            (proposal["id"],),
        ).fetchone()
        self.assertEqual(
            ("approved", "candidate", "Reviewed synthetic proposal"), tuple(approval)
        )

        with self.assertRaisesRegex(ValueError, "already approved"):
            approve_evidence_proposal(self.connection, proposal["id"])
        self.assertEqual(1, self.connection.execute("SELECT count(*) FROM project").fetchone()[0])

    def test_approval_rejects_stale_source_without_partial_writes(self) -> None:
        source = register_source(
            self.connection, self.paths, self.document, source_type="candidate_attestation"
        )
        proposal = create_evidence_proposal(
            self.connection, self._project_payload(source["id"])
        )
        self.document.write_text("# Changed attestation\n")
        register_source(
            self.connection, self.paths, self.document, source_type="candidate_attestation"
        )

        with self.assertRaisesRegex(ValueError, "source changed"):
            approve_evidence_proposal(self.connection, proposal["id"])

        self.assertEqual(0, self.connection.execute("SELECT count(*) FROM project").fetchone()[0])
        self.assertEqual(
            "pending",
            self.connection.execute(
                "SELECT state FROM proposal WHERE id = ?", (proposal["id"],)
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
