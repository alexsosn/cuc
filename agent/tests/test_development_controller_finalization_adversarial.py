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
    BoundedDevelopmentController,
    ControllerStopCode,
    DevelopmentControllerPolicy,
    DevelopmentControllerPorts,
    DevelopmentControllerState,
    ImplementationResult,
)
from harness.development_reviewer import IndependentReviewer
from harness.github_effects import (
    GitHubAction,
    GitHubEffectGateway,
    GitHubEffectRequest,
    GitHubTaskPolicy,
)


def _initial_implement_core() -> RunState:
    intent = HarnessTestIntent(
        "targeted",
        HarnessTestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "baseline RED",
    )
    return RunState(
        "run-terminal-integrity",
        TaskSpec(
            "issue-11",
            "controller",
            "preserve durable controller integrity",
            ("completion and pending revisions must be reconstructable",),
        ),
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ()),
        plan=PlanArtifact("plan", "done", ("test", "implement")),
        test_intents=(intent,),
    )


def _never(*_args, **_kwargs):
    raise AssertionError("port must not be called")


class _Adapter:
    def execute(self, _request):
        raise AssertionError("GitHub adapter must not be called")


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
    with pytest.raises(ValueError, match="COMPLETE|complete|terminal|phase"):
        DevelopmentControllerState(
            1,
            "a" * 40,
            _initial_implement_core(),
            stop_code=ControllerStopCode.COMPLETE,
            stop_reason="fabricated successful completion",
        )


def test_terminal_stop_code_requires_explicit_stop_reason() -> None:
    implement = _initial_implement_core()
    blocked = RunState(
        implement.run_id,
        implement.task,
        research=implement.research,
        plan=implement.plan,
        test_intents=implement.test_intents,
        phase=RunPhase.BLOCKED,
        resume_phase=RunPhase.IMPLEMENT,
        pause_reason="policy blocked",
    )

    with pytest.raises(ValueError, match="reason|terminal|stop"):
        DevelopmentControllerState(
            1,
            "a" * 40,
            blocked,
            stop_code=ControllerStopCode.POLICY_BLOCK,
        )


def test_pending_implementation_head_must_match_current_head() -> None:
    request = GitHubEffectRequest(
        "write-head-bound",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "head-bound operation"},
    )
    pending = ImplementationResult(
        ChangeSet(
            "candidate-head-bound",
            "candidate implementation",
            ("agent/harness/example.py",),
            (request.operation_id,),
        ),
        "b" * 40,
        (request,),
    )

    with pytest.raises(ValueError, match="head|revision|pending"):
        DevelopmentControllerState(
            1,
            "a" * 40,
            _initial_implement_core(),
            pending_implementation=pending,
            current_head_sha="c" * 40,
            revision_attempts=1,
        )


def test_controller_requires_reviewer_bound_to_declared_implementer_identity() -> None:
    ports = DevelopmentControllerPorts(
        research=_never,
        plan=_never,
        declare_tests=_never,
        run_red=_never,
        implement=_never,
        run_test=_never,
        run_evals=_never,
        final_diff=_never,
    )
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=1,
        max_verification_executions=1,
        max_review_attempts=1,
        max_github_writes=0,
    )
    reviewer = IndependentReviewer("implementer", _never)
    gateway = GitHubEffectGateway(GitHubTaskPolicy(), _Adapter())

    with pytest.raises(ValueError, match="independent|reviewer|implementer"):
        BoundedDevelopmentController(
            policy=policy,
            ports=ports,
            reviewer=reviewer,
            github_gateway=gateway,
            persist=lambda _payload: None,
            policy_refs=("AGENTS.md",),
            review_rubric=("independence",),
            implementer_id="implementer",
        )
