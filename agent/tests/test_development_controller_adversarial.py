from __future__ import annotations

import copy
from dataclasses import replace
import importlib

import pytest

from harness.contracts import RunPhase
from harness.github_effects import (
    GitHubAction,
    GitHubEffectGateway,
    GitHubEffectRequest,
    GitHubOperationPermission,
    GitHubReconciliationDisposition,
    GitHubReconciliationResult,
    GitHubTaskPolicy,
    HumanApproval,
    HumanApprovalAuthority,
)

from test_development_controller import (
    ScriptedPorts,
    _controller,
    _policy,
    _start,
)


def _runtime():
    try:
        return importlib.import_module("harness.development_controller")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-010 runtime is not implemented yet: {exc}")


class RecordingAdapter:
    def __init__(self, *, fail_after_write: bool = False) -> None:
        self.calls: list[GitHubEffectRequest] = []
        self.results: dict[str, str] = {}
        self.fail_after_write = fail_after_write

    def execute(self, request: GitHubEffectRequest) -> str:
        self.calls.append(request)
        result = f"provider:{request.operation_id}"
        self.results[request.operation_id] = result
        if self.fail_after_write:
            self.fail_after_write = False
            raise RuntimeError("simulated crash after provider mutation")
        return result


class RecordingReconciler:
    def __init__(self, adapter: RecordingAdapter) -> None:
        self.adapter = adapter
        self.calls: list[str] = []

    def reconcile(self, request: GitHubEffectRequest) -> GitHubReconciliationResult:
        self.calls.append(request.operation_id)
        result = self.adapter.results.get(request.operation_id)
        if result is None:
            return GitHubReconciliationResult(GitHubReconciliationDisposition.NOT_EXECUTED)
        return GitHubReconciliationResult(
            GitHubReconciliationDisposition.EXECUTED,
            result,
        )


def _gateway(
    requests: tuple[GitHubEffectRequest, ...],
    adapter: RecordingAdapter,
    *,
    authority: HumanApprovalAuthority | None = None,
    reconciler: RecordingReconciler | None = None,
) -> GitHubEffectGateway:
    permissions = tuple(
        GitHubOperationPermission(item.operation_id, item.action)
        for item in requests
        if item.repository.casefold() == "alexsosn/cuc"
    )
    upstream = tuple(
        item.operation_id
        for item in requests
        if item.repository.casefold() == "dt-ucph/cuc"
    )
    return GitHubEffectGateway(
        GitHubTaskPolicy(
            allowed_fork_write_operations=permissions,
            allowed_upstream_operation_ids=upstream,
        ),
        adapter,
        approval_authority=authority or HumanApprovalAuthority(),
        reconciler=reconciler,
    )


def _advance_to_pending(controller):
    state = _start(controller)
    for _ in range(20):
        if state.pending_implementation is not None:
            return state
        state = controller.step(state)
    raise AssertionError("controller never reached pending implementation")


class SimulatedControllerCrash(RuntimeError):
    pass


class CrashAfterReceiptStore:
    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        self.last_good: dict[str, object] | None = None
        self.snapshots: list[dict[str, object]] = []
        self.enabled = False

    def __call__(self, payload: dict[str, object]) -> None:
        runtime = _runtime()
        restored = runtime.DevelopmentControllerState.from_dict(payload)
        if (
            self.enabled
            and restored.pending_operation_index == 1
            and restored.github_journal.receipt_for(self.operation_id) is not None
        ):
            raise SimulatedControllerCrash(
                "controller died after durable receipt but before cursor persistence"
            )
        saved = copy.deepcopy(payload)
        self.last_good = saved
        self.snapshots.append(saved)


def test_crash_after_receipt_before_cursor_replays_without_duplicate_or_budget_bypass():
    req1 = GitHubEffectRequest(
        "write-1",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "first"},
    )
    req2 = GitHubEffectRequest(
        "write-2",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "second"},
    )
    ports = ScriptedPorts()
    ports.github_requests = (req1, req2)
    adapter = RecordingAdapter()
    gateway = _gateway((req1, req2), adapter)
    store = CrashAfterReceiptStore(req1.operation_id)
    controller = _controller(
        ports,
        gateway=gateway,
        policy=_policy(max_github_writes=1),
        persist=store,
    )
    state = _advance_to_pending(controller)
    logical_steps_before = state.steps_used
    store.enabled = True

    with pytest.raises(SimulatedControllerCrash):
        controller.step(state)

    assert len(adapter.calls) == 1
    assert adapter.calls[0].operation_id == "write-1"
    assert store.last_good is not None
    durable = _runtime().DevelopmentControllerState.from_dict(store.last_good)
    assert durable.pending_operation_index == 0
    assert durable.github_journal.receipt_for("write-1") is not None

    store.enabled = False
    resumed = _controller(
        ports,
        gateway=gateway,
        policy=_policy(max_github_writes=1),
        persist=store,
    )
    replayed = resumed.step(durable)
    assert replayed.pending_operation_index == 1
    assert replayed.github_writes == 1
    assert replayed.steps_used == logical_steps_before + 1
    assert len(adapter.calls) == 1

    blocked = resumed.step(replayed)
    assert blocked.stop_code is _runtime().ControllerStopCode.GITHUB_WRITE_BUDGET_EXHAUSTED
    assert len(adapter.calls) == 1


