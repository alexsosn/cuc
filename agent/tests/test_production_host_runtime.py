from __future__ import annotations

import pytest

import harness.production_host as production_host
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
    GitHubEffectUncertainRequest,
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
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.ambiguous = ambiguous
        self.calls: list[str] = []

    def execute(self, request: GitHubEffectRequest) -> str:
        self.calls.append(request.operation_id)
        if self.ambiguous:
            raise RuntimeError("provider response lost after possible mutation")
        return f"result:{request.operation_id}"


class Reconciler:
    def __init__(self, result: GitHubReconciliationResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def reconcile(self, request: GitHubEffectRequest) -> GitHubReconciliationResult:
        self.calls.append(request.operation_id)
        return self.result


def _never(*_args, **_kwargs):
    raise AssertionError("model-facing controller port must not be called")


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


def _controller_policy() -> DevelopmentControllerPolicy:
    return DevelopmentControllerPolicy(
        max_revision_attempts=2,
        max_verification_executions=2,
        max_review_attempts=2,
        max_github_writes=4,
        max_cost_units=10.0,
        require_red=False,
        production_mode=True,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-53",
        "durable host",
        "restore exact trusted runtime state",
        ("uncertain writes are reconciled without blind redispatch",),
    )


def _targeted() -> TestIntent:
    return TestIntent(
        "host-targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_production_host_runtime.py"),
        "agent",
        "production host runtime gate",
    )


def _request(*, sensitive: bool = False, payload_title: str = "one") -> GitHubEffectRequest:
    if sensitive:
        return GitHubEffectRequest(
            "operation-1",
            FORK,
            GitHubAction.DISPATCH_WORKFLOW,
            {"workflow": "tests.yml", "inputs": {"title": payload_title}},
            target_ref="harn-010-production-host",
        )
    return GitHubEffectRequest(
        "operation-1",
        FORK,
        GitHubAction.CREATE_ISSUE,
        {"title": payload_title},
    )


def _github_policy(request: GitHubEffectRequest) -> GitHubTaskPolicy:
    return GitHubTaskPolicy(
        allowed_fork_write_operations=(
            GitHubOperationPermission(request.operation_id, request.action),
        )
    )


def _pending_state(
    request: GitHubEffectRequest,
    *,
    phase: RunPhase = RunPhase.IMPLEMENT,
    journal: GitHubEffectJournal | None = None,
    stop_code: ControllerStopCode | None = None,
    stop_reason: str | None = None,
) -> DevelopmentControllerState:
    resume_phase = RunPhase.IMPLEMENT if phase in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN} else None
    pause_reason = stop_reason if resume_phase is not None else None
    core = RunState(
        "run-53",
        _task(),
        phase=phase,
        research=ResearchArtifact("research-53", "host research complete", ("issue:53",)),
        plan=PlanArtifact("plan-53", "host plan complete", ("persist", "reconcile")),
        test_intents=(_targeted(),),
        resume_phase=resume_phase,
        pause_reason=pause_reason,
    )
    implementation = ImplementationResult(
        ChangeSet(
            "change-1",
            "pending provider effect",
            ("agent/harness/example.py",),
            (request.operation_id,),
        ),
        HEAD,
        (request,),
    )
    return DevelopmentControllerState(
        schema_version=1,
        base_sha=BASE,
        core=core,
        pending_implementation=implementation,
        current_head_sha=HEAD,
        revision_attempts=1,
        github_journal=journal or GitHubEffectJournal(),
        stop_code=stop_code,
        stop_reason=stop_reason,
    )


def _host(
    store: AtomicHostStateStore,
    request: GitHubEffectRequest,
    adapter: Adapter,
    reconciler: Reconciler,
) -> ProductionDevelopmentHost:
    return ProductionDevelopmentHost(
        store=store,
        policy=_controller_policy(),
        ports=_ports(),
        reviewer=_reviewer(),
        github_policy=_github_policy(request),
        github_adapter=adapter,
        github_reconciler=reconciler,
        policy_refs=("AGENTS.md", "HARN-023", "issue:53"),
        review_rubric=("durability", "authority separation", "reconciliation"),
        implementer_id="implementer",
    )


