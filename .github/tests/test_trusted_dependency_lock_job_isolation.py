from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trusted-dependency-lock-apply.yml"
APPLIER = REPO_ROOT / ".github" / "scripts" / "apply_dependency_lock.py"
RESOLVER = REPO_ROOT / ".github" / "scripts" / "resolve_dependency_lock.py"
UPLOAD_ARTIFACT_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_SHA = "37930b1c2abaa49bbe596cd826c3c89aef350131"


def job_block(source: str, name: str) -> str:
    marker = f"  {name}:\n"
    if marker not in source:
        raise AssertionError(f"workflow job is missing: {name}")
    tail = source.split(marker, 1)[1]
    next_job = re.search(r"(?m)^  [a-z0-9][a-z0-9-]*:\n", tail)
    return tail if next_job is None else tail[: next_job.start()]


class TrustedDependencyLockJobIsolationTest(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(WORKFLOW.is_file())
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_has_exactly_resolve_and_apply_jobs(self) -> None:
        source = self.workflow()
        jobs = re.findall(r"(?m)^  ([a-z0-9][a-z0-9-]*):\n", source.split("jobs:\n", 1)[1])
        self.assertEqual(jobs, ["resolve-lock", "apply-lock"])

    def test_real_workflow_run_payload_uses_repo_id_not_missing_pr_repo_full_name(self) -> None:
        source = self.workflow()
        # Live HARN-004 workflow_run payload exposed pull_requests[0].head.repo as
        # {id, url, name}; full_name was absent, making the previous guard always false.
        self.assertNotIn("pull_requests[0].head.repo.full_name", source)
        self.assertIn(
            "github.event.workflow_run.pull_requests[0].head.repo.id == "
            "github.event.workflow_run.head_repository.id",
            source,
        )
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            source,
        )

    def test_resolver_job_is_read_only_and_owns_uv_execution(self) -> None:
        block = job_block(self.workflow(), "resolve-lock")
        self.assertIn("permissions:\n      contents: read", block)
        self.assertIn("astral-sh/setup-uv@", block)
        self.assertIn("python .github/scripts/resolve_dependency_lock.py", block)
        self.assertIn(f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}", block)
        self.assertIn("name: trusted-uv-lock-${{ github.event.workflow_run.head_sha }}", block)
        self.assertNotIn("contents: write", block)
        self.assertNotIn("apply_dependency_lock.py", block)

    def test_apply_job_is_write_only_after_resolver_and_never_runs_uv(self) -> None:
        block = job_block(self.workflow(), "apply-lock")
        self.assertIn("needs: resolve-lock", block)
        self.assertIn("permissions:\n      contents: write", block)
        self.assertIn(f"actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}", block)
        self.assertIn("name: trusted-uv-lock-${{ github.event.workflow_run.head_sha }}", block)
        self.assertIn("python .github/scripts/apply_dependency_lock.py", block)
        self.assertNotIn("setup-uv", block)
        self.assertNotRegex(block, r"\buv\s+lock\b")
        self.assertNotIn("resolve_dependency_lock.py", block)

    def test_only_current_trusted_run_artifact_is_used(self) -> None:
        block = job_block(self.workflow(), "apply-lock")
        self.assertNotIn("run-id:", block)
        self.assertNotIn("repository:", block)
        self.assertNotIn("github-token:", block)
        self.assertIn("path: trusted-lock-artifact", block)

    def test_resolver_is_separate_and_applier_consumes_fixed_artifact_paths(self) -> None:
        self.assertTrue(RESOLVER.is_file(), "read-only resolver script is missing")
        resolver = RESOLVER.read_text(encoding="utf-8")
        applier = APPLIER.read_text(encoding="utf-8")
        self.assertIn('ARTIFACT_LOCK_PATH = "trusted-lock-artifact/uv.lock"', applier)
        self.assertIn('ARTIFACT_METADATA_PATH = "trusted-lock-artifact/metadata.json"', applier)
        self.assertIn("expected_head", resolver)
        self.assertIn("uv_lock_sha256", resolver)
        self.assertIn("expected_head", applier)
        self.assertIn("uv_lock_sha256", applier)

    def test_artifact_metadata_is_verified_before_any_git_write(self) -> None:
        applier = APPLIER.read_text(encoding="utf-8")
        verification = applier.find("verify_artifact")
        blob_write = applier.find('"POST",\n        "/git/blobs"')
        self.assertGreaterEqual(verification, 0)
        self.assertGreater(blob_write, verification)
        self.assertIn("sha256", applier)


if __name__ == "__main__":
    unittest.main()
