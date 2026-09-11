from __future__ import annotations

import pytest

from harness.contracts import (
    ChangeSet,
    GateOutcome,
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
from harness.github_effects import (
    GitHubAction,
    GitHubEffectGateway,
    GitHubEffectJournal,
    GitHubEffectRequest,
    GitHubOperationPermission,
    GitHubTaskPolicy,
)


BASE = "a" * 40
HEAD = "b" * 40
FORK = "alexsosn/cuc"


class Adapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(self, request):
        self.calls.append((request.action.value, request.operation_id))
        return f"issue:{request.operation_id}"


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


def _policy(*, max_github_writes: int = 1) -> DevelopmentControllerPolicy:
    return DevelopmentControllerPolicy(
        max_revision_attempts=2,
        max_verification_executions=1,
        max_review_attempts=1,
        max_github_writes=max_github_writes,
        max_cost_units=10.0,
        production_mode=True,
    )


def _gateway(requests, adapter):
    permissions = tuple(
        GitHubOperationPermission(item.operation_id, item.action) for item in requests
    )
    return GitHubEffectGateway(
        GitHubTaskPolicy(allowed_fork_write_operations=permissions),
        adapter,
    )


def _controller(gateway, persist, *, policy=None) -> BoundedDevelopmentController:
    return BoundedDevelopmentController(
        policy=policy or _policy(),
        ports=_ports(),
        reviewer=_reviewer(),
        github_gateway=gateway,
        persist=persist,
        policy_refs=("AGENTS.md", "HARN-023"),
        review_rubric=("termination", "replay safety"),
        implementer_id="implementer",
    )


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-11",
        "controller",
        "preserve replay-safe bounded writes",
        ("duplicate retry cannot bypass GitHub write budget",),
    )


def _targeted() -> TestIntent:
    return TestIntent(
        "targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "baseline RED",
    )


def _red(intent: TestIntent) -> RedGateEvidence:
    return RedGateEvidence(
        intent.intent_id,
        BASE,
        GateOutcome.TEST_FAILURE,
        1,
        0,
        1,
        "expected baseline failure",
    )


def _pending_state() -> DevelopmentControllerState:
    intent = _targeted()
    core = RunState(
        "run-11",
        _task(),
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ()),
        plan=PlanArtifact("plan", "done", ("test", "implement")),
        test_intents=(intent,),
    )
    op1 = GitHubEffectRequest(
        "write-1", FORK, GitHubAction.CREATE_ISSUE, {"title": "first"}
    )
    op2 = GitHubEffectRequest(
        "write-2", FORK, GitHubAction.CREATE_ISSUE, {"title": "second"}
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
    return DevelopmentControllerState(
        1,
        BASE,
        core,
        red_evidence=(_red(intent),),
        pending_implementation=implementation,
        current_head_sha=HEAD,
        revision_attempts=1,
        github_journal=GitHubEffectJournal(),
    )


def test_replayed_completed_write_consumes_budget_after_controller_snapshot_crash() -> None:
    state = _pending_state()
    requests = state.pending_implementation.github_operations
    adapter = Adapter()
    gateway = _gateway(requests, adapter)

    durable_snapshots: list[dict[str, object]] = []
    persist_calls = 0

    def crash_after_completed_journal_checkpoint(payload):
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 3:
            raise SimulatedControllerCrash(
                "controller died after durable receipt but before index/counter snapshot"
            )
        durable_snapshots.append(payload)

    crashing = _controller(gateway, crash_after_completed_journal_checkpoint)
    with pytest.raises(SimulatedControllerCrash):
        crashing.step(state)

    assert adapter.calls == [(GitHubAction.CREATE_ISSUE.value, "write-1")]
    assert len(durable_snapshots) == 2
    restored = DevelopmentControllerState.from_dict(durable_snapshots[-1])
    assert restored.pending_operation_index == 0
    assert restored.github_writes == 0
    assert restored.github_journal.receipt_for("write-1") is not None

    resumed_snapshots: list[dict[str, object]] = []
    resumed = _controller(gateway, resumed_snapshots.append)
    replayed = resumed.step(restored)

    assert replayed.pending_operation_index == 1
    assert replayed.github_writes == 1
    assert adapter.calls == [(GitHubAction.CREATE_ISSUE.value, "write-1")]

    blocked = resumed.step(replayed)
    assert blocked.stop_code is ControllerStopCode.GITHUB_WRITE_BUDGET_EXHAUSTED
    assert adapter.calls == [(GitHubAction.CREATE_ISSUE.value, "write-1")]


def test_reused_operation_id_is_blocked_before_replay_or_provider_dispatch() -> None:
    intent = _targeted()
    reused = GitHubEffectRequest(
        "write-1", FORK, GitHubAction.CREATE_ISSUE, {"title": "same"}
    )
    prior = ChangeSet(
        "prior-change",
        "prior write",
        ("agent/harness/prior.py",),
        (reused.operation_id,),
    )
    core = RunState(
        "run-11",
        _task(),
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ()),
        plan=PlanArtifact("plan", "done", ("test", "implement")),
        test_intents=(intent,),
        changes=(prior,),
    )
    pending = ImplementationResult(
        ChangeSet(
            "new-change",
            "must not reuse operation",
            ("agent/harness/new.py",),
            (reused.operation_id,),
        ),
        HEAD,
        (reused,),
    )
    state = DevelopmentControllerState(
        1,
        BASE,
        core,
        red_evidence=(_red(intent),),
        pending_implementation=pending,
        current_head_sha=HEAD,
        revision_attempts=2,
        github_journal=GitHubEffectJournal(),
    )

    adapter = Adapter()
    gateway = _gateway((reused,), adapter)
    controller = _controller(gateway, lambda _payload: None, policy=_policy(max_github_writes=5))
    blocked = controller.step(state)

    assert blocked.core.phase is RunPhase.BLOCKED
    assert blocked.stop_code is ControllerStopCode.POLICY_BLOCK
    assert "operation" in blocked.stop_reason.casefold()
    assert "reuse" in blocked.stop_reason.casefold()
    assert adapter.calls == []
