from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    GitHubEffectReceipt,
    GitHubEffectRequest,
    GitHubEffectUncertainRequest,
    GitHubOperationPermission,
    GitHubReconciliationDisposition,
    GitHubReconciliationResult,
    GitHubTaskPolicy,
    HumanApproval,
)


FORK = "alexsosn/cuc"
BASE = "a" * 40
HEAD = "b" * 40


def _runtime():
    import harness.development_host as runtime

    return runtime


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[GitHubEffectRequest] = []

    def execute(self, request: GitHubEffectRequest) -> str:
        self.calls.append(request)
        return f"provider:{request.operation_id}"


class RecordingReconciler:
    def __init__(
        self,
        disposition: GitHubReconciliationDisposition = GitHubReconciliationDisposition.EXECUTED,
    ) -> None:
        self.disposition = disposition
        self.calls: list[GitHubEffectRequest] = []

    def reconcile(self, request: GitHubEffectRequest) -> GitHubReconciliationResult:
        self.calls.append(request)
        if self.disposition is GitHubReconciliationDisposition.EXECUTED:
            return GitHubReconciliationResult(self.disposition, f"reconciled:{request.operation_id}")
        return GitHubReconciliationResult(self.disposition)


class RecordingStore:
    def __init__(self, path: Path) -> None:
        runtime = _runtime()
        self._inner = runtime.AtomicJsonDevelopmentHostStore(path)
        self.events: list[str] = []
        self.fail_save = False

    def load(self):
        return self._inner.load()

    def save(self, envelope) -> None:
        self.events.append("save")
        if self.fail_save:
            raise OSError("simulated durable persistence failure")
        self._inner.save(envelope)


class DurableStoreSubclass:
    """Factory wrapper replaced with a real subclass at runtime to satisfy type checks."""


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-53",
        "durable host",
        "wire restart-safe trusted controller hosting",
        ("durable persistence", "no duplicate GitHub write"),
    )


def _core(phase: RunPhase, *, pause_reason: str | None = None) -> RunState:
    research = ResearchArtifact("research-53", "durable host research", ("issue:53",))
    plan = PlanArtifact("plan-53", "durable host plan", ("test", "implement"), ("issue:53",))
    test = TestIntent(
        "targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_development_host_runtime.py"),
        "agent",
        "trusted host behavior",
    )
    if phase in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN}:
        return RunState(
            "host-run",
            _task(),
            phase=phase,
            research=research,
            plan=plan,
            test_intents=(test,),
            resume_phase=RunPhase.IMPLEMENT,
            pause_reason=pause_reason or "trusted recovery required",
        )
    assert phase is RunPhase.IMPLEMENT
    return RunState(
        "host-run",
        _task(),
        phase=RunPhase.IMPLEMENT,
        research=research,
        plan=plan,
        test_intents=(test,),
    )


def _request(
    *,
    action: GitHubAction = GitHubAction.UPDATE_CONTENTS,
    operation_id: str = "operation-1",
    payload_marker: str = "v1",
) -> GitHubEffectRequest:
    target_ref = "feature-branch" if action in {
        GitHubAction.CREATE_BRANCH,
        GitHubAction.UPDATE_REF,
        GitHubAction.MERGE_PULL_REQUEST,
        GitHubAction.DISPATCH_WORKFLOW,
    } else None
    return GitHubEffectRequest(
        operation_id,
        FORK,
        action,
        {"marker": payload_marker},
        target_ref=target_ref,
    )


def _approval(request: GitHubEffectRequest) -> HumanApproval:
    return HumanApproval(
        "approval-1",
        "human-reviewer",
        request.operation_id,
        request.request_sha256,
    )


def _pending_state(
    request: GitHubEffectRequest,
    *,
    phase: RunPhase = RunPhase.IMPLEMENT,
    journal: GitHubEffectJournal | None = None,
    stop_code: ControllerStopCode | None = None,
) -> DevelopmentControllerState:
    change = ChangeSet(
        "change-1",
        "pending durable host change",
        ("agent/harness/development_host.py",),
        (request.operation_id,),
    )
    pending = ImplementationResult(change, HEAD, (request,), 0.0, ("fixture:pending",))
    reason = None if phase is RunPhase.IMPLEMENT else "trusted recovery required"
    return DevelopmentControllerState(
        schema_version=1,
        base_sha=BASE,
        core=_core(phase, pause_reason=reason),
        pending_implementation=pending,
        pending_operation_index=0,
        current_head_sha=HEAD,
        github_journal=journal or GitHubEffectJournal(),
        stop_code=stop_code,
        stop_reason=reason if stop_code is not None else None,
        audit_events=("fixture",),
    )


def _ports() -> DevelopmentControllerPorts:
    def unused(*_args, **_kwargs):
        raise RuntimeError("unused test port")

    return DevelopmentControllerPorts(
        research=unused,
        plan=unused,
        declare_tests=unused,
        run_red=unused,
        implement=unused,
        run_test=unused,
        run_evals=unused,
        final_diff=unused,
    )


