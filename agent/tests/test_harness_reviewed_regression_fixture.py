from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from reviewed_evaluation.loader import MorphologyTsvLoader


AGENT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = AGENT_ROOT / "tests" / "fixtures" / "harn_003_reviewed_morphology"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
REVIEWED_ROOT = FIXTURE_ROOT / "reviewed"
AUTO_ROOT = FIXTURE_ROOT / "auto"
SELECTED_IDS = {
    "159322",
    "159323",
    "176080",
    "176084",
    "176085",
    "176096",
    "176102",
}
REQUIRED_ROLES = {
    "unambiguous",
    "genuine-ambiguity",
    "dulat-not-found",
    "ordered-heuristic",
    "overgeneration",
}


class ReviewedMorphologyRegressionFixtureTest(unittest.TestCase):
    maxDiff = None

    def load_manifest(self) -> dict:
        self.assertTrue(MANIFEST_PATH.is_file(), f"missing fixture manifest: {MANIFEST_PATH}")
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_declares_exact_representative_selection(self) -> None:
        manifest = self.load_manifest()

        cases = manifest["cases"]
        self.assertEqual({str(case["id"]) for case in cases}, SELECTED_IDS)
        self.assertTrue(
            REQUIRED_ROLES.issubset(
                {role for case in cases for role in case["roles"]}
            )
        )
        self.assertEqual(
            manifest["sources"],
            {
                "reviewed/KTU 1.6.tsv": "reviewed/KTU 1.6.tsv",
                "reviewed/KTU 2.10.tsv": "reviewed/KTU 2.10.tsv",
                "auto/KTU 1.6.tsv": "auto_parsing/0.2.8/KTU 1.6.tsv",
                "auto/KTU 2.10.tsv": "auto_parsing/0.2.8/KTU 2.10.tsv",
            },
        )

    def test_fixture_preserves_ambiguity_overgeneration_and_unresolved_case(self) -> None:
        loader = MorphologyTsvLoader()
        reviewed_210 = loader.load(REVIEWED_ROOT / "KTU 2.10.tsv")
        auto_210 = loader.load(AUTO_ROOT / "KTU 2.10.tsv")

        self.assertEqual(reviewed_210.tokens_by_id["176080"].analyses, frozenset({"tḥm/"}))
        self.assertEqual(
            reviewed_210.tokens_by_id["176096"].analyses,
            frozenset({"in/~m~m", "in m(nm"}),
        )
        self.assertGreater(
            len(auto_210.tokens_by_id["176084"].analyses),
            len(reviewed_210.tokens_by_id["176084"].analyses),
        )
        self.assertGreater(
            len(auto_210.tokens_by_id["176102"].analyses),
            len(reviewed_210.tokens_by_id["176102"].analyses),
        )

        auto_text = (AUTO_ROOT / "KTU 2.10.tsv").read_text(encoding="utf-8")
        unresolved_lines = [
            line for line in auto_text.splitlines() if line.startswith("176085\t")
        ]
        self.assertEqual(len(unresolved_lines), 1)
        self.assertIn("DULAT: NOT FOUND", unresolved_lines[0])

    def test_fixture_preserves_ordered_formula_context_pair(self) -> None:
        loader = MorphologyTsvLoader()
        reviewed_16 = loader.load(REVIEWED_ROOT / "KTU 1.6.tsv")
        auto_16 = loader.load(AUTO_ROOT / "KTU 1.6.tsv")

        self.assertEqual(set(reviewed_16.tokens_by_id), {"159322", "159323"})
        self.assertEqual(set(auto_16.tokens_by_id), {"159322", "159323"})
        self.assertEqual(reviewed_16.tokens_by_id["159322"].surface, "aliyn")
        self.assertEqual(reviewed_16.tokens_by_id["159323"].surface, "bˤl")
        self.assertEqual(
            reviewed_16.tokens_by_id["159323"].analyses,
            frozenset({"bˤl(II)/"}),
        )
        self.assertIn(
            "# KTU 1.6 I:12",
            (REVIEWED_ROOT / "KTU 1.6.tsv").read_text(encoding="utf-8"),
        )

    def test_authoritative_scorer_runs_end_to_end_and_payload_is_small(self) -> None:
        command = [
            sys.executable,
            str(AGENT_ROOT / "scripts" / "score_reviewed_morphology.py"),
            "--reviewed",
            str(REVIEWED_ROOT),
            "--auto",
            str(AUTO_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=AGENT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["file_count"], 2)
        self.assertEqual(len(payload["files"]), 2)
        self.assertEqual(payload["summary"]["compared_ids"], 7)
        self.assertEqual(
            {result["label"] for result in payload["files"]},
            {"KTU 1.6.tsv", "KTU 2.10.tsv"},
        )
        self.assertLess(len(completed.stdout.encode("utf-8")), 32_000)


if __name__ == "__main__":
    unittest.main()
