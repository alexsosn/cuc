from __future__ import annotations

import pytest

from harness.contracts import ChangeSet
from harness.development_controller import ImplementationResult
from harness.github_effects import GitHubAction, GitHubEffectRequest


def test_implementation_result_cannot_schedule_merge_before_verification_and_review() -> None:
    merge = GitHubEffectRequest(
        "premature-merge",
        "alexsosn/cuc",
        GitHubAction.MERGE_PULL_REQUEST,
        {"pull_number": 59},
        target_ref="agent-harness-safety",
    )
    change = ChangeSet(
        "candidate",
        "candidate implementation",
        ("agent/harness/example.py",),
        (merge.operation_id,),
    )

    with pytest.raises(ValueError, match="MERGE_PULL_REQUEST|merge|finaliz|review"):
        ImplementationResult(change, "b" * 40, (merge,))
