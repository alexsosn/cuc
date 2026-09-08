from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trusted-dependency-lock-apply.yml"
WRITER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "apply_dependency_lock.py"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "default-branch-infra-tests.yml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
EXPECTED_HEAD = "a" * 40
MOVED_HEAD = "f" * 40
PROJECT = b"[project]\nname='fixture'\n"
OLD_LOCK = b"old-lock\n"
NEW_LOCK = b"new-lock\n"


def load_module():
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
        if path == "agent/pyproject.toml":
            return PROJECT
        if path == "agent/uv.lock":
            return OLD_LOCK
        raise AssertionError(f"unexpected fetch path: {path}")

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


def write_artifact(root: Path, *, lock: bytes = NEW_LOCK, **overrides) -> tuple[Path, Path]:
    lock_path = root / "uv.lock"
    metadata_path = root / "metadata.json"
    lock_path.write_bytes(lock)
    metadata = {
        "schema_version": 1,
        "repository": "alexsosn/cuc",
        "base_branch": "agent-harness-safety",
        "head_branch": "harn-fixture",
        "expected_head": EXPECTED_HEAD,
        "uv_version": "0.12.7",
        "pyproject_sha256": sha256(PROJECT).hexdigest(),
        "old_lock_sha256": sha256(OLD_LOCK).hexdigest(),
        "uv_lock_sha256": sha256(lock).hexdigest(),
    }
    metadata.update(overrides)
    metadata_path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return lock_path, metadata_path


