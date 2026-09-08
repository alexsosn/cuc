#!/usr/bin/env python3
"""Fail closed unless a downloaded uv lock artifact matches the expected PR run."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Mapping


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_FIELDS = {
    "schema_version",
    "pr_head_sha",
    "merge_sha",
    "merge_ref",
    "uv_version",
    "pyproject_sha256",
    "uv_lock_sha256",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_metadata(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read artifact metadata: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("artifact metadata must be a JSON object")
    missing = _REQUIRED_FIELDS - set(payload)
    if missing:
        raise ValueError(f"artifact metadata missing fields: {sorted(missing)}")
    if payload["schema_version"] != 1:
        raise ValueError("unsupported artifact metadata schema_version")
    return payload


def verify(
    *,
    metadata_path: Path,
    pyproject_path: Path,
    lock_path: Path,
    expected_head_sha: str,
    expected_merge_sha: str,
    expected_merge_ref: str,
    expected_uv_version: str,
) -> None:
    for value, field in (
        (expected_head_sha, "expected_head_sha"),
        (expected_merge_sha, "expected_merge_sha"),
    ):
        if not _SHA_RE.fullmatch(value):
            raise ValueError(f"{field} must be a lowercase 40-character Git SHA")

    metadata = _load_metadata(metadata_path)
    expected = {
        "pr_head_sha": expected_head_sha,
        "merge_sha": expected_merge_sha,
        "merge_ref": expected_merge_ref,
        "uv_version": expected_uv_version,
    }
    for field, value in expected.items():
        if metadata[field] != value:
            raise ValueError(
                f"artifact {field} mismatch: expected {value!r}, got {metadata[field]!r}"
            )

    actual_pyproject = _sha256(pyproject_path)
    actual_lock = _sha256(lock_path)
    if metadata["pyproject_sha256"] != actual_pyproject:
        raise ValueError("artifact pyproject.toml digest mismatch")
    if metadata["uv_lock_sha256"] != actual_lock:
        raise ValueError("artifact uv.lock digest mismatch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--pyproject", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--expected-merge-sha", required=True)
    parser.add_argument("--expected-merge-ref", required=True)
    parser.add_argument("--expected-uv-version", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        verify(
            metadata_path=args.metadata,
            pyproject_path=args.pyproject,
            lock_path=args.lock,
            expected_head_sha=args.expected_head_sha,
            expected_merge_sha=args.expected_merge_sha,
            expected_merge_ref=args.expected_merge_ref,
            expected_uv_version=args.expected_uv_version,
        )
    except (OSError, ValueError) as exc:
        print(f"dependency lock artifact verification failed: {exc}", file=sys.stderr)
        return 2
    print("dependency lock artifact verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
