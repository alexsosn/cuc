from __future__ import annotations

import copy

import pytest

from harness.github_side_effects import (
    GitHubOperationIntent,
    GitHubOperationKind,
    GitHubTarget,
    GuardedGitHubSideEffects,
    OperationJournal,
    PolicyDisposition,
)


FORK = GitHubTarget("alexsosn", "cuc")


class CrashAfterWriteAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.provider_results: dict[str, str] = {}
        self.crash_after_write = True

    def read(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("read")
        return "read"

    def reconcile(self, intent: GitHubOperationIntent) -> str | None:
        self.calls.append("reconcile")
        return self.provider_results.get(intent.operation_id)

    def create_issue(self, intent: GitHubOperationIntent) -> str:
        self.calls.append("create_issue")
        result = f"issue:{intent.operation_id}"
        self.provider_results[intent.operation_id] = result
        if self.crash_after_write:
            raise RuntimeError("simulated process death after provider write")
        return result

    def create_branch(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def update_branch(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def update_issue(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def create_pr(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def update_pr(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def comment(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def merge_pr(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")

    def trigger_workflow(self, intent: GitHubOperationIntent) -> str:
        raise AssertionError("unexpected write")


def test_prepared_state_is_durably_persisted_before_provider_write() -> None:
    persisted: dict[str, object] = {}

    def persist(snapshot: dict[str, object]) -> None:
        persisted.clear()
        persisted.update(copy.deepcopy(snapshot))

    journal = OperationJournal(persist=persist)
    adapter = CrashAfterWriteAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter, journal=journal)
    intent = GitHubOperationIntent(
        "durable-crash",
        GitHubOperationKind.CREATE_ISSUE,
        FORK,
        {"title": "durable"},
    )

    with pytest.raises(RuntimeError, match="process death"):
        guard.execute(intent)

    # Simulate actual process death: discard the in-memory journal and recover only
    # from the snapshot that was synchronously persisted before provider dispatch.
    assert persisted["entries"][0]["status"] == "prepared"  # type: ignore[index]
    restarted_journal = OperationJournal.from_dict(persisted, persist=persist)
    adapter.crash_after_write = False
    restarted = GuardedGitHubSideEffects(adapter=adapter, journal=restarted_journal)
    result = restarted.execute(intent)

    assert result.status == "replayed"
    assert result.result_ref == "issue:durable-crash"
    assert adapter.calls == ["create_issue", "reconcile"]


def test_persistence_failure_prevents_provider_dispatch() -> None:
    def fail_persist(snapshot: dict[str, object]) -> None:
        raise OSError("durable store unavailable")

    journal = OperationJournal(persist=fail_persist)
    adapter = CrashAfterWriteAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter, journal=journal)
    intent = GitHubOperationIntent(
        "persist-fails",
        GitHubOperationKind.CREATE_ISSUE,
        FORK,
        {"title": "must not write"},
    )

    with pytest.raises(OSError, match="durable store unavailable"):
        guard.execute(intent)
    assert adapter.calls == []


@pytest.mark.parametrize(
    "kind", [GitHubOperationKind.MERGE_PR, GitHubOperationKind.TRIGGER_WORKFLOW]
)
def test_sensitive_integration_actions_require_explicit_target_ref(
    kind: GitHubOperationKind,
) -> None:
    adapter = CrashAfterWriteAdapter()
    guard = GuardedGitHubSideEffects(adapter=adapter)
    intent = GitHubOperationIntent(
        f"missing-ref-{kind.value}",
        kind,
        FORK,
        {},
    )

    decision = guard.evaluate(intent)
    assert decision.disposition is PolicyDisposition.DENY
    assert "ref" in decision.reason.lower()
    assert adapter.calls == []
