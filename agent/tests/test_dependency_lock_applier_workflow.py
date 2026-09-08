from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dependency-lock-apply.yml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_UV_SHA = "20cfd1bf945f4377ade1205e4dbc17946fc9a30d"


class DependencyLockApplierWorkflowTest(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(WORKFLOW.is_file(), "dependency lock apply workflow is missing")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_trigger_is_only_fork_harness_pull_requests(self) -> None:
        source = self.workflow()
        self.assertIn('pull_request:', source)
        self.assertIn('branches: ["agent-harness-safety"]', source)
        self.assertIn('types: [opened, synchronize, reopened]', source)
        self.assertNotIn('pull_request_target', source)
        self.assertNotRegex(source, r'(?m)^\s*push\s*:')
        self.assertNotRegex(source, r'(?m)^\s*schedule\s*:')
        self.assertNotIn('workflow_dispatch', source)
        self.assertNotIn('repository_dispatch', source)

    def test_token_scope_is_contents_write_only(self) -> None:
        source = self.workflow()
        permissions = re.search(r'(?ms)^permissions:\n(?P<body>(?:\s{2}.+\n)+)', source)
        self.assertIsNotNone(permissions)
        body = permissions.group('body')
        self.assertIn('contents: write', body)
        self.assertNotIn('issues:', body)
        self.assertNotIn('pull-requests:', body)
        self.assertNotIn('actions:', body)
        self.assertNotIn('write-all', source)

    def test_job_rejects_forks_and_non_harn_branches(self) -> None:
        source = self.workflow()
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository', source)
        self.assertIn("startsWith(github.event.pull_request.head.ref, 'harn-')", source)
        self.assertIn('^harn-[A-Za-z0-9._-]+$', source)
        self.assertIn('agent-harness-safety', source)
        self.assertIn('main', source)
        self.assertNotIn('DT-UCPH/cuc', source)

    def test_checkout_is_exact_head_pinned_and_has_no_persisted_credentials(self) -> None:
        source = self.workflow()
        self.assertIn(f'actions/checkout@{CHECKOUT_SHA}', source)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}', source)
        self.assertIn('persist-credentials: false', source)
        self.assertIn('git rev-parse HEAD', source)
        self.assertIn('EXPECTED_HEAD', source)
        self.assertNotRegex(source, r'uses:\s*[^\n]+@(main|master|v\d+(?:\.\d+)*)\s*(?:#.*)?$')

    def test_uv_is_pinned_and_only_lockfile_may_change(self) -> None:
        source = self.workflow()
        self.assertIn(f'astral-sh/setup-uv@{SETUP_UV_SHA}', source)
        self.assertIn('version: "0.12.7"', source)
        self.assertIn('uv lock\n', source)
        self.assertIn('uv lock --check', source)
        self.assertIn('agent/uv.lock', source)
        self.assertIn('git status --porcelain', source)
        self.assertIn('LOCK_PATH = "agent/uv.lock"', source)
        self.assertNotIn('git add -A', source)

    def test_git_data_write_is_fixed_path_exact_parent_and_non_force(self) -> None:
        source = self.workflow()
        self.assertIn('/git/blobs', source)
        self.assertIn('/git/trees', source)
        self.assertIn('/git/commits', source)
        self.assertIn('/git/refs/heads/', source)
        self.assertIn('"path": LOCK_PATH', source)
        self.assertIn('"parents": [expected_head]', source)
        self.assertIn('"force": False', source)
        self.assertIn('current_head == expected_head', source)
        self.assertNotRegex(source, r'\bgit\s+push\b')

    def test_writer_has_no_arbitrary_destination_inputs_or_repository_scripts(self) -> None:
        source = self.workflow()
        self.assertNotRegex(source, r'(?m)^\s*inputs\s*:')
        self.assertNotIn('${{ inputs.', source)
        self.assertNotRegex(source, r'python\s+agent/scripts/')
        self.assertNotRegex(source, r'bash\s+(?:agent/)?scripts/')
        self.assertNotIn('persist-credentials: true', source)

    def test_workflow_documents_bot_commit_not_as_ci_evidence(self) -> None:
        source = self.workflow()
        self.assertIn('does not count as Agent tests evidence', source)
        self.assertIn('connector-authored follow-up commit', source)


if __name__ == "__main__":
    unittest.main()
