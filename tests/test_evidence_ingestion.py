from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.evidence.ingestion import (
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


if __name__ == "__main__":
    unittest.main()