def _controller_policy() -> DevelopmentControllerPolicy:
    return DevelopmentControllerPolicy(
        max_revision_attempts=2,
        max_verification_executions=4,
        max_review_attempts=2,
        max_github_writes=4,
        require_red=False,
        production_mode=True,
    )


def _github_policy(request: GitHubEffectRequest) -> GitHubTaskPolicy:
    return GitHubTaskPolicy(
        allowed_fork_write_operations=(
            GitHubOperationPermission(request.operation_id, request.action),
        )
    )


def _reviewer() -> IndependentReviewer:
    def unused_review(_context):
        raise RuntimeError("unused review port")

    return IndependentReviewer("independent-reviewer", unused_review, "implementer")


def _host(
    tmp_path: Path,
    request: GitHubEffectRequest,
    *,
    state: DevelopmentControllerState | None = None,
    trusted_approvals: tuple[HumanApproval, ...] = (),
    adapter: RecordingAdapter | None = None,
    reconciler: RecordingReconciler | None = None,
    store=None,
):
    runtime = _runtime()
    durable_store = store or runtime.AtomicJsonDevelopmentHostStore(tmp_path / "host.json")
    if state is not None or trusted_approvals:
        durable_store.save(
            runtime.DevelopmentHostEnvelope(1, state, trusted_approvals)
        )
    return runtime.TrustedDevelopmentHost(
        durable_store,
        controller_policy=_controller_policy(),
        controller_ports=_ports(),
        reviewer=_reviewer(),
        github_policy=_github_policy(request),
        github_adapter=adapter or RecordingAdapter(),
        github_reconciler=reconciler or RecordingReconciler(),
        policy_refs=("policy:fork-only",),
        review_rubric=("independent review",),
        implementer_id="implementer",
    )


def test_restart_restores_trusted_approval_authority_for_sensitive_effect(tmp_path: Path) -> None:
    request = _request(action=GitHubAction.MERGE_PULL_REQUEST)
    approval = _approval(request)
    state = _pending_state(
        request,
        journal=GitHubEffectJournal(approvals=(approval,)),
    )
    adapter = RecordingAdapter()
    host = _host(
        tmp_path,
        request,
        state=state,
        trusted_approvals=(approval,),
        adapter=adapter,
    )

    updated = host.step()

    assert [item.operation_id for item in adapter.calls] == [request.operation_id]
    assert updated.pending_operation_index == 1
    assert host.state == updated
    assert host.envelope.trusted_approvals == (approval,)


def test_journal_approval_without_persisted_trusted_authority_cannot_authorize(tmp_path: Path) -> None:
    request = _request(action=GitHubAction.MERGE_PULL_REQUEST)
    approval = _approval(request)
    state = _pending_state(
        request,
        journal=GitHubEffectJournal(approvals=(approval,)),
    )
    adapter = RecordingAdapter()
    host = _host(tmp_path, request, state=state, adapter=adapter)

    updated = host.step()

    assert not adapter.calls
    assert updated.stop_code is ControllerStopCode.NEEDS_HUMAN
    assert updated.core.phase is RunPhase.AWAITING_HUMAN


def test_resume_with_approval_persists_before_live_authority_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(action=GitHubAction.MERGE_PULL_REQUEST)
    approval = _approval(request)
    state = _pending_state(
        request,
        phase=RunPhase.AWAITING_HUMAN,
        stop_code=ControllerStopCode.NEEDS_HUMAN,
    )

    class OrderedStore(runtime.AtomicJsonDevelopmentHostStore):
        def __init__(self, path):
            super().__init__(path)
            self.events: list[str] = []

        def save(self, envelope):
            self.events.append("save")
            return super().save(envelope)

    store = OrderedStore(tmp_path / "host.json")
    store.save(runtime.DevelopmentHostEnvelope(1, state, ()))
    adapter = RecordingAdapter()
    host = _host(tmp_path, request, adapter=adapter, store=store)
    store.events.clear()

    original_register = runtime.HumanApprovalAuthority.register

    def recording_register(authority, value):
        store.events.append("register")
        return original_register(authority, value)

    monkeypatch.setattr(runtime.HumanApprovalAuthority, "register", recording_register)
    resumed = host.resume_with_approval(approval)

    assert store.events[:2] == ["save", "register"]
    assert store.events[-1] == "save"
    assert resumed.core.phase is RunPhase.IMPLEMENT
    assert host.envelope.trusted_approvals == (approval,)
    host.step()
    assert [item.operation_id for item in adapter.calls] == [request.operation_id]


def test_failed_approval_persistence_never_reaches_live_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(action=GitHubAction.MERGE_PULL_REQUEST)
    approval = _approval(request)
    state = _pending_state(
        request,
        phase=RunPhase.AWAITING_HUMAN,
        stop_code=ControllerStopCode.NEEDS_HUMAN,
    )

    class FailingStore(runtime.AtomicJsonDevelopmentHostStore):
        fail = False

        def save(self, envelope):
            if self.fail:
                raise OSError("simulated durable persistence failure")
            return super().save(envelope)

    store = FailingStore(tmp_path / "host.json")
    store.save(runtime.DevelopmentHostEnvelope(1, state, ()))
    host = _host(tmp_path, request, store=store)
    registrations: list[HumanApproval] = []
    original_register = runtime.HumanApprovalAuthority.register

    def recording_register(authority, value):
        registrations.append(value)
        return original_register(authority, value)

    monkeypatch.setattr(runtime.HumanApprovalAuthority, "register", recording_register)
    store.fail = True
    with pytest.raises(OSError, match="simulated durable persistence failure"):
        host.resume_with_approval(approval)

    assert not registrations
    assert host.envelope.trusted_approvals == ()
    assert store.load().trusted_approvals == ()


