from __future__ import annotations

import pytest

from harness.contracts import (
    ChangeSet,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent,
    TestKind,
)
from harness.development_controller import (
    BoundedDevelopmentController,
    ControllerStopCode,
    DevelopmentControllerPolicy,
    DevelopmentControllerPorts,
    DevelopmentControllerState,
    ImplementationResult,
    RedGateEvidence,
)
from harness.development_reviewer import IndependentReviewer
from harness.github_side_effects import (
    GitHubOperationIntent,
    GitHubOperationKind,
    GitHubTarget,
    GuardedGitHubSideEffects,
    HumanApprovalRegistry,
    OperationJournal,
)
from harness.contracts import GateOutcome


BASE = "a" * 40
HEAD = "b" * 40
FORK = GitHubTarget("alexsosn", "cuc")


class Adapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.results: dict[str, str] = {}

    def read(self, intent):
        raise AssertionError("read is not expected")

    def reconcile(self, intent):
        self.calls.append(("reconcile", intent.operation_id))
        return self.results.get(intent.operation_id)

    def create_issue(self, intent):
        self.calls.append(("create_issue", intent.operation_id))
        result = f"issue:{intent.operation_id}"
        self.results[intent.operation_id] = result
        return result


class SimulatedControllerCrash(RuntimeError):
    pass


def _never(*_args, **_kwargs):
    raise AssertionError("port must not be called in this scenario")


def _ports() -> DevelopmentControllerPorts:
    return DevelopmentControllerPorts(
        research=_never,
        plan=_never,
        declare_tests=_never,
        run_red=_never,
        implement=_never,
        run_test=_never,
        run_evals=_never,
        final_diff=_never,
    )


def _reviewer() -> IndependentReviewer:
    return IndependentReviewer("clean-reviewer", _never, implementer_id="implementer")


def _policy() -> DevelopmentControllerPolicy:
    return DevelopmentControllerPolicy(
        max_revision_attempts=1,
        max_verification_executions=1,
        max_review_attempts=1,
        max_github_writes=1,
        max_cost_units=10.0,
        production_mode=True,
    )


def _controller(guard, persist) -> BoundedDevelopmentController:
    return BoundedDevelopmentController(
        policy=_policy(),
        ports=_ports(),
        reviewer=_reviewer(),
        side_effects=guard,
        persist=persist,
        approval_state_persist=lambda _payload: None,
        policy_refs=("AGENTS.md", "HARN-009"),
        review_rubric=("termination", "replay safety"),
        implementer_id="implementer",
    )


def _pending_state() -> DevelopmentControllerState:
    task = TaskSpec(
        "issue-11",
        "controller",
        "preserve replay-safe bounded writes",
        ("duplicate retry cannot bypass GitHub write budget",),
    )
    intent = TestIntent(
        "targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "baseline RED",
    )
    core = RunState(
        "run-11",
        task,
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ()),
        plan=PlanArtifact("plan", "done", ("test", "implement")),
        test_intents=(intent,),
    )
    op1 = GitHubOperationIntent(
        "write-1", GitHubOperationKind.CREATE_ISSUE, FORK, {"title": "first"}
    )
    op2 = GitHubOperationIntent(
        "write-2", GitHubOperationKind.CREATE_ISSUE, FORK, {"title": "second"}
    )
    implementation = ImplementationResult(
        ChangeSet(
            "change-1",
            "two writes",
            ("agent/harness/example.py",),
            (op1.operation_id, op2.operation_id),
        ),
        HEAD,
        (op1, op2),
    )
    red = RedGateEvidence(
        intent.intent_id,
        BASE,
        GateOutcome.TEST_FAILURE,
        1,
        0,
        1,
        "expected baseline failure",
    )
    return DevelopmentControllerState(
        1,
        BASE,
        core,
        red_evidence=(red,),
        pending_implementation=implementation,
        current_head_sha=HEAD,
        revision_attempts=1,
    )


def test_replayed_completed_write_consumes_budget_after_controller_snapshot_crash() -> None:
    adapter = Adapter()
    journal_snapshots: list[dict[str, object]] = []
    journal = OperationJournal(persist=journal_snapshots.append)
    guard = GuardedGitHubSideEffects(
        adapter=adapter,
        journal=journal,
        approvals=HumanApprovalRegistry(),
    )
    durable_before_dispatch = _pending_state().to_dict()

    def crash_controller_persist(_payload):
        raise SimulatedControllerCrash(
            "controller died after HARN-009 completion but before controller snapshot"
        )

    crashing = _controller(guard, crash_controller_persist)
    with pytest.raises(SimulatedControllerCrash):
        crashing.step(DevelopmentControllerState.from_dict(durable_before_dispatch))

    assert journal.status("write-1") == "completed"
    assert [call for call in adapter.calls if call[0] == "create_issue"] == [
        ("create_issue", "write-1")
    ]

    restored = DevelopmentControllerState.from_dict(durable_before_dispatch)
    persisted_after_restart: list[dict[str, object]] = []
    resumed = _controller(guard, persisted_after_restart.append)

    replayed = resumed.step(restored)
    assert replayed.pending_operation_index == 1
    assert replayed.github_writes == 1

    blocked = resumed.step(replayed)
    assert blocked.stop_code is ControllerStopCode.GITHUB_WRITE_BUDGET_EXHAUSTED
    assert [call for call in adapter.calls if call[0] == "create_issue"] == [
        ("create_issue", "write-1")
    ]
