from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_job_search.domain.evidence.inventory import inventory_sources


class EvidenceInventoryTests(unittest.TestCase):
    def test_inventory_reports_minimum_onboarding_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            cv = workspace / "documents" / "cv" / "master"
            projects = workspace / "documents" / "projects" / "project-a"
            cv.mkdir(parents=True)
            projects.mkdir(parents=True)
            (cv / "main.tex").write_text("\\documentclass{article}", encoding="utf-8")
            (cv / "compiled.pdf").write_bytes(b"%PDF synthetic fixture")
            (projects / "README.md").write_text("# Synthetic project", encoding="utf-8")
            result = inventory_sources(workspace)
            self.assertTrue(result["ready_for_ingestion"])
            self.assertEqual(2, result["counts"]["cv"])
            self.assertEqual(1, result["counts"]["projects"])
            self.assertEqual(3, result["supported_count"])

    def test_empty_inventory_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = inventory_sources(Path(directory))
            self.assertFalse(result["ready_for_ingestion"])
            self.assertEqual(0, result["file_count"])


if __name__ == "__main__":
    unittest.main()
