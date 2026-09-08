from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "agent" / "scripts" / "verify_dependency_lock_artifact.py"


class DependencyLockArtifactVerifierTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        pyproject = root / "pyproject.toml"
        lock = root / "uv.lock"
        metadata = root / "lock-artifact-metadata.json"
        pyproject.write_text('[project]\nname = "fixture"\nversion = "0.0.0"\n', encoding="utf-8")
        lock.write_text('version = 1\nrevision = 3\nrequires-python = ">=3.13"\n', encoding="utf-8")
        payload = {
            "schema_version": 1,
            "pr_head_sha": "a" * 40,
            "merge_sha": "b" * 40,
            "merge_ref": "refs/pull/30/merge",
            "uv_version": "0.12.7",
            "pyproject_sha256": sha256(pyproject.read_bytes()).hexdigest(),
            "uv_lock_sha256": sha256(lock.read_bytes()).hexdigest(),
        }
        metadata.write_text(json.dumps(payload), encoding="utf-8")
        return pyproject, lock, metadata

    def run_verifier(
        self,
        pyproject: Path,
        lock: Path,
        metadata: Path,
        *,
        head: str = "a" * 40,
        merge: str = "b" * 40,
        merge_ref: str = "refs/pull/30/merge",
        uv_version: str = "0.12.7",
    ) -> subprocess.CompletedProcess[str]:
        self.assertTrue(VERIFIER.is_file(), "dependency lock artifact verifier is missing")
        return subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--metadata",
                str(metadata),
                "--pyproject",
                str(pyproject),
                "--lock",
                str(lock),
                "--expected-head-sha",
                head,
                "--expected-merge-sha",
                merge,
                "--expected-merge-ref",
                merge_ref,
                "--expected-uv-version",
                uv_version,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_exact_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_verifier(*self.fixture(Path(directory)))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_stale_or_wrong_run_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            cases = (
                {"head": "c" * 40},
                {"merge": "d" * 40},
                {"merge_ref": "refs/pull/31/merge"},
                {"uv_version": "0.12.8"},
            )
            for overrides in cases:
                with self.subTest(overrides=overrides):
                    result = self.run_verifier(*fixture, **overrides)
                    self.assertNotEqual(result.returncode, 0)

    def test_rejects_tampered_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pyproject, lock, metadata = self.fixture(Path(directory))
            lock.write_text(lock.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
            result = self.run_verifier(pyproject, lock, metadata)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
