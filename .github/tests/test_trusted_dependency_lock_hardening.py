from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trusted-dependency-lock-apply.yml"


class TrustedDependencyLockHardeningTest(unittest.TestCase):
    def test_privileged_uv_setup_disables_cache_and_does_not_receive_github_token_input(self) -> None:
        source = WRITER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("enable-cache: false", source)
        self.assertIn('github-token: ""', source)


if __name__ == "__main__":
    unittest.main()
