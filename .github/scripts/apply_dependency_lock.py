#!/usr/bin/env python3
"""Verify a trusted lock artifact and commit only agent/uv.lock."""

from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_PATH = "agent/pyproject.toml"
LOCK_PATH = "agent/uv.lock"
ARTIFACT_LOCK_PATH = "trusted-lock-artifact/uv.lock"
ARTIFACT_METADATA_PATH = "trusted-lock-artifact/metadata.json"
UV_VERSION = "0.12.7"
BRANCH_RE = re.compile(r"^harn-[A-Za-z0-9._-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_BRANCHES = {"main", "agent-harness-safety"}
API_VERSION = "2026-03-10"
METADATA_KEYS = {
    "schema_version",
    "repository",
    "base_branch",
    "head_branch",
    "expected_head",
    "uv_version",
    "pyproject_sha256",
    "old_lock_sha256",
    "uv_lock_sha256",
}


class ApplyError(RuntimeError):
    """Raised when a trusted-writer invariant fails."""


class GitHubApi:
    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com") -> None:
        self.repository = repository
        self.token = token
        self.base = f"{api_url.rstrip('/')}/repos/{repository}"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "cuc-trusted-lock-applier",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise ApplyError(f"GitHub API {method} {path} failed: {exc.code}: {details}") from exc
        if not data:
            return {}
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ApplyError(f"GitHub API {method} {path} returned a non-object response")
        return decoded

    def current_head(self, head_branch: str) -> str:
        branch = quote(head_branch, safe="")
        payload = self.request("GET", f"/git/ref/heads/{branch}")
        try:
            commit_sha = payload["object"]["sha"]
        except (KeyError, TypeError) as exc:
            raise ApplyError("branch ref response lacks object.sha") from exc
        if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
            raise ApplyError("branch ref returned an invalid commit SHA")
        return commit_sha

    def fetch_file(self, path: str, ref: str) -> bytes:
        if path not in {PROJECT_PATH, LOCK_PATH}:
            raise ApplyError(f"refusing to fetch non-dependency path: {path}")
        payload = self.request("GET", f"/contents/{path}?ref={quote(ref, safe='')}")
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise ApplyError(f"unexpected Contents API response for {path}")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ApplyError(f"Contents API response lacks content for {path}")
        try:
            return base64.b64decode(content, validate=False)
        except ValueError as exc:
            raise ApplyError(f"invalid base64 content for {path}") from exc

    def parent_tree(self, expected_head: str) -> str:
        payload = self.request("GET", f"/git/commits/{expected_head}")
        try:
            tree_sha = payload["tree"]["sha"]
        except (KeyError, TypeError) as exc:
            raise ApplyError("parent commit response lacks tree.sha") from exc
        if not isinstance(tree_sha, str) or not SHA_RE.fullmatch(tree_sha):
            raise ApplyError("parent commit returned an invalid tree SHA")
        return tree_sha


def validate_inputs(
    *,
    repository: str,
    expected_repository: str,
    base_branch: str,
    head_branch: str,
    expected_head: str,
) -> None:
    if expected_repository != repository:
        raise ApplyError("workflow-run head repository does not match current repository")
    if base_branch != "agent-harness-safety":
        raise ApplyError("trusted lock applier only serves agent-harness-safety PRs")
    if not BRANCH_RE.fullmatch(head_branch):
        raise ApplyError("head branch does not match the trusted harn-* policy")
    if head_branch in FORBIDDEN_BRANCHES:
        raise ApplyError("refusing to write a protected integration/default branch")
    if not SHA_RE.fullmatch(expected_head):
        raise ApplyError("expected head must be a lowercase 40-character Git SHA")


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def verify_artifact(
    *,
    artifact_lock_path: Path,
    artifact_metadata_path: Path,
    repository: str,
    base_branch: str,
    head_branch: str,
    expected_head: str,
    project_bytes: bytes,
    old_lock: bytes,
) -> bytes:
    try:
        lock_bytes = artifact_lock_path.read_bytes()
        metadata_raw = artifact_metadata_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"trusted lock artifact is unreadable: {exc}") from exc

    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError as exc:
        raise ApplyError("trusted lock artifact metadata is not valid JSON") from exc
    if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
        raise ApplyError("trusted lock artifact metadata schema mismatch")

    expected_values = {
        "schema_version": 1,
        "repository": repository,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "expected_head": expected_head,
        "uv_version": UV_VERSION,
        "pyproject_sha256": _digest(project_bytes),
        "old_lock_sha256": _digest(old_lock),
        "uv_lock_sha256": _digest(lock_bytes),
    }
    for key, expected in expected_values.items():
        if metadata.get(key) != expected:
            raise ApplyError(f"trusted lock artifact metadata mismatch: {key}")
    return lock_bytes


def apply_lock_update(
    *,
    api: GitHubApi,
    repository: str,
    expected_repository: str,
    base_branch: str,
    head_branch: str,
    expected_head: str,
    artifact_lock_path: Path = Path(ARTIFACT_LOCK_PATH),
    artifact_metadata_path: Path = Path(ARTIFACT_METADATA_PATH),
) -> str | None:
    validate_inputs(
        repository=repository,
        expected_repository=expected_repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head=expected_head,
    )

    if api.current_head(head_branch) != expected_head:
        raise ApplyError("branch moved before trusted lock apply")

    project_bytes = api.fetch_file(PROJECT_PATH, expected_head)
    old_lock = api.fetch_file(LOCK_PATH, expected_head)
    new_lock = verify_artifact(
        artifact_lock_path=artifact_lock_path,
        artifact_metadata_path=artifact_metadata_path,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head=expected_head,
        project_bytes=project_bytes,
        old_lock=old_lock,
    )
    if new_lock == old_lock:
        print("uv.lock is already current; no trusted write required")
        return None

    blob = api.request(
        "POST",
        "/git/blobs",
        {"content": base64.b64encode(new_lock).decode("ascii"), "encoding": "base64"},
    )
    blob_sha = blob.get("sha")
    if not isinstance(blob_sha, str) or not SHA_RE.fullmatch(blob_sha):
        raise ApplyError("created lock blob returned an invalid SHA")

    base_tree = api.parent_tree(expected_head)
    tree = api.request(
        "POST",
        "/git/trees",
        {
            "base_tree": base_tree,
            "tree": [
                {
                    "path": LOCK_PATH,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            ],
        },
    )
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or not SHA_RE.fullmatch(tree_sha):
        raise ApplyError("created lock tree returned an invalid SHA")

    commit = api.request(
        "POST",
        "/git/commits",
        {
            "message": "chore: apply trusted uv lock update",
            "tree": tree_sha,
            "parents": [expected_head],
        },
    )
    commit_sha = commit.get("sha")
    if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
        raise ApplyError("created lock commit returned an invalid SHA")

    if api.current_head(head_branch) != expected_head:
        raise ApplyError("branch moved during trusted lock apply; refusing stale ref update")

    api.request(
        "PATCH",
        f"/git/refs/heads/{quote(head_branch, safe='')}",
        {"sha": commit_sha, "force": False},
    )
    print(f"trusted uv.lock commit created: {commit_sha}")
    return commit_sha


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
    apply_lock_update(
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
    except ApplyError as exc:
        print(f"trusted dependency lock apply failed: {exc}", file=os.sys.stderr)
        raise SystemExit(2) from exc
