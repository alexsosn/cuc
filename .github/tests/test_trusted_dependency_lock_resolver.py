from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "resolve_dependency_lock.py"
EXPECTED_HEAD = "a" * 40
MOVED_HEAD = "f" * 40
PROJECT = b"[project]\nname='fixture'\n"
OLD_LOCK = b"old-lock\n"
NEW_LOCK = b"new-lock\n"


def load_module():
    self_dir = str(RESOLVER_SCRIPT.parent)
    if self_dir not in sys.path:
        sys.path.insert(0, self_dir)
    spec = importlib.util.spec_from_file_location("trusted_lock_resolver_under_test", RESOLVER_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load trusted lock resolver module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self, heads: list[str]) -> None:
        self.heads = iter(heads)
        self.fetches: list[tuple[str, str]] = []

    def current_head(self, head_branch: str) -> str:
        return next(self.heads)

    def fetch_file(self, path: str, ref: str) -> bytes:
        self.fetches.append((path, ref))
        if path == "agent/pyproject.toml":
            return PROJECT
        if path == "agent/uv.lock":
            return OLD_LOCK
        raise AssertionError(f"unexpected fetch path: {path}")


class TrustedDependencyLockResolverTest(unittest.TestCase):
    def test_resolver_exists_and_owns_uv_not_git_write_operations(self) -> None:
        self.assertTrue(RESOLVER_SCRIPT.is_file(), "read-only resolver script is missing")
        source = RESOLVER_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("TemporaryDirectory", source)
        self.assertIn('["uv", "lock"]', source)
        self.assertIn('["uv", "lock", "--check"]', source)
        self.assertNotIn('"POST", "/git/blobs"', source)
        self.assertNotIn('"POST", "/git/trees"', source)
        self.assertNotIn('"POST", "/git/commits"', source)
        self.assertNotIn('"PATCH"', source)

    def test_uv_subprocess_receives_allowlisted_environment_only(self) -> None:
        module = load_module()
        seen_envs = []

        def fake_run(args, *, cwd, check, env):
            self.assertTrue(check)
            seen_envs.append(dict(env))
            Path(cwd, "uv.lock").write_bytes(NEW_LOCK)

        secret_env = {
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "GITHUB_TOKEN": "read-token",
            "GH_TOKEN": "gh-token",
            "ACTIONS_RUNTIME_TOKEN": "runtime-token",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-token",
            "SHOULD_NOT_LEAK": "secret",
        }
        with patch.dict(os.environ, secret_env, clear=True):
            with patch.object(module.subprocess, "run", side_effect=fake_run):
                result = module.regenerate_lock(PROJECT, OLD_LOCK)

        self.assertEqual(result, NEW_LOCK)
        self.assertEqual(len(seen_envs), 2)
        for child_env in seen_envs:
            self.assertIn("PATH", child_env)
            self.assertIn("HOME", child_env)
            for forbidden in (
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "ACTIONS_RUNTIME_TOKEN",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                "SHOULD_NOT_LEAK",
            ):
                self.assertNotIn(forbidden, child_env)

    def test_resolver_fetches_only_exact_head_dependency_inputs(self) -> None:
        module = load_module()
        api = FakeApi([EXPECTED_HEAD, EXPECTED_HEAD])
        with TemporaryDirectory() as temporary:
            with patch.object(module, "regenerate_lock", return_value=NEW_LOCK):
                module.resolve_lock_artifact(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                    artifact_dir=Path(temporary),
                )
        self.assertEqual(
            api.fetches,
            [("agent/pyproject.toml", EXPECTED_HEAD), ("agent/uv.lock", EXPECTED_HEAD)],
        )

    def test_resolver_detects_head_move_before_and_after_resolution(self) -> None:
        module = load_module()
        with TemporaryDirectory() as temporary:
            api = FakeApi([MOVED_HEAD])
            with patch.object(module, "regenerate_lock") as regenerate:
                with self.assertRaisesRegex(module.ApplyError, "moved before"):
                    module.resolve_lock_artifact(
                        api=api,
                        repository="alexsosn/cuc",
                        expected_repository="alexsosn/cuc",
                        base_branch="agent-harness-safety",
                        head_branch="harn-fixture",
                        expected_head=EXPECTED_HEAD,
                        artifact_dir=Path(temporary),
                    )
            regenerate.assert_not_called()

        with TemporaryDirectory() as temporary:
            api = FakeApi([EXPECTED_HEAD, MOVED_HEAD])
            with patch.object(module, "regenerate_lock", return_value=NEW_LOCK):
                with self.assertRaisesRegex(module.ApplyError, "moved during"):
                    module.resolve_lock_artifact(
                        api=api,
                        repository="alexsosn/cuc",
                        expected_repository="alexsosn/cuc",
                        base_branch="agent-harness-safety",
                        head_branch="harn-fixture",
                        expected_head=EXPECTED_HEAD,
                        artifact_dir=Path(temporary),
                    )

    def test_resolver_writes_fixed_lock_and_provenance_digests(self) -> None:
        module = load_module()
        api = FakeApi([EXPECTED_HEAD, EXPECTED_HEAD])
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(module, "regenerate_lock", return_value=NEW_LOCK):
                lock_path, metadata_path = module.resolve_lock_artifact(
                    api=api,
                    repository="alexsosn/cuc",
                    expected_repository="alexsosn/cuc",
                    base_branch="agent-harness-safety",
                    head_branch="harn-fixture",
                    expected_head=EXPECTED_HEAD,
                    artifact_dir=root,
                )
            self.assertEqual(lock_path, root / "uv.lock")
            self.assertEqual(metadata_path, root / "metadata.json")
            self.assertEqual(lock_path.read_bytes(), NEW_LOCK)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["expected_head"], EXPECTED_HEAD)
            self.assertEqual(metadata["repository"], "alexsosn/cuc")
            self.assertEqual(metadata["base_branch"], "agent-harness-safety")
            self.assertEqual(metadata["head_branch"], "harn-fixture")
            self.assertEqual(metadata["uv_version"], "0.12.7")
            self.assertEqual(metadata["pyproject_sha256"], sha256(PROJECT).hexdigest())
            self.assertEqual(metadata["old_lock_sha256"], sha256(OLD_LOCK).hexdigest())
            self.assertEqual(metadata["uv_lock_sha256"], sha256(NEW_LOCK).hexdigest())


if __name__ == "__main__":
    unittest.main()