def _save_state(store: AtomicHostStateStore, state: DevelopmentControllerState) -> None:
    store.save(ProductionHostEnvelope(1, state, ()))


def test_production_host_requires_explicit_durable_store() -> None:
    request = _request()
    with pytest.raises(ValueError, match="store|durable"):
        ProductionDevelopmentHost(
            store=None,
            policy=_controller_policy(),
            ports=_ports(),
            reviewer=_reviewer(),
            github_policy=_github_policy(request),
            github_adapter=Adapter(),
            github_reconciler=Reconciler(
                GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)
            ),
            policy_refs=("AGENTS.md",),
            review_rubric=("durability",),
            implementer_id="implementer",
        )


def test_journal_only_approval_is_not_promoted_to_trusted_authority_after_restart(
    tmp_path,
) -> None:
    request = _request(sensitive=True)
    approval = HumanApproval(
        "approval-1", "human", request.operation_id, request.request_sha256
    )
    state = _pending_state(
        request,
        journal=GitHubEffectJournal().with_approval(approval),
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    adapter = Adapter()
    host = _host(
        store,
        request,
        adapter,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )

    paused = host.step()

    assert paused.core.phase is RunPhase.AWAITING_HUMAN
    assert paused.stop_code is ControllerStopCode.NEEDS_HUMAN
    assert adapter.calls == []
    assert store.load().trusted_approvals == ()


def test_human_approval_is_persisted_before_live_authority_registration(
    tmp_path, monkeypatch
) -> None:
    request = _request(sensitive=True)
    state = _pending_state(
        request,
        phase=RunPhase.AWAITING_HUMAN,
        stop_code=ControllerStopCode.NEEDS_HUMAN,
        stop_reason="approval required",
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    adapter = Adapter()
    host = _host(
        store,
        request,
        adapter,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )
    approval = HumanApproval(
        "approval-1", "human", request.operation_id, request.request_sha256
    )

    events: list[str] = []
    original_save = AtomicHostStateStore.save
    original_register = production_host.HumanApprovalAuthority.register

    def recording_save(self, envelope):
        if envelope.trusted_approvals:
            events.append("persist-trusted-approval")
        return original_save(self, envelope)

    def recording_register(self, value):
        events.append("register-live-authority")
        return original_register(self, value)

    monkeypatch.setattr(AtomicHostStateStore, "save", recording_save)
    monkeypatch.setattr(production_host.HumanApprovalAuthority, "register", recording_register)

    resumed = host.resume(approval=approval)

    assert resumed.core.phase is RunPhase.IMPLEMENT
    assert events[:2] == ["persist-trusted-approval", "register-live-authority"]
    durable = store.load()
    assert durable is not None
    assert durable.trusted_approvals == (approval,)
    assert durable.controller_state.github_journal.approval_for(request.operation_id) == approval


def test_restart_restores_trusted_approval_and_sensitive_effect_executes_once(tmp_path) -> None:
    request = _request(sensitive=True)
    state = _pending_state(
        request,
        phase=RunPhase.AWAITING_HUMAN,
        stop_code=ControllerStopCode.NEEDS_HUMAN,
        stop_reason="approval required",
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    approval = HumanApproval(
        "approval-1", "human", request.operation_id, request.request_sha256
    )

    first_adapter = Adapter()
    first = _host(
        store,
        request,
        first_adapter,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )
    first.resume(approval=approval)
    assert first_adapter.calls == []

    restarted_adapter = Adapter()
    restarted = _host(
        store,
        request,
        restarted_adapter,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )
    completed_effect = restarted.step()

    assert completed_effect.pending_operation_index == 1
    assert restarted_adapter.calls == [request.operation_id]
    assert store.load().trusted_approvals == (approval,)


def test_uncertain_checkpoint_survives_restart_and_is_not_blindly_redispatched(
    tmp_path,
) -> None:
    request = _request()
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, _pending_state(request))
    ambiguous = Adapter(ambiguous=True)
    first = _host(
        store,
        request,
        ambiguous,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )

    blocked = first.step()
    assert blocked.stop_code is ControllerStopCode.BLOCKED_EXECUTION
    assert blocked.github_journal.uncertain_for(request.operation_id) is not None
    assert ambiguous.calls == [request.operation_id]

    should_not_dispatch = Adapter()
    restarted = _host(
        store,
        request,
        should_not_dispatch,
        Reconciler(GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)),
    )
    still_blocked = restarted.step()

    assert still_blocked.stop_code is ControllerStopCode.BLOCKED_EXECUTION
    assert should_not_dispatch.calls == []


def test_executed_reconciliation_resumes_via_receipt_without_adapter_redispatch(
    tmp_path,
) -> None:
    request = _request()
    uncertain = GitHubEffectUncertainRequest(
        request.operation_id,
        request.request_sha256,
        request.repository,
        request.action,
    )
    state = _pending_state(
        request,
        phase=RunPhase.BLOCKED,
        journal=GitHubEffectJournal().with_uncertain_request(uncertain),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
        stop_reason="trusted reconciliation required",
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    adapter = Adapter()
    reconciler = Reconciler(
        GitHubReconciliationResult(
            GitHubReconciliationDisposition.EXECUTED,
            "issue:already-created",
        )
    )
    host = _host(store, request, adapter, reconciler)

    reconciled = host.reconcile_uncertain()
    assert reconciled.core.phase is RunPhase.IMPLEMENT
    assert reconciled.stop_code is None
    assert reconciled.github_journal.receipt_for(request.operation_id) is not None
    assert reconciler.calls == [request.operation_id]
    assert adapter.calls == []

    consumed = host.step()
    assert consumed.pending_operation_index == 1
    assert consumed.github_writes == 1
    assert adapter.calls == []


def test_not_executed_reconciliation_resumes_then_allows_one_normal_dispatch(tmp_path) -> None:
    request = _request()
    uncertain = GitHubEffectUncertainRequest(
        request.operation_id,
        request.request_sha256,
        request.repository,
        request.action,
    )
    state = _pending_state(
        request,
        phase=RunPhase.BLOCKED,
        journal=GitHubEffectJournal().with_uncertain_request(uncertain),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
        stop_reason="trusted reconciliation required",
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    adapter = Adapter()
    reconciler = Reconciler(
        GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)
    )
    host = _host(store, request, adapter, reconciler)

    reconciled = host.reconcile_uncertain()
    assert reconciled.core.phase is RunPhase.IMPLEMENT
    assert reconciled.github_journal.uncertain_for(request.operation_id) is None
    assert adapter.calls == []

    dispatched = host.step()
    assert dispatched.pending_operation_index == 1
    assert adapter.calls == [request.operation_id]


def test_reconciliation_rejects_persisted_request_identity_drift(tmp_path) -> None:
    request = _request(payload_title="current")
    different = _request(payload_title="different")
    uncertain = GitHubEffectUncertainRequest(
        different.operation_id,
        different.request_sha256,
        different.repository,
        different.action,
    )
    state = _pending_state(
        request,
        phase=RunPhase.BLOCKED,
        journal=GitHubEffectJournal().with_uncertain_request(uncertain),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
        stop_reason="trusted reconciliation required",
    )
    store = AtomicHostStateStore(tmp_path / "host.json")
    _save_state(store, state)
    adapter = Adapter()
    reconciler = Reconciler(
        GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)
    )
    host = _host(store, request, adapter, reconciler)

    with pytest.raises(ValueError, match="identity|digest|conflict"):
        host.reconcile_uncertain()

    assert reconciler.calls == []
    assert adapter.calls == []
    restored = store.load().controller_state
    assert restored.github_journal.uncertain_for(request.operation_id) is not None


def test_host_does_not_expose_privileged_dependencies_as_public_capabilities(tmp_path) -> None:
    request = _request()
    store = AtomicHostStateStore(tmp_path / "host.json")
    adapter = Adapter()
    reconciler = Reconciler(
        GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)
    )
    host = _host(store, request, adapter, reconciler)

    for forbidden in (
        "adapter",
        "github_adapter",
        "reconciler",
        "github_reconciler",
        "approval_authority",
        "github_gateway",
        "gateway",
        "register_approval",
        "store",
    ):
        assert not hasattr(host, forbidden), forbidden
    assert host.state is None
