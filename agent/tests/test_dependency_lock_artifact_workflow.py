from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dependency-lock-artifact.yml"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-tests.yml"
UPLOAD_ARTIFACT_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


class DependencyLockArtifactWorkflowTest(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(LOCK_WORKFLOW.is_file(), "dependency lock artifact workflow is missing")
        return LOCK_WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_fork_pr_scoped_and_read_only(self) -> None:
        source = self.workflow()
        self.assertIn('branches: ["agent-harness-safety"]', source)
        self.assertIn('"agent/pyproject.toml"', source)
        self.assertIn("contents: read", source)
        self.assertNotRegex(source, r"contents:\s*write")
        self.assertNotIn("pull_request_target", source)
        self.assertNotIn("permissions: write-all", source)
        self.assertNotRegex(source, r"\bgit\s+push\b")
        self.assertNotRegex(source, r"\bgh\s+pr\b")

    def test_checkout_and_actions_are_immutable_and_credentials_are_not_persisted(self) -> None:
        source = self.workflow()
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            source,
        )
        self.assertIn("persist-credentials: false", source)
        self.assertIn(
            "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
            source,
        )
        self.assertIn('version: "0.12.7"', source)
        self.assertIn(f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}", source)
        self.assertNotRegex(source, r"uses:\s*[^\n]+@(main|master|v\d+(?:\.\d+)*)\s*(?:#.*)?$")

    def test_uv_generates_and_checks_lock_before_upload(self) -> None:
        source = self.workflow()
        lock_index = source.index("uv lock\n")
        check_index = source.index("uv lock --check")
        upload_index = source.index("actions/upload-artifact@")
        self.assertLess(lock_index, check_index)
        self.assertLess(check_index, upload_index)
        self.assertIn("agent/uv.lock", source)
        self.assertIn("agent/pyproject.toml", source)
        self.assertIn("agent/lock-artifact-metadata.json", source)

    def test_artifact_metadata_binds_head_merge_ref_and_uv_version(self) -> None:
        source = self.workflow()
        self.assertIn("github.event.pull_request.head.sha", source)
        self.assertIn("github.sha", source)
        self.assertIn("github.ref", source)
        self.assertIn('"uv_version":"0.12.7"', source.replace(" ", ""))

    def test_authoritative_agent_tests_remain_frozen(self) -> None:
        source = TEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("uv sync --frozen --no-install-project", source)
        self.assertNotRegex(source, r"(?m)^\s*run:\s*uv lock\s*$")


if __name__ == "__main__":
    unittest.main()
