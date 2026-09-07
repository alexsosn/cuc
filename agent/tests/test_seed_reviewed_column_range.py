import subprocess
import sys
import unittest
from pathlib import Path


class SeedReviewedColumnRangeTests(unittest.TestCase):
    def test_columnless_tablet_can_be_seeded_in_dry_run(self):
        agent_dir = Path(__file__).resolve().parents[1]
        reviewed = agent_dir.parent / "reviewed" / "KTU 2.10.tsv"
        before = reviewed.read_text(encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "scripts/seed_reviewed_column_range.py",
                "2.10",
                "-",
                "--dry-run",
            ],
            cwd=agent_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("KTU 2.10 from column -:", result.stdout)
        self.assertIn("(dry run — nothing written)", result.stdout)
        self.assertEqual(before, reviewed.read_text(encoding="utf-8"))

    def test_fully_reviewed_tablet_non_dry_is_noop(self):
        agent_dir = Path(__file__).resolve().parents[1]
        reviewed = agent_dir.parent / "reviewed" / "KTU 2.10.tsv"
        before = reviewed.read_text(encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "scripts/seed_reviewed_column_range.py",
                "2.10",
                "-",
            ],
            cwd=agent_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("0 rows to append", result.stdout)
        self.assertIn("no rows to append", result.stdout)
        self.assertEqual(before, reviewed.read_text(encoding="utf-8"))

    def test_seed_plan_omits_headers_without_new_rows(self):
        from scripts import seed_reviewed_column_range as seed

        selector = getattr(seed, "select_seed_order", None)
        self.assertIsNotNone(selector, "seed planning must filter headers before writing")

        order = [
            ("H", "# KTU 2.10 1"),
            ("T", "1"),
            ("H", "# KTU 2.10 2"),
            ("T", "2"),
            ("T", "3"),
            ("H", "# KTU 2.10 3"),
            ("T", "4"),
        ]
        selected = selector(order, {"1", "2", "4"})

        self.assertEqual(
            selected,
            [
                ("H", "# KTU 2.10 2"),
                ("T", "3"),
            ],
        )
        self.assertEqual(selector(order, {"1", "2", "3", "4"}), [])


if __name__ == "__main__":
    unittest.main()
