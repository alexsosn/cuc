#!/usr/bin/env python3
"""Resolve a HARN dependency lock without any repository write authority."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from apply_dependency_lock import (
    ApplyError,
    GitHubApi,
    LOCK_PATH,
    PROJECT_PATH,
    UV_VERSION,
    validate_inputs,
)


ARTIFACT_DIR = "trusted-lock-artifact"
ALLOWED_CHILD_ENV = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def child_environment() -> dict[str, str]:
    """Return only OS/network plumbing required by uv, never runner credentials."""
    return {name: os.environ[name] for name in ALLOWED_CHILD_ENV if name in os.environ}


def regenerate_lock(project_bytes: bytes, lock_bytes: bytes) -> bytes:
    with TemporaryDirectory(prefix="cuc-trusted-lock-") as temporary:
        root = Path(temporary)
        (root / "pyproject.toml").write_bytes(project_bytes)
        (root / "uv.lock").write_bytes(lock_bytes)
        environment = child_environment()
        subprocess.run(["uv", "lock"], cwd=root, check=True, env=environment)
        subprocess.run(["uv", "lock", "--check"], cwd=root, check=True, env=environment)
        return (root / "uv.lock").read_bytes()


def resolve_lock_artifact(
    *,
    api: GitHubApi,
    repository: str,
    expected_repository: str,
    base_branch: str,
    head_branch: str,
    expected_head: str,
    artifact_dir: Path = Path(ARTIFACT_DIR),
) -> tuple[Path, Path]:
    validate_inputs(
        repository=repository,
        expected_repository=expected_repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head=expected_head,
    )

    if api.current_head(head_branch) != expected_head:
        raise ApplyError("branch moved before trusted lock resolution")

    project_bytes = api.fetch_file(PROJECT_PATH, expected_head)
    old_lock = api.fetch_file(LOCK_PATH, expected_head)
    new_lock = regenerate_lock(project_bytes, old_lock)

    if api.current_head(head_branch) != expected_head:
        raise ApplyError("branch moved during trusted lock resolution")

    metadata = {
        "schema_version": 1,
        "repository": repository,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "expected_head": expected_head,
        "uv_version": UV_VERSION,
        "pyproject_sha256": _digest(project_bytes),
        "old_lock_sha256": _digest(old_lock),
        "uv_lock_sha256": _digest(new_lock),
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    lock_path = artifact_dir / "uv.lock"
    metadata_path = artifact_dir / "metadata.json"
    lock_path.write_bytes(new_lock)
    metadata_path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return lock_path, metadata_path


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ApplyError(f"missing required environment variable: {name}")
    return value


def main() -> int:
    repository = required_env("REPOSITORY")
    expected_repository = required_env("EXPECTED_REPOSITORY")
    base_branch = required_env("BASE_BRANCH")
    head_branch = required_env("HEAD_BRANCH")
    expected_head = required_env("EXPECTED_HEAD")
    token = required_env("GITHUB_TOKEN")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")

    api = GitHubApi(repository, token, api_url)
    resolve_lock_artifact(
        api=api,
        repository=repository,
        expected_repository=expected_repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head=expected_head,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ApplyError, subprocess.CalledProcessError) as exc:
        print(f"trusted dependency lock resolution failed: {exc}", file=os.sys.stderr)
        raise SystemExit(2) from exc
