from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import harness.github_effects as ge


REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[ge.GitHubEffectRequest] = []

    def execute(self, request: ge.GitHubEffectRequest) -> str:
        self.calls.append(request)
        return f"result:{request.operation_id}"


class FakeReconciler:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[ge.GitHubEffectRequest] = []

    def reconcile(self, request: ge.GitHubEffectRequest):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def _require_ported_api() -> None:
    missing = [
        name
        for name in (
            "HumanApprovalAuthority",
            "GitHubReconciliationDisposition",
            "GitHubReconciliationResult",
        )
        if not hasattr(ge, name)
    ]
    if missing:
        pytest.fail("canonical github_effects API has not ported: " + ", ".join(missing))
    if "target_ref" not in inspect.signature(ge.GitHubEffectRequest).parameters:
        pytest.fail("GitHubEffectRequest lacks first-class target_ref")
    if "approval_authority" not in inspect.signature(ge.GitHubEffectGateway).parameters:
        pytest.fail("GitHubEffectGateway lacks host-owned approval_authority")
    if "reconciler" not in inspect.signature(ge.GitHubEffectGateway).parameters:
        pytest.fail("GitHubEffectGateway lacks trusted reconciler")
    if not hasattr(ge.GitHubEffectGateway, "reconcile_uncertain"):
        pytest.fail("GitHubEffectGateway lacks reconcile_uncertain")


def _policy(*permissions, upstream=()):
    return ge.GitHubTaskPolicy(
        allowed_fork_write_operations=tuple(
            ge.GitHubOperationPermission(operation_id, action)
            for operation_id, action in permissions
        ),
        allowed_upstream_operation_ids=tuple(upstream),
    )


def _request(operation_id, repository, action, payload=None, *, target_ref=None):
    return ge.GitHubEffectRequest(
        operation_id,
        repository,
        action,
        payload or {"value": operation_id},
        target_ref=target_ref,
    )


def _gateway(policy, adapter=None, authority=None, reconciler=None):
    _require_ported_api()
    return ge.GitHubEffectGateway(
        policy,
        adapter or FakeAdapter(),
        approval_authority=authority or ge.HumanApprovalAuthority(),
        reconciler=reconciler,
    )


def _uncertain_journal(request):
    return ge.GitHubEffectJournal().with_uncertain_request(
        ge.GitHubEffectUncertainRequest(
            request.operation_id,
            request.request_sha256,
            request.repository,
            request.action,
        )
    )


def test_repository_has_one_write_authority_module() -> None:
    canonical = REPO_ROOT / "agent/harness/github_effects.py"
    duplicate = REPO_ROOT / "agent/harness/github_side_effects.py"
    assert canonical.is_file()
    assert not duplicate.exists(), (
        "two independently usable HARN-009 write boundaries remain; "
        "retire github_side_effects.py after porting its stronger invariants"
    )


def test_unregistered_approval_cannot_authorize_upstream_write() -> None:
    _require_ported_api()
    adapter = FakeAdapter()
    authority = ge.HumanApprovalAuthority()
    request = _request(
        "upstream-op",
        "DT-UCPH/cuc",
        ge.GitHubAction.UPDATE_ISSUE,
        {"issue": 1, "body": "x"},
    )
    fabricated = ge.HumanApproval(
        "approval-fabricated",
        "model-shaped-value",
        request.operation_id,
        request.request_sha256,
    )
    journal = ge.GitHubEffectJournal().with_approval(fabricated)
    gateway = _gateway(
        _policy(upstream=(request.operation_id,)),
        adapter,
        authority,
    )

    with pytest.raises(ge.HumanApprovalRequired):
        gateway.execute_write(request, journal, checkpoint=lambda _: None)
    assert adapter.calls == []


def test_registered_exact_approval_authorizes_once() -> None:
    _require_ported_api()
    adapter = FakeAdapter()
    authority = ge.HumanApprovalAuthority()
    request = _request(
        "upstream-op-registered",
        "DT-UCPH/cuc",
        ge.GitHubAction.UPDATE_ISSUE,
        {"issue": 2, "body": "x"},
    )
    approval = ge.HumanApproval(
        "approval-human",
        "trusted-human",
        request.operation_id,
        request.request_sha256,
    )
    authority.register(approval)
    journal = ge.GitHubEffectJournal().with_approval(approval)
    persisted = []
    gateway = _gateway(
        _policy(upstream=(request.operation_id,)),
        adapter,
        authority,
    )

    journal, receipt = gateway.execute_write(
        request,
        journal,
        checkpoint=persisted.append,
    )
    journal2, receipt2 = gateway.execute_write(
        request,
        journal,
        checkpoint=persisted.append,
    )
    assert receipt2 == receipt
    assert journal2 == journal
    assert len(adapter.calls) == 1


def test_target_ref_is_part_of_request_identity_and_round_trip() -> None:
    _require_ported_api()
    first = _request(
        "ref-op",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_REF,
        {"sha": "abc"},
        target_ref="feature-a",
    )
    second = _request(
        "ref-op",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_REF,
        {"sha": "abc"},
        target_ref="feature-b",
    )
    assert first.request_sha256 != second.request_sha256
    restored = ge.GitHubEffectRequest.from_dict(first.to_dict())
    assert restored == first
    assert restored.target_ref == "feature-a"


