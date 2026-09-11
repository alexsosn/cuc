from __future__ import annotations

from hashlib import sha256
import json

import pytest

from harness.development_reviewer import DevelopmentReviewContext


HEAD = "1" * 40
EXECUTED = "2" * 40
BASE = "3" * 40
FINAL_DIFF = "diff --git a/agent/example.py b/agent/example.py\n+value = 1\n"


def _rehash(payload: dict) -> dict:
    identity = {key: value for key, value in payload.items() if key != "review_context_id"}
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["review_context_id"] = "review-context-" + sha256(encoded).hexdigest()
    return payload


def _valid_payload() -> dict:
    identity = {
        "schema_version": 1,
        "development_run_id": "dev-run-restart",
        "task": {
            "task_id": "HARN-006",
            "title": "Independent development review",
            "objective": "Preserve verified evidence across restart",
            "acceptance_criteria": ["clean restartable context"],
        },
        "change": {
            "change_id": "change",
            "changed_paths": ["agent/example.py"],
        },
        "base_sha": BASE,
        "head_sha": HEAD,
        "executed_sha": EXECUTED,
        "final_diff": FINAL_DIFF,
        "diff_sha256": sha256(FINAL_DIFF.encode("utf-8")).hexdigest(),
        "test_evidence": [
            {
                "intent_id": "targeted",
                "kind": "targeted",
                "command": ["python", "-m", "pytest", "-q"],
                "working_directory": "agent",
                "head_sha": HEAD,
                "executed_sha": EXECUTED,
                "outcome": "success",
                "exit_code": 0,
                "passed_tests": 3,
                "failed_tests": 0,
                "evidence_refs": ["ci:targeted"],
            }
        ],
        "eval_evidence": [
            {
                "eval_id": "eval",
                "head_sha": HEAD,
                "executed_sha": EXECUTED,
                "outcome": "success",
                "metrics": {"regressions": 0},
                "evidence_refs": ["eval:1"],
            }
        ],
        "policy_refs": ["AGENTS.md"],
        "rubric": ["attack restart evidence"],
    }
    return _rehash({"review_context_id": "", **identity})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("exit_code", 1),
        ("failed_tests", 1),
        ("passed_tests", 0),
    ),
)
def test_rehashed_success_test_evidence_must_remain_semantically_successful(
    field: str,
    value: int,
) -> None:
    payload = _valid_payload()
    payload["test_evidence"] = [dict(payload["test_evidence"][0])]
    payload["test_evidence"][0][field] = value
    _rehash(payload)

    with pytest.raises(ValueError, match="success|test|verification"):
        DevelopmentReviewContext.from_dict(payload)
