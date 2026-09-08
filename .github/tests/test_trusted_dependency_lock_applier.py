from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trusted-dependency-lock-apply.yml"
WRITER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "apply_dependency_lock.py"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "default-branch-infra-tests.yml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
SETUP_UV_SHA = "20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
EXPECTED_HEAD = "a" * 40
MOVED_HEAD = "f" * 40


def load_writer_module():
    spec = importlib.util.spec_from_file_location("trusted_lock_applier_under_test", WRITER_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load trusted lock applier module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self, heads: list[str]) -> None:
        self.heads = iter(heads)
        self.fetches: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, object]] = []

    def current_head(self, head_branch: str) -> str:
        self.requests.append(("HEAD-CAS", head_branch, None))
        return next(self.heads)

    def fetch_file(self, path: str, ref: str) -> bytes:
        self.fetches.append((path, ref))
        return b"old-lock" if path.endswith("uv.lock") else b"[project]\nname='fixture'\n"

    def parent_tree(self, expected_head: str) -> str:
        self.requests.append(("PARENT-TREE", expected_head, None))
        return "b" * 40

    def request(self, method: str, path: str, payload=None):
        self.requests.append((method, path, payload))
        if path == "/git/blobs":
            return {"sha": "c" * 40}
        if path == "/git/trees":
            return {"sha": "d" * 40}
        if path == "/git/commits":
            return {"sha": "e" * 40}
        if method == "PATCH":
            return {}
        raise AssertionError(f"unexpected request: {method} {path}")