def test_controller_persistence_callback_revalidates_serialized_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request()
    host = _host(tmp_path, request)

    def reject(_cls, _payload):
        raise ValueError("canonical controller revalidation sentinel")

    monkeypatch.setattr(
        runtime.DevelopmentControllerState,
        "from_dict",
        classmethod(reject),
    )
    with pytest.raises(ValueError, match="revalidation sentinel"):
        host.start(run_id="new-run", task=_task(), base_sha=BASE, provenance_refs=("issue:53",))

    assert host.state is None


@pytest.mark.parametrize(
    "disposition",
    [GitHubReconciliationDisposition.EXECUTED, GitHubReconciliationDisposition.NOT_EXECUTED],
)
def test_restart_reconciles_uncertain_effect_without_write_adapter(
    tmp_path: Path,
    disposition: GitHubReconciliationDisposition,
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
        journal=GitHubEffectJournal(uncertain_requests=(uncertain,)),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
    )
    adapter = RecordingAdapter()
    reconciler = RecordingReconciler(disposition)
    host = _host(
        tmp_path,
        request,
        state=state,
        adapter=adapter,
        reconciler=reconciler,
    )

    reconciled = host.reconcile_uncertain()

    assert not adapter.calls
    assert [item.operation_id for item in reconciler.calls] == [request.operation_id]
    assert reconciled.github_journal.uncertain_for(request.operation_id) is None
    assert reconciled.core.phase is RunPhase.IMPLEMENT
    assert reconciled.stop_code is None
    if disposition is GitHubReconciliationDisposition.EXECUTED:
        assert reconciled.github_journal.receipt_for(request.operation_id) is not None
        replayed = host.step()
        assert not adapter.calls
        assert replayed.pending_operation_index == 1
    else:
        assert reconciled.github_journal.receipt_for(request.operation_id) is None


def test_restart_replays_durable_receipt_without_duplicate_provider_write(tmp_path: Path) -> None:
    request = _request()
    receipt = GitHubEffectReceipt(
        request.operation_id,
        request.request_sha256,
        request.repository,
        request.action,
        "provider:already-executed",
    )
    state = _pending_state(
        request,
        journal=GitHubEffectJournal(receipts=(receipt,)),
    )
    adapter = RecordingAdapter()
    host = _host(tmp_path, request, state=state, adapter=adapter)

    updated = host.step()

    assert not adapter.calls
    assert updated.pending_operation_index == 1
    assert updated.github_writes == 1
    assert updated.github_journal.receipt_for(request.operation_id) == receipt


def test_restart_request_identity_mismatch_fails_closed_before_reconciliation(tmp_path: Path) -> None:
    original = _request(payload_marker="original")
    changed = _request(payload_marker="changed")
    uncertain = GitHubEffectUncertainRequest(
        original.operation_id,
        original.request_sha256,
        original.repository,
        original.action,
    )
    state = _pending_state(
        changed,
        phase=RunPhase.BLOCKED,
        journal=GitHubEffectJournal(uncertain_requests=(uncertain,)),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
    )
    adapter = RecordingAdapter()
    reconciler = RecordingReconciler()
    host = _host(
        tmp_path,
        changed,
        state=state,
        adapter=adapter,
        reconciler=reconciler,
    )

    with pytest.raises(ValueError, match="digest|identity|request"):
        host.reconcile_uncertain()

    assert not adapter.calls
    assert not reconciler.calls
    assert host.state.github_journal.uncertain_for(original.operation_id) is not None


def test_host_public_surface_does_not_expose_privileged_dependencies(tmp_path: Path) -> None:
    request = _request()
    host = _host(tmp_path, request)

    for name in (
        "adapter",
        "github_adapter",
        "reconciler",
        "github_reconciler",
        "approval_authority",
        "github_gateway",
        "store",
        "controller",
        "register_approval",
    ):
        assert not hasattr(host, name), name

    assert host.state is None
    assert host.envelope.controller_state is None


def test_trusted_host_requires_explicit_durable_store(tmp_path: Path) -> None:
    runtime = _runtime()
    request = _request()
    with pytest.raises((TypeError, ValueError), match="store|durable"):
        runtime.TrustedDevelopmentHost(
            None,
            controller_policy=_controller_policy(),
            controller_ports=_ports(),
            reviewer=_reviewer(),
            github_policy=_github_policy(request),
            github_adapter=RecordingAdapter(),
            github_reconciler=RecordingReconciler(),
            policy_refs=("policy:fork-only",),
            review_rubric=("independent review",),
            implementer_id="implementer",
        )
