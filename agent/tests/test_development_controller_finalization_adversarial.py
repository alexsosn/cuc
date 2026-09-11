from __future__ import annotations

import pytest

from harness.contracts import (
    ChangeSet,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent as HarnessTestIntent,
    TestKind as HarnessTestKind,
)
from harness.development_controller import (
    ControllerStopCode,
    DevelopmentControllerState,
    ImplementationResult,
)
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


def test_terminal_complete_code_requires_harn002_complete_phase() -> None:
    intent = HarnessTestIntent(
        "targeted",
        HarnessTestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "baseline RED",
    )
    core = RunState(
        "run-terminal-integrity",
        TaskSpec(
            "issue-11",
            "controller",
            "do not trust fabricated terminal status",
            ("completion requires verified independent review",),
        ),
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ()),
        plan=PlanArtifact("plan", "done", ("test", "implement")),
        test_intents=(intent,),
    )

    with pytest.raises(ValueError, match="COMPLETE|complete|terminal|phase"):
        DevelopmentControllerState(
            1,
            "a" * 40,
            core,
            stop_code=ControllerStopCode.COMPLETE,
            stop_reason="fabricated successful completion",
        )
