from __future__ import annotations

import pytest

from harness.github_side_effects import (
    ApprovalRequired,
    GitHubOperationIntent,
    GitHubOperationKind,
    GitHubTarget,
    GuardedGitHubSideEffects,
    HumanApprovalGrant,
    HumanApprovalRegistry,
    OperationJournal,
)


UPSTREAM = GitHubTarget("DT-UCPH", "cuc")


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reconciled: dict[str, str] = {}

    def read(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("read")
        return "read"

    def reconcile(self, intent: GitHubOperationIntent) -> str | None:
        self.calls.append("reconcile")
        return self.reconciled.get(intent.operation_id)

    def create_branch(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("create_branch")
        return "created"

    def update_branch(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("update_branch")
        return "updated"

    def create_issue(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("create_issue")
        return "issue"

    def update_issue(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("update_issue")
        return "issue-updated"

    def create_pr(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("create_pr")
        return "pr"

    def update_pr(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("update_pr")
        return "pr-updated"

    def comment(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("comment")
        return "comment"

    def merge_pr(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("merge_pr")
        return "merged"

    def trigger_workflow(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("trigger_workflow")
        return "workflow"


def _upstream_intent(operation_id: str = "upstream-write") -> GitHubOperationIntent:
    return GitHubOperationIntent(
        operation_id,
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        {"title": "requires a human gate"},
    )


def _grant(
    intent: GitHubOperationIntent,
    *,
    approval_id: str = "trusted-human-approval",
    approved_by: str = "human-user",
) -> HumanApprovalGrant:
    return HumanApprovalGrant(
        approval_id=approval_id,
        approved_by=approved_by,
        operation_id=intent.operation_id,
        operation_fingerprint=intent.fingerprint,
        issued_at="2026-09-11T12:00:00+03:00",
    )


def test_caller_fabricated_matching_grant_is_not_human_authorization() -> None:
    """A model/controller must not mint its own human approval artifact."""
    adapter = RecordingAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter, journal=OperationJournal())
    intent = _upstream_intent("forged-upstream-write")

    forged = HumanApprovalGrant(
        approval_id="forged-by-controller",
        approved_by="human-user",
        operation_id=intent.operation_id,
        operation_fingerprint=intent.fingerprint,
        issued_at="2026-09-11T12:00:00+03:00",
    )

    with pytest.raises(ApprovalRequired):
        guard.execute(intent, approval=forged)
    assert adapter.calls == []


def test_registered_exact_grant_authorizes_but_same_id_forgery_does_not() -> None:
    intent = _upstream_intent()
    grant = _grant(intent)
    registry = HumanApprovalRegistry()
    registry.register(grant)
    adapter = RecordingAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter, approvals=registry)

    forged = _grant(intent, approved_by="controller-pretending-to-be-human")
    with pytest.raises(ApprovalRequired):
        guard.execute(intent, approval=forged)
    assert adapter.calls == []

    result = guard.execute(intent, approval=grant.approval_id)
    assert result.status == "executed"
    assert adapter.calls == ["create_issue"]


def test_approval_registry_round_trip_preserves_exact_trust_set() -> None:
    intent = _upstream_intent("registry-roundtrip")
    grant = _grant(intent)
    registry = HumanApprovalRegistry()
    registry.register(grant)

    restored = HumanApprovalRegistry.from_dict(registry.to_dict())
    assert restored.resolve(grant.approval_id) == grant
    assert restored.resolve(grant) == grant

    forged = _grant(intent, approved_by="not-the-human")
    assert restored.resolve(forged) is None


def test_prepared_sensitive_operation_can_resume_only_with_restored_trusted_approval() -> None:
    intent = _upstream_intent("approved-restart")
    grant = _grant(intent)
    registry = HumanApprovalRegistry()
    registry.register(grant)
    journal = OperationJournal()
    journal.prepare(intent, approval_id=grant.approval_id)

    adapter = RecordingAdapter()
    adapter.reconciled[intent.operation_id] = "provider-existing-issue"

    without_registry = GuardedGitHubSideEffects(
        adapter=adapter,
        journal=OperationJournal.from_dict(journal.to_dict()),
    )
    with pytest.raises(ApprovalRequired):
        without_registry.execute(intent)
    assert adapter.calls == []

    restarted = GuardedGitHubSideEffects(
        adapter=adapter,
        journal=OperationJournal.from_dict(journal.to_dict()),
        approvals=HumanApprovalRegistry.from_dict(registry.to_dict()),
    )
    result = restarted.execute(intent)
    assert result.status == "replayed"
    assert result.result_ref == "provider-existing-issue"
    assert adapter.calls == ["reconcile"]


def test_empty_array_and_empty_object_are_distinct_authorization_payloads() -> None:
    array_intent = GitHubOperationIntent(
        "container-type",
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        {"value": []},
    )
    object_intent = GitHubOperationIntent(
        "container-type",
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        {"value": {}},
    )

    assert array_intent.fingerprint != object_intent.fingerprint
    assert GitHubOperationIntent.from_dict(array_intent.to_dict()) == array_intent
    assert GitHubOperationIntent.from_dict(object_intent.to_dict()) == object_intent
    assert array_intent.to_dict()["payload"] == {"value": []}
    assert object_intent.to_dict()["payload"] == {"value": {}}


def test_nested_empty_container_types_survive_round_trip() -> None:
    intent = GitHubOperationIntent(
        "nested-container-types",
        GitHubOperationKind.CREATE_PR,
        UPSTREAM,
        {"outer": [{"array": [], "object": {}}, [], {}]},
    )

    restored = GitHubOperationIntent.from_dict(intent.to_dict())
    assert restored == intent
    assert restored.fingerprint == intent.fingerprint
    assert restored.to_dict()["payload"] == {
        "outer": [{"array": [], "object": {}}, [], {}]
    }