class TrustedDependencyLockApplierTest(unittest.TestCase):
    def writer_workflow(self) -> str:
        self.assertTrue(
            WRITER_WORKFLOW.is_file(),
            "trusted default-branch dependency lock writer workflow is missing",
        )
        return WRITER_WORKFLOW.read_text(encoding="utf-8")

    def writer_script(self) -> str:
        self.assertTrue(
            WRITER_SCRIPT.is_file(),
            "trusted default-branch dependency lock writer script is missing",
        )
        return WRITER_SCRIPT.read_text(encoding="utf-8")

    def test_focused_test_workflow_is_read_only_and_pinned(self) -> None:
        source = TEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('branches: ["main"]', source)
        self.assertIn("contents: read", source)
        self.assertNotIn("contents: write", source)
        self.assertIn(f"actions/checkout@{CHECKOUT_SHA}", source)
        self.assertIn(f"actions/setup-python@{SETUP_PYTHON_SHA}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("python .github/tests/test_trusted_dependency_lock_applier.py -v", source)
        self.assertNotRegex(source, r"uses:\s*[^\n]+@(main|master|v\d+(?:\.\d+)*)\s*(?:#.*)?$")

    def test_writer_trigger_is_trusted_default_branch_workflow_run_only(self) -> None:
        source = self.writer_workflow()
        self.assertIn("workflow_run:", source)
        self.assertIn('workflows: ["Dependency lock artifact"]', source)
        self.assertIn("types: [completed]", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertNotRegex(source, r"(?m)^\s*push\s*:")
        self.assertNotRegex(source, r"(?m)^\s*schedule\s*:")
        self.assertNotIn("workflow_dispatch", source)
        self.assertNotIn("repository_dispatch", source)

    def test_writer_token_scope_is_contents_write_only(self) -> None:
        source = self.writer_workflow()
        match = re.search(r"(?m)^permissions:\n(?P<body>(?: {2}[^\n]+\n)+)", source)
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertEqual(body.strip(), "contents: write")
        self.assertNotIn("write-all", source)

    def test_writer_guards_exact_trigger_identity_and_never_checks_out_pr_head(self) -> None:
        source = self.writer_workflow()
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.event == 'pull_request'", source)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", source)
        self.assertIn("github.event.workflow_run.pull_requests[0].base.ref == 'agent-harness-safety'", source)
        self.assertIn("github.event.workflow_run.pull_requests[0].head.repo.full_name == github.repository", source)
        self.assertIn("startsWith(github.event.workflow_run.head_branch, 'harn-')", source)
        self.assertIn(f"actions/checkout@{CHECKOUT_SHA}", source)
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertNotIn("ref: ${{ github.event.workflow_run.head_sha }}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertNotIn("download-artifact", source)

    def test_writer_uses_pinned_uv_but_no_pr_repository_commands(self) -> None:
        source = self.writer_workflow()
        self.assertIn(f"astral-sh/setup-uv@{SETUP_UV_SHA}", source)
        self.assertIn('version: "0.12.7"', source)
        self.assertIn("python .github/scripts/apply_dependency_lock.py", source)
        self.assertNotRegex(source, r"python\s+agent/scripts/")
        self.assertNotRegex(source, r"bash\s+(?:agent/)?scripts/")
        self.assertNotRegex(source, r"\bgit\s+checkout\b.*workflow_run")
        self.assertNotRegex(source, r"\bgit\s+push\b")

    def test_script_has_fixed_paths_strict_branch_policy_and_no_upstream_target(self) -> None:
        source = self.writer_script()
        self.assertIn('PROJECT_PATH = "agent/pyproject.toml"', source)
        self.assertIn('LOCK_PATH = "agent/uv.lock"', source)
        self.assertIn(r'^harn-[A-Za-z0-9._-]+$', source)
        self.assertIn('{"main", "agent-harness-safety"}', source)
        self.assertNotIn("DT-UCPH/cuc", source)
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"PROJECT_PATH", "LOCK_PATH"}
        }
        self.assertEqual(assignments["PROJECT_PATH"], "agent/pyproject.toml")
        self.assertEqual(assignments["LOCK_PATH"], "agent/uv.lock")

    def test_script_fetches_only_data_at_exact_head_and_regenerates_in_tempdir(self) -> None:
        source = self.writer_script()
        self.assertIn("TemporaryDirectory", source)
        self.assertIn("fetch_file(PROJECT_PATH, expected_head)", source)
        self.assertIn("fetch_file(LOCK_PATH, expected_head)", source)
        self.assertIn('["uv", "lock"]', source)
        self.assertIn('["uv", "lock", "--check"]', source)
        self.assertNotIn("exec(", source)
        self.assertNotIn("eval(", source)
        self.assertNotIn("importlib", source)

    def test_script_has_two_head_cas_checks_and_single_fixed_tree_path(self) -> None:
        source = self.writer_script()
        self.assertGreaterEqual(source.count("api.current_head(head_branch)"), 2)
        self.assertGreaterEqual(source.count("api.current_head(head_branch) != expected_head"), 2)
        self.assertIn('"path": LOCK_PATH', source)
        self.assertIn('"parents": [expected_head]', source)
        self.assertIn('"force": False', source)
        self.assertNotIn("force=True", source)
        self.assertNotIn('"force": True', source)

    def test_script_rejects_untrusted_repository_base_and_branch_inputs(self) -> None:
        source = self.writer_script()
        self.assertIn('expected_repository != repository', source)
        self.assertIn('base_branch != "agent-harness-safety"', source)
        self.assertIn("BRANCH_RE.fullmatch(head_branch)", source)
        self.assertIn("head_branch in FORBIDDEN_BRANCHES", source)
        self.assertIn("expected_head", source)
        self.assertIn("^[0-9a-f]{40}$", source)

    def test_writer_documents_bot_commit_is_not_ci_evidence(self) -> None:
        source = self.writer_workflow()
        self.assertIn("does not count as Agent tests evidence", source)
        self.assertIn("connector-authored follow-up commit", source)

    def test_uv_subprocess_does_not_inherit_write_or_runtime_tokens(self) -> None:
        module = load_writer_module()
        seen_envs = []

        def fake_run(args, *, cwd, check, env):
            self.assertTrue(check)
            self.assertEqual(Path(cwd).name.startswith("cuc-trusted-lock-"), True)
            seen_envs.append(dict(env))

        secret_env = {
            "GITHUB_TOKEN": "write-token",
            "GH_TOKEN": "gh-token",
            "ACTIONS_RUNTIME_TOKEN": "runtime-token",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-token",
        }
        with patch.dict(os.environ, secret_env, clear=False):
            with patch.object(module.subprocess, "run", side_effect=fake_run):
                module.regenerate_lock(b"[project]\nname='fixture'\n", b"old-lock")

        self.assertEqual(len(seen_envs), 2)
        for child_env in seen_envs:
            for secret_name in secret_env:
                self.assertNotIn(secret_name, child_env)
            self.assertIn("PATH", child_env)

    def test_behavior_pre_generation_head_drift_fails_before_fetch_or_write(self) -> None:
        module = load_writer_module()
        api = FakeApi([MOVED_HEAD])
        with patch.object(module, "regenerate_lock") as regenerate:
            with self.assertRaisesRegex(module.ApplyError, "moved before"):
                module.apply_lock_update(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                )
        regenerate.assert_not_called()
        self.assertEqual(api.fetches, [])
        self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.requests))

    def test_behavior_unchanged_lock_produces_no_git_write(self) -> None:
        module = load_writer_module()
        api = FakeApi([EXPECTED_HEAD])
        with patch.object(module, "regenerate_lock", return_value=b"old-lock"):
            result = module.apply_lock_update(
                api=api,
                repository="alexsosn/cuc",
                expected_repository="alexsosn/cuc",
                base_branch="agent-harness-safety",
                head_branch="harn-fixture",
                expected_head=EXPECTED_HEAD,
            )
        self.assertIsNone(result)
        self.assertEqual(
            api.fetches,
            [("agent/pyproject.toml", EXPECTED_HEAD), ("agent/uv.lock", EXPECTED_HEAD)],
        )
        self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.requests))

    def test_behavior_post_generation_head_drift_never_updates_ref(self) -> None:
        module = load_writer_module()
        api = FakeApi([EXPECTED_HEAD, MOVED_HEAD])
        with patch.object(module, "regenerate_lock", return_value=b"new-lock"):
            with self.assertRaisesRegex(module.ApplyError, "moved during"):
                module.apply_lock_update(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                )
        self.assertFalse(any(method == "PATCH" for method, _, _ in api.requests))

    def test_behavior_success_commits_one_lock_path_with_exact_parent_and_nonforce_ref(self) -> None:
        module = load_writer_module()
        api = FakeApi([EXPECTED_HEAD, EXPECTED_HEAD])
        with patch.object(module, "regenerate_lock", return_value=b"new-lock"):
            result = module.apply_lock_update(
                api=api,
                repository="alexsosn/cuc",
                expected_repository="alexsosn/cuc",
                base_branch="agent-harness-safety",
                head_branch="harn-fixture",
                expected_head=EXPECTED_HEAD,
            )
        self.assertEqual(result, "e" * 40)
        tree_requests = [payload for method, path, payload in api.requests if path == "/git/trees"]
        self.assertEqual(len(tree_requests), 1)
        self.assertEqual(
            tree_requests[0]["tree"],
            [{"path": "agent/uv.lock", "mode": "100644", "type": "blob", "sha": "c" * 40}],
        )
        commit_requests = [payload for method, path, payload in api.requests if path == "/git/commits"]
        self.assertEqual(commit_requests[0]["parents"], [EXPECTED_HEAD])
        patch_requests = [(path, payload) for method, path, payload in api.requests if method == "PATCH"]
        self.assertEqual(
            patch_requests,
            [("/git/refs/heads/harn-fixture", {"sha": "e" * 40, "force": False})],
        )

    def test_behavior_rejects_untrusted_input_dimensions(self) -> None:
        module = load_writer_module()
        common = dict(
            repository="alexsosn/cuc",
            expected_repository="alexsosn/cuc",
            base_branch="agent-harness-safety",
            head_branch="harn-fixture",
            expected_head=EXPECTED_HEAD,
        )
        bad_cases = [
            {"expected_repository": "attacker/cuc"},
            {"base_branch": "main"},
            {"head_branch": "feature/not-harn"},
            {"head_branch": "main"},
            {"expected_head": "not-a-sha"},
        ]
        for overrides in bad_cases:
            case = {**common, **overrides}
            with self.subTest(overrides=overrides):
                with self.assertRaises(module.ApplyError):
                    module.validate_inputs(**case)


if __name__ == "__main__":
    unittest.main()