def test_uncertain_provider_outcome_is_reconciled_without_blind_redispatch():
    req = GitHubEffectRequest(
        "uncertain-write",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "uncertain"},
    )
    ports = ScriptedPorts()
    ports.github_requests = (req,)
    adapter = RecordingAdapter(fail_after_write=True)
    reconciler = RecordingReconciler(adapter)
    gateway = _gateway((req,), adapter, reconciler=reconciler)
    snapshots: list[dict[str, object]] = []
    controller = _controller(ports, gateway=gateway, persist=snapshots.append)

    final = controller.run_until_stop(_start(controller))

    assert final.core.phase is RunPhase.COMPLETE
    assert len(adapter.calls) == 1
    assert reconciler.calls == ["uncertain-write"]
    assert final.github_journal.uncertain_for("uncertain-write") is None
    assert final.github_journal.receipt_for("uncertain-write") is not None
    assert final.github_writes == 1


def test_sensitive_fork_action_pauses_until_trusted_host_registers_exact_approval():
    req = GitHubEffectRequest(
        "merge-feature",
        "alexsosn/cuc",
        GitHubAction.MERGE_PULL_REQUEST,
        {"pull": 999},
        target_ref="feature/harn-010",
    )
    ports = ScriptedPorts()
    ports.github_requests = (req,)
    adapter = RecordingAdapter()
    authority = HumanApprovalAuthority()
    gateway = _gateway((req,), adapter, authority=authority)
    controller = _controller(ports, gateway=gateway)
    state = _advance_to_pending(controller)

    paused = controller.step(state)
    assert paused.core.phase is RunPhase.AWAITING_HUMAN
    assert paused.stop_code is _runtime().ControllerStopCode.NEEDS_HUMAN
    assert adapter.calls == []

    approval = HumanApproval(
        "approval-merge",
        "human-user",
        req.operation_id,
        req.request_sha256,
    )
    journal_only = paused.github_journal.with_approval(approval)
    with pytest.raises(Exception):
        controller.resume_after_human(paused, journal_only)

    authority.register(approval)
    resumed = controller.resume_after_human(paused, journal_only)
    assert resumed.core.phase is RunPhase.IMPLEMENT
    assert resumed.stop_code is None
    final = controller.run_until_stop(resumed)
    assert final.core.phase is RunPhase.COMPLETE
    assert len(adapter.calls) == 1


def test_gateway_policy_denial_becomes_explicit_policy_block_without_adapter_call():
    req = GitHubEffectRequest(
        "not-authorized",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "denied"},
    )
    ports = ScriptedPorts()
    ports.github_requests = (req,)
    adapter = RecordingAdapter()
    gateway = GitHubEffectGateway(GitHubTaskPolicy(), adapter)
    controller = _controller(ports, gateway=gateway)

    final = controller.run_until_stop(_start(controller))

    assert final.stop_code is _runtime().ControllerStopCode.POLICY_BLOCK
    assert final.core.phase is RunPhase.BLOCKED
    assert adapter.calls == []


def test_controller_checkpoint_rejects_unknown_fields_and_inconsistent_pending_index():
    ports = ScriptedPorts()
    controller = _controller(ports)
    state = _start(controller)
    runtime = _runtime()
    payload = state.to_dict()
    payload["unexpected"] = "smuggled"
    with pytest.raises(ValueError, match="unknown|fields|unexpected"):
        runtime.DevelopmentControllerState.from_dict(payload)

    state = _advance_to_pending(controller)
    payload2 = state.to_dict()
    payload2["pending_operation_index"] = 999
    with pytest.raises(ValueError, match="pending|index|operation"):
        runtime.DevelopmentControllerState.from_dict(payload2)


def test_changed_request_identity_under_same_operation_id_cannot_replay_receipt():
    req = GitHubEffectRequest(
        "stable-id",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "one"},
    )
    ports = ScriptedPorts()
    ports.github_requests = (req,)
    adapter = RecordingAdapter()
    gateway = _gateway((req,), adapter)
    controller = _controller(ports, gateway=gateway)
    state = _advance_to_pending(controller)
    state = controller.step(state)
    assert state.github_journal.receipt_for("stable-id") is not None
    assert state.pending_implementation is not None

    changed = GitHubEffectRequest(
        "stable-id",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "changed"},
    )
    tampered_impl = replace(state.pending_implementation, github_requests=(changed,))
    tampered = replace(state, pending_operation_index=0, pending_implementation=tampered_impl)
    with pytest.raises(ValueError, match="digest|identity|request"):
        controller.step(tampered)
    assert len(adapter.calls) == 1


def test_pending_receipt_replay_does_not_consume_a_second_revision_or_review_budget():
    req = GitHubEffectRequest(
        "one-write",
        "alexsosn/cuc",
        GitHubAction.CREATE_ISSUE,
        {"title": "one"},
    )
    ports = ScriptedPorts()
    ports.github_requests = (req,)
    adapter = RecordingAdapter()
    gateway = _gateway((req,), adapter)
    store = CrashAfterReceiptStore(req.operation_id)
    controller = _controller(ports, gateway=gateway, persist=store)
    state = _advance_to_pending(controller)
    revisions = state.revision_attempts
    reviews = state.review_attempts
    store.enabled = True
    with pytest.raises(SimulatedControllerCrash):
        controller.step(state)
    assert store.last_good is not None
    durable = _runtime().DevelopmentControllerState.from_dict(store.last_good)
    store.enabled = False
    replayed = controller.step(durable)
    assert replayed.revision_attempts == revisions
    assert replayed.review_attempts == reviews
    assert len(adapter.calls) == 1
