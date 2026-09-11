from __future__ import annotations

from dataclasses import replace

import pytest

from harness.contracts import (
    ChangeSet,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent as ContractTestIntent,
    TestKind as ContractTestKind,
)
from harness.development_controller import (
    ControllerStopCode,
    DevelopmentControllerPolicy,
    DevelopmentControllerPorts,
    DevelopmentControllerState,
    ImplementationResult,
)
from harness.development_reviewer import IndependentReviewer
from harness.github_effects import (
    GitHubAction,
    GitHubEffectJournal,
    GitHubEffectRequest,
    GitHubOperationPermission,
    GitHubReconciliationDisposition,
    GitHubReconciliationResult,
    GitHubTaskPolicy,
    HumanApproval,
)
from harness.production_host import (
    AtomicHostStateStore,
    ProductionDevelopmentHost,
    ProductionHostEnvelope,
)


BASE = "a" * 40
HEAD = "b" * 40
FORK = "alexsosn/cuc"


class Adapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, request: GitHubEffectRequest) -> str:
        self.calls.append(request.operation_id)
        return f"result:{request.operation_id}"


class Reconciler:
    def reconcile(self, request: GitHubEffectRequest) -> GitHubReconciliationResult:
        return GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)


def _never(*_args, **_kwargs):
    raise AssertionError("model-facing port must not be called")


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


def _policy() -> DevelopmentControllerPolicy:
    return DevelopmentControllerPolicy(
        max_revision_attempts=2,
        max_verification_executions=2,
        max_review_attempts=2,
        max_github_writes=2,
        max_cost_units=10.0,
        require_red=False,
        production_mode=True,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-53-review",
        "production host review",
        "fail closed around trusted authority and recovery",
        ("invalid resume cannot create durable authority",),
    )


def _request() -> GitHubEffectRequest:
    return GitHubEffectRequest(
        "sensitive-op",
        FORK,
        GitHubAction.DISPATCH_WORKFLOW,
        {"workflow": "tests.yml"},
        target_ref="harn-010-production-host",
    )


def _pending_implement_state(request: GitHubEffectRequest) -> DevelopmentControllerState:
    intent = ContractTestIntent(
        "host-review-targeted",
        ContractTestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_production_host_review_adversarial.py"),
        "agent",
        "host review gate",
    )
    core = RunState(
        "run-53-review",
        _task(),
        phase=RunPhase.IMPLEMENT,
        research=ResearchArtifact("research", "done", ("issue:53",)),
        plan=PlanArtifact("plan", "done", ("persist", "authorize")),
        test_intents=(intent,),
    )
    implementation = ImplementationResult(
        ChangeSet(
            "change-review",
            "pending sensitive effect",
            ("agent/harness/example.py",),
            (request.operation_id,),
        ),
        HEAD,
        (request,),
    )
    return DevelopmentControllerState(
        1,
        BASE,
        core,
        pending_implementation=implementation,
        current_head_sha=HEAD,
        revision_attempts=1,
        github_journal=GitHubEffectJournal(),
    )


def _awaiting_human_state(
    request: GitHubEffectRequest,
    *,
    journal: GitHubEffectJournal | None = None,
) -> DevelopmentControllerState:
    state = _pending_implement_state(request)
    reason = "human approval required"
    return replace(
        state,
        core=replace(
            state.core,
            phase=RunPhase.AWAITING_HUMAN,
            resume_phase=RunPhase.IMPLEMENT,
            pause_reason=reason,
        ),
        github_journal=journal or GitHubEffectJournal(),
        stop_code=ControllerStopCode.NEEDS_HUMAN,
        stop_reason=reason,
    )


def _github_policy(request: GitHubEffectRequest) -> GitHubTaskPolicy:
    return GitHubTaskPolicy(
        allowed_fork_write_operations=(
            GitHubOperationPermission(request.operation_id, request.action),
        )
    )


def _host(
    store: AtomicHostStateStore,
    request: GitHubEffectRequest,
    *,
    reconciler=Reconciler(),
) -> ProductionDevelopmentHost:
    return ProductionDevelopmentHost(
        store=store,
        policy=_policy(),
        ports=_ports(),
        reviewer=IndependentReviewer("reviewer", _never, implementer_id="implementer"),
        github_policy=_github_policy(request),
        github_adapter=Adapter(),
        github_reconciler=reconciler,
        policy_refs=("AGENTS.md", "HARN-023", "issue:53"),
        review_rubric=("authority", "recovery"),
        implementer_id="implementer",
    )


def test_invalid_resume_does_not_persist_or_register_approval_authority(tmp_path) -> None:
    request = _request()
    state = _pending_implement_state(request)
    store = AtomicHostStateStore(tmp_path / "host.json")
    store.save(ProductionHostEnvelope(1, state, ()))
    host = _host(store, request)
    approval = HumanApproval(
        "approval-review",
        "human",
        request.operation_id,
        request.request_sha256,
    )

    with pytest.raises(ValueError, match="awaiting|resume|human"):
        host.resume(approval=approval)

    durable = store.load()
    assert durable is not None
    assert durable.trusted_approvals == ()
    assert durable.controller_state is not None
    assert durable.controller_state.github_journal.approval_for(request.operation_id) is None


def test_conflicting_journal_approval_is_rejected_before_new_authority_is_persisted(
    tmp_path,
) -> None:
    request = _request()
    untrusted = HumanApproval(
        "forged-journal-approval",
        "not-trusted",
        request.operation_id,
        request.request_sha256,
    )
    genuine = HumanApproval(
        "genuine-human-approval",
        "human",
        request.operation_id,
        request.request_sha256,
    )
    state = _awaiting_human_state(
        request,
        journal=GitHubEffectJournal().with_approval(untrusted),
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    store.save(ProductionHostEnvelope(1, state, ()))
    host = _host(store, request)

    with pytest.raises(ValueError, match="approval|conflict|different"):
        host.resume(approval=genuine)

    durable = store.load()
    assert durable is not None
    assert durable.trusted_approvals == ()
    assert durable.controller_state is not None
    assert durable.controller_state.github_journal.approval_for(request.operation_id) == untrusted


def test_production_host_requires_a_trusted_reconciler(tmp_path) -> None:
    request = _request()
    store = AtomicHostStateStore(tmp_path / "host.json")

    with pytest.raises(ValueError, match="reconciler|recovery"):
        _host(store, request, reconciler=None)