@pytest.mark.parametrize(
    "action",
    (
        ge.GitHubAction.UPDATE_REF,
        ge.GitHubAction.MERGE_PULL_REQUEST,
        ge.GitHubAction.DISPATCH_WORKFLOW,
    ),
)
def test_ref_sensitive_actions_require_first_class_ref_before_dispatch(action) -> None:
    _require_ported_api()
    adapter = FakeAdapter()
    operation_id = f"missing-ref-{action.value}"
    request = _request(operation_id, "alexsosn/cuc", action)
    gateway = _gateway(_policy((operation_id, action)), adapter)

    with pytest.raises(PermissionError, match="ref"):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )
    assert adapter.calls == []


@pytest.mark.parametrize("ref", ("main", "MAIN", "agent-harness-safety", "Agent-Harness-Safety"))
def test_generic_fork_ref_update_denies_protected_integration_refs(ref) -> None:
    _require_ported_api()
    adapter = FakeAdapter()
    request = _request(
        "protected-update",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_REF,
        {"sha": "abc"},
        target_ref=ref,
    )
    gateway = _gateway(
        _policy((request.operation_id, ge.GitHubAction.UPDATE_REF)),
        adapter,
    )

    with pytest.raises(PermissionError, match="protected|integration|ref"):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )
    assert adapter.calls == []


def test_uncertain_executed_reconciliation_records_receipt_without_redispatch() -> None:
    _require_ported_api()
    request = _request(
        "reconcile-executed",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_ISSUE,
        {"issue": 3},
    )
    adapter = FakeAdapter()
    result = ge.GitHubReconciliationResult(
        ge.GitHubReconciliationDisposition.EXECUTED,
        "issue:3",
    )
    reconciler = FakeReconciler(result)
    gateway = _gateway(
        _policy((request.operation_id, request.action)),
        adapter,
        reconciler=reconciler,
    )
    persisted = []
    journal, receipt = gateway.reconcile_uncertain(
        request,
        _uncertain_journal(request),
        checkpoint=persisted.append,
    )
    replayed_journal, replayed_receipt = gateway.execute_write(
        request,
        journal,
        checkpoint=persisted.append,
    )

    assert receipt is not None
    assert receipt.result_ref == "issue:3"
    assert replayed_receipt == receipt
    assert replayed_journal == journal
    assert len(reconciler.calls) == 1
    assert adapter.calls == []


def test_uncertain_not_executed_reconciliation_allows_one_later_dispatch() -> None:
    _require_ported_api()
    request = _request(
        "reconcile-not-executed",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_ISSUE,
        {"issue": 4},
    )
    adapter = FakeAdapter()
    reconciler = FakeReconciler(
        ge.GitHubReconciliationResult(
            ge.GitHubReconciliationDisposition.NOT_EXECUTED,
            None,
        )
    )
    gateway = _gateway(
        _policy((request.operation_id, request.action)),
        adapter,
        reconciler=reconciler,
    )
    persisted = []
    safe_journal, receipt = gateway.reconcile_uncertain(
        request,
        _uncertain_journal(request),
        checkpoint=persisted.append,
    )
    assert receipt is None
    assert safe_journal.uncertain_for(request.operation_id) is None

    completed, completed_receipt = gateway.execute_write(
        request,
        safe_journal,
        checkpoint=persisted.append,
    )
    assert completed.receipt_for(request.operation_id) == completed_receipt
    assert len(reconciler.calls) == 1
    assert len(adapter.calls) == 1


def test_reconciliation_failure_preserves_quarantine_and_blocks_execute_retry() -> None:
    _require_ported_api()
    request = _request(
        "reconcile-failure",
        "alexsosn/cuc",
        ge.GitHubAction.UPDATE_ISSUE,
        {"issue": 5},
    )
    adapter = FakeAdapter()
    reconciler = FakeReconciler(error=RuntimeError("reconciliation unavailable"))
    gateway = _gateway(
        _policy((request.operation_id, request.action)),
        adapter,
        reconciler=reconciler,
    )
    journal = _uncertain_journal(request)

    with pytest.raises(RuntimeError, match="reconciliation unavailable"):
        gateway.reconcile_uncertain(request, journal, checkpoint=lambda _: None)
    assert journal.uncertain_for(request.operation_id) is not None
    with pytest.raises(ge.GitHubEffectOutcomeUnknown):
        gateway.execute_write(request, journal, checkpoint=lambda _: None)
    assert adapter.calls == []


def test_task_local_operation_action_binding_survives_reconciliation_ports() -> None:
    _require_ported_api()
    adapter = FakeAdapter()
    operation_id = "exact-action"
    policy = _policy((operation_id, ge.GitHubAction.UPDATE_ISSUE))
    request = _request(
        operation_id,
        "alexsosn/cuc",
        ge.GitHubAction.COMMENT,
        {"issue": 6, "body": "no"},
    )
    gateway = _gateway(policy, adapter)

    with pytest.raises(PermissionError, match="operation|action|allowed"):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )
    assert adapter.calls == []
