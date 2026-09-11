from __future__ import annotations

import pytest

from harness.github_side_effects import (
    ApprovalRequired,
    GitHubOperationIntent,
    GitHubOperationKind,
    GitHubTarget,
    GuardedGitHubSideEffects,
    HumanApprovalGrant,
    OperationJournal,
)


UPSTREAM = GitHubTarget("DT-UCPH", "cuc")


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("read")
        return "read"

    def reconcile(self, intent: GitHubOperationIntent) -> str | None:
        self.calls.append("reconcile")
        return None

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


def test_caller_fabricated_matching_grant_is_not_human_authorization() -> None:
    """A model/controller must not mint its own human approval artifact."""
    adapter = RecordingAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter, journal=OperationJournal())
    intent = GitHubOperationIntent(
        "forged-upstream-write",
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        {"title": "should require a real human gate"},
    )

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