class TrustedDependencyLockApplierTest(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(WRITER_WORKFLOW.is_file())
        return WRITER_WORKFLOW.read_text(encoding="utf-8")

    def script(self) -> str:
        self.assertTrue(WRITER_SCRIPT.is_file())
        return WRITER_SCRIPT.read_text(encoding="utf-8")

    def test_focused_test_workflow_is_read_only_and_pinned(self) -> None:
        source = TEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('branches: ["main"]', source)
        self.assertIn("contents: read", source)
        self.assertNotIn("contents: write", source)
        self.assertIn(f"actions/checkout@{CHECKOUT_SHA}", source)
        self.assertIn(f"actions/setup-python@{SETUP_PYTHON_SHA}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("test_trusted_dependency_lock_applier.py", source)
        self.assertIn("test_trusted_dependency_lock_resolver.py", source)
        self.assertIn("test_trusted_dependency_lock_job_isolation.py", source)
        self.assertNotRegex(source, r"uses:\s*[^\n]+@(main|master|v\d+(?:\.\d+)*)\s*(?:#.*)?$")

    def test_writer_trigger_is_default_branch_workflow_run_only(self) -> None:
        source = self.workflow()
        self.assertIn("workflow_run:", source)
        self.assertIn('workflows: ["Dependency lock artifact"]', source)
        self.assertIn("types: [completed]", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertNotRegex(source, r"(?m)^\s*push\s*:")
        self.assertNotRegex(source, r"(?m)^\s*schedule\s*:")
        self.assertNotIn("workflow_dispatch", source)
        self.assertNotIn("repository_dispatch", source)

    def test_privileged_script_is_byte_only_and_has_no_dependency_execution(self) -> None:
        source = self.script()
        self.assertIn('PROJECT_PATH = "agent/pyproject.toml"', source)
        self.assertIn('LOCK_PATH = "agent/uv.lock"', source)
        self.assertIn('ARTIFACT_LOCK_PATH = "trusted-lock-artifact/uv.lock"', source)
        self.assertIn('ARTIFACT_METADATA_PATH = "trusted-lock-artifact/metadata.json"', source)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("subprocess.", source)
        self.assertNotIn("TemporaryDirectory", source)
        self.assertNotIn('["uv", "lock"]', source)
        self.assertNotIn('["uv", "lock", "--check"]', source)
        self.assertNotIn("DT-UCPH/cuc", source)

    def test_rejects_untrusted_input_dimensions(self) -> None:
        module = load_module()
        common = dict(
            repository="alexsosn/cuc",
            expected_repository="alexsosn/cuc",
            base_branch="agent-harness-safety",
            head_branch="harn-fixture",
            expected_head=EXPECTED_HEAD,
        )
        for overrides in (
            {"expected_repository": "attacker/cuc"},
            {"base_branch": "main"},
            {"head_branch": "feature/not-harn"},
            {"head_branch": "main"},
            {"expected_head": "not-a-sha"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(module.ApplyError):
                    module.validate_inputs(**{**common, **overrides})

    def test_artifact_metadata_or_digest_mismatch_fails_before_write(self) -> None:
        module = load_module()
        for override in (
            {"expected_head": MOVED_HEAD},
            {"repository": "attacker/cuc"},
            {"uv_lock_sha256": "0" * 64},
        ):
            with self.subTest(override=override), TemporaryDirectory() as temporary:
                lock_path, metadata_path = write_artifact(Path(temporary), **override)
                api = FakeApi([EXPECTED_HEAD])
                with self.assertRaises(module.ApplyError):
                    module.apply_lock_update(
                        api=api,
                        repository="alexsosn/cuc",
                        expected_repository="alexsosn/cuc",
                        base_branch="agent-harness-safety",
                        head_branch="harn-fixture",
                        expected_head=EXPECTED_HEAD,
                        artifact_lock_path=lock_path,
                        artifact_metadata_path=metadata_path,
                    )
                self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.requests))

    def test_pre_apply_head_drift_fails_before_fetch_or_write(self) -> None:
        module = load_module()
        with TemporaryDirectory() as temporary:
            lock_path, metadata_path = write_artifact(Path(temporary))
            api = FakeApi([MOVED_HEAD])
            with self.assertRaisesRegex(module.ApplyError, "moved before"):
                module.apply_lock_update(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                    artifact_lock_path=lock_path,
                    artifact_metadata_path=metadata_path,
                )
            self.assertEqual(api.fetches, [])
            self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.requests))

    def test_unchanged_verified_lock_produces_no_git_write(self) -> None:
        module = load_module()
        with TemporaryDirectory() as temporary:
            lock_path, metadata_path = write_artifact(Path(temporary), lock=OLD_LOCK)
            api = FakeApi([EXPECTED_HEAD])
            result = module.apply_lock_update(
                api=api,
                repository="alexsosn/cuc",
                expected_repository="alexsosn/cuc",
                base_branch="agent-harness-safety",
                head_branch="harn-fixture",
                expected_head=EXPECTED_HEAD,
                artifact_lock_path=lock_path,
                artifact_metadata_path=metadata_path,
            )
            self.assertIsNone(result)
            self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.requests))

    def test_post_prepare_head_drift_never_updates_ref(self) -> None:
        module = load_module()
        with TemporaryDirectory() as temporary:
            lock_path, metadata_path = write_artifact(Path(temporary))
            api = FakeApi([EXPECTED_HEAD, MOVED_HEAD])
            with self.assertRaisesRegex(module.ApplyError, "moved during"):
                module.apply_lock_update(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                    artifact_lock_path=lock_path,
                    artifact_metadata_path=metadata_path,
                )
            self.assertFalse(any(method == "PATCH" for method, _, _ in api.requests))

    def test_success_commits_one_lock_path_exact_parent_and_nonforce_ref(self) -> None:
        module = load_module()
        with TemporaryDirectory() as temporary:
            lock_path, metadata_path = write_artifact(Path(temporary))
            api = FakeApi([EXPECTED_HEAD, EXPECTED_HEAD])
            result = module.apply_lock_update(
                api=api,
                repository="alexsosn/cuc",
                expected_repository="alexsosn/cuc",
                base_branch="agent-harness-safety",
                head_branch="harn-fixture",
                expected_head=EXPECTED_HEAD,
                artifact_lock_path=lock_path,
                artifact_metadata_path=metadata_path,
            )
            self.assertEqual(result, "e" * 40)
            tree_payloads = [payload for method, path, payload in api.requests if path == "/git/trees"]
            self.assertEqual(
                tree_payloads[0]["tree"],
                [{"path": "agent/uv.lock", "mode": "100644", "type": "blob", "sha": "c" * 40}],
            )
            commit_payloads = [payload for method, path, payload in api.requests if path == "/git/commits"]
            self.assertEqual(commit_payloads[0]["parents"], [EXPECTED_HEAD])
            patches = [(path, payload) for method, path, payload in api.requests if method == "PATCH"]
            self.assertEqual(
                patches,
                [("/git/refs/heads/harn-fixture", {"sha": "e" * 40, "force": False})],
            )


if __name__ == "__main__":
    unittest.main()
