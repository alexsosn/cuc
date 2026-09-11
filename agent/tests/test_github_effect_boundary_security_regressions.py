from __future__ import annotations

import pytest

import harness.github_effects as ge


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[ge.GitHubEffectRequest] = []

    def execute(self, request: ge.GitHubEffectRequest) -> str:
        self.calls.append(request)
        return f"result:{request.operation_id}"


def _policy(operation_id: str, action: ge.GitHubAction) -> ge.GitHubTaskPolicy:
    return ge.GitHubTaskPolicy(
        allowed_fork_write_operations=(
            ge.GitHubOperationPermission(operation_id, action),
        ),
    )


def _request(
    operation_id: str,
    action: ge.GitHubAction,
    *,
    target_ref: str | None,
) -> ge.GitHubEffectRequest:
    return ge.GitHubEffectRequest(
        operation_id,
        "alexsosn/cuc",
        action,
        {"value": operation_id},
        target_ref=target_ref,
    )


def test_create_branch_requires_explicit_target_ref() -> None:
    operation_id = "create-feature"
    action = ge.GitHubAction.CREATE_BRANCH
    adapter = FakeAdapter()
    gateway = ge.GitHubEffectGateway(_policy(operation_id, action), adapter)
    request = _request(operation_id, action, target_ref=None)

    with pytest.raises(PermissionError, match="ref"):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )
    assert adapter.calls == []


@pytest.mark.parametrize(
    "protected_ref",
    ("main", "MAIN", "agent-harness-safety", "Agent-Harness-Safety"),
)
def test_create_branch_cannot_target_protected_fork_integration_ref(
    protected_ref: str,
) -> None:
    operation_id = "create-protected"
    action = ge.GitHubAction.CREATE_BRANCH
    adapter = FakeAdapter()
    gateway = ge.GitHubEffectGateway(_policy(operation_id, action), adapter)
    request = _request(operation_id, action, target_ref=protected_ref)

    with pytest.raises(PermissionError, match="protected|integration|ref"):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )
    assert adapter.calls == []


@pytest.mark.parametrize(
    "action",
    (ge.GitHubAction.MERGE_PULL_REQUEST, ge.GitHubAction.DISPATCH_WORKFLOW),
)
def test_fork_integration_actions_require_registered_human_approval(
    action: ge.GitHubAction,
) -> None:
    operation_id = f"sensitive-{action.value}"
    request = _request(operation_id, action, target_ref="feature-branch")
    adapter = FakeAdapter()
    authority = ge.HumanApprovalAuthority()
    gateway = ge.GitHubEffectGateway(
        _policy(operation_id, action),
        adapter,
        approval_authority=authority,
    )

    with pytest.raises(ge.HumanApprovalRequired):
        gateway.execute_write(
            request,
            ge.GitHubEffectJournal(),
            checkpoint=lambda _: None,
        )

    fabricated = ge.HumanApproval(
        f"approval-{action.value}",
        "model-shaped-value",
        request.operation_id,
        request.request_sha256,
    )
    fabricated_journal = ge.GitHubEffectJournal().with_approval(fabricated)
    with pytest.raises(ge.HumanApprovalRequired):
        gateway.execute_write(
            request,
            fabricated_journal,
            checkpoint=lambda _: None,
        )

    authority.register(fabricated)
    completed, receipt = gateway.execute_write(
        request,
        fabricated_journal,
        checkpoint=lambda _: None,
    )
    assert completed.receipt_for(operation_id) == receipt
    assert len(adapter.calls) == 1
