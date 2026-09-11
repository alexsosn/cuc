from __future__ import annotations

import copy

import pytest

from harness.github_side_effects import (
    ApprovalRequired,
    GitHubDestination,
    GitHubOperationIntent,
    GitHubOperationKind,
    GitHubTarget,
    GuardedGitHubSideEffects,
    HumanApprovalGrant,
    HumanApprovalRegistry,
    OperationJournal,
    OperationReplayConflict,
    PolicyDisposition,
    SideEffectDenied,
)


FORK = GitHubTarget("alexsosn", "cuc")
UPSTREAM = GitHubTarget("DT-UCPH", "cuc")


class FakeGitHubAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.reconciled: dict[str, str] = {}
        self.fail_after_write: set[str] = set()

    def _write(self, name: str, intent: GitHubOperationIntent) -> str:
        self.calls.append((name, intent.operation_id))
        result = f"{name}:{intent.operation_id}"
        if intent.operation_id in self.fail_after_write:
            self.reconciled[intent.operation_id] = result
            raise RuntimeError("simulated crash after provider write")
        return result

    def read(self, intent: GitHubOperationIntent) -> str:
        self.calls.append(("read", intent.operation_id))
        return f"read:{intent.operation_id}"

    def reconcile(self, intent: GitHubOperationIntent) -> str | None:
        self.calls.append(("reconcile", intent.operation_id))
        return self.reconciled.get(intent.operation_id)

    def create_branch(self, intent: GitHubOperationIntent) -> str:
        return self._write("create_branch", intent)

    def update_branch(self, intent: GitHubOperationIntent) -> str:
        return self._write("update_branch", intent)

    def create_issue(self, intent: GitHubOperationIntent) -> str:
        return self._write("create_issue", intent)

    def update_issue(self, intent: GitHubOperationIntent) -> str:
        return self._write("update_issue", intent)

    def create_pr(self, intent: GitHubOperationIntent) -> str:
        return self._write("create_pr", intent)

    def update_pr(self, intent: GitHubOperationIntent) -> str:
        return self._write("update_pr", intent)

    def comment(self, intent: GitHubOperationIntent) -> str:
        return self._write("comment", intent)

    def merge_pr(self, intent: GitHubOperationIntent) -> str:
        return self._write("merge_pr", intent)

    def trigger_workflow(self, intent: GitHubOperationIntent) -> str:
        return self._write("trigger_workflow", intent)


def _intent(
    operation_id: str,
    kind: GitHubOperationKind,
    target: GitHubTarget = FORK,
    *,
    ref: str | None = None,
    payload: dict[str, object] | None = None,
) -> GitHubOperationIntent:
    if ref is not None:
        target = GitHubTarget(target.owner, target.repo, ref)
    return GitHubOperationIntent(operation_id, kind, target, payload or {})


def _approval(intent: GitHubOperationIntent, approval_id: str = "human-approval-1") -> HumanApprovalGrant:
    return HumanApprovalGrant(
        approval_id=approval_id,
        approved_by="human-user",
        operation_id=intent.operation_id,
        operation_fingerprint=intent.fingerprint,
        issued_at="2026-09-11T12:00:00+03:00",
    )


def _registry(*grants: HumanApprovalGrant) -> HumanApprovalRegistry:
    registry = HumanApprovalRegistry()
    for grant in grants:
        registry.register(grant)
    return registry


def _guard(
    adapter: FakeGitHubAdapter | None = None,
    journal: OperationJournal | None = None,
    approvals: HumanApprovalRegistry | None = None,
):
    adapter = adapter or FakeGitHubAdapter()
    return (
        GuardedGitHubSideEffects(
            adapter=adapter,
            journal=journal or OperationJournal(),
            approvals=approvals or HumanApprovalRegistry(),
        ),
        adapter,
    )


def test_destination_classification_is_closed_and_case_insensitive() -> None:
    guard, _ = _guard()
    assert guard.classify(GitHubTarget("AlexSosn", "CUC")) is GitHubDestination.FORK
    assert guard.classify(GitHubTarget("dt-ucph", "CuC")) is GitHubDestination.UPSTREAM
    assert guard.classify(GitHubTarget("someone-else", "cuc")) is GitHubDestination.DENIED


def test_reads_of_fork_and_upstream_are_allowed() -> None:
    guard, adapter = _guard()
    fork = _intent("read-fork", GitHubOperationKind.READ)
    upstream = _intent("read-upstream", GitHubOperationKind.READ, UPSTREAM)

    assert guard.evaluate(fork).disposition is PolicyDisposition.ALLOW
    assert guard.evaluate(upstream).disposition is PolicyDisposition.ALLOW
    assert guard.execute(fork).result_ref == "read:read-fork"
    assert guard.execute(upstream).result_ref == "read:read-upstream"
    assert adapter.calls == [("read", "read-fork"), ("read", "read-upstream")]


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        (GitHubOperationKind.CREATE_BRANCH, "feature/harn-009"),
        (GitHubOperationKind.UPDATE_BRANCH, "feature/harn-009"),
        (GitHubOperationKind.CREATE_ISSUE, None),
        (GitHubOperationKind.UPDATE_ISSUE, None),
        (GitHubOperationKind.CREATE_PR, None),
        (GitHubOperationKind.UPDATE_PR, None),
        (GitHubOperationKind.COMMENT, None),
    ],
)
def test_controlled_fork_development_writes_are_allowed(
    kind: GitHubOperationKind, ref: str | None
) -> None:
    guard, adapter = _guard()
    intent = _intent(f"fork-{kind.value}", kind, ref=ref)

    assert guard.evaluate(intent).disposition is PolicyDisposition.ALLOW
    result = guard.execute(intent)
    assert result.status == "executed"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("ref", ["main", "agent-harness-safety"])
@pytest.mark.parametrize(
    "kind", [GitHubOperationKind.CREATE_BRANCH, GitHubOperationKind.UPDATE_BRANCH]
)
def test_direct_fork_updates_to_integration_refs_are_denied(
    kind: GitHubOperationKind, ref: str
) -> None:
    guard, adapter = _guard()
    intent = _intent(f"unsafe-{kind.value}-{ref}", kind, ref=ref)

    assert guard.evaluate(intent).disposition is PolicyDisposition.DENY
    with pytest.raises(SideEffectDenied):
        guard.execute(intent, approval=_approval(intent))
    assert adapter.calls == []


@pytest.mark.parametrize(
    "kind", [GitHubOperationKind.MERGE_PR, GitHubOperationKind.TRIGGER_WORKFLOW]
)
def test_sensitive_fork_actions_require_exact_human_approval(kind: GitHubOperationKind) -> None:
    intent = _intent(f"sensitive-{kind.value}", kind, ref="agent-harness-safety")
    grant = _approval(intent)
    guard, adapter = _guard(approvals=_registry(grant))

    assert guard.evaluate(intent).disposition is PolicyDisposition.REQUIRE_APPROVAL
    with pytest.raises(ApprovalRequired):
        guard.execute(intent)
    assert adapter.calls == []

    result = guard.execute(intent, approval=grant)
    assert result.status == "executed"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "kind",
    [
        GitHubOperationKind.CREATE_BRANCH,
        GitHubOperationKind.UPDATE_BRANCH,
        GitHubOperationKind.CREATE_ISSUE,
        GitHubOperationKind.UPDATE_ISSUE,
        GitHubOperationKind.CREATE_PR,
        GitHubOperationKind.UPDATE_PR,
        GitHubOperationKind.COMMENT,
        GitHubOperationKind.MERGE_PR,
        GitHubOperationKind.TRIGGER_WORKFLOW,
    ],
)
def test_every_upstream_write_requires_exact_approval(kind: GitHubOperationKind) -> None:
    ref = "release/harn-009" if kind in {
        GitHubOperationKind.CREATE_BRANCH,
        GitHubOperationKind.UPDATE_BRANCH,
        GitHubOperationKind.MERGE_PR,
        GitHubOperationKind.TRIGGER_WORKFLOW,
    } else None
    intent = _intent(f"upstream-{kind.value}", kind, UPSTREAM, ref=ref)
    grant = _approval(intent)
    guard, adapter = _guard(approvals=_registry(grant))

    assert guard.evaluate(intent).disposition is PolicyDisposition.REQUIRE_APPROVAL
    with pytest.raises(ApprovalRequired):
        guard.execute(intent)
    assert adapter.calls == []

    result = guard.execute(intent, approval=grant)
    assert result.status == "executed"
    assert len(adapter.calls) == 1


def test_unknown_repository_is_denied_even_with_approval() -> None:
    guard, adapter = _guard()
    intent = _intent(
        "other-repo",
        GitHubOperationKind.CREATE_ISSUE,
        GitHubTarget("not-allowed", "other"),
    )

    with pytest.raises(SideEffectDenied):
        guard.execute(intent, approval=_approval(intent))
    assert adapter.calls == []


@pytest.mark.parametrize("mutation", ["id", "action", "repo", "ref", "payload"])
def test_approval_is_bound_to_exact_operation_fingerprint(mutation: str) -> None:
    original = _intent(
        "approved-upstream",
        GitHubOperationKind.CREATE_PR,
        UPSTREAM,
        ref="main",
        payload={"title": "release", "base": "main"},
    )
    grant = _approval(original)
    guard, adapter = _guard(approvals=_registry(grant))

    if mutation == "id":
        changed = _intent("different-id", original.kind, UPSTREAM, ref="main", payload=dict(original.payload))
    elif mutation == "action":
        changed = _intent(original.operation_id, GitHubOperationKind.UPDATE_PR, UPSTREAM, ref="main", payload=dict(original.payload))
    elif mutation == "repo":
        changed = _intent(original.operation_id, original.kind, GitHubTarget("DT-UCPH", "other"), ref="main", payload=dict(original.payload))
    elif mutation == "ref":
        changed = _intent(original.operation_id, original.kind, UPSTREAM, ref="release", payload=dict(original.payload))
    else:
        changed = _intent(original.operation_id, original.kind, UPSTREAM, ref="main", payload={"title": "changed"})

    with pytest.raises((ApprovalRequired, SideEffectDenied)):
        guard.execute(changed, approval=grant)
    assert adapter.calls == []


def test_payload_cannot_smuggle_approval_or_change_destination() -> None:
    guard, adapter = _guard()
    upstream = _intent(
        "smuggled-approval",
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        payload={"approved": True, "human_approval": "yes"},
    )
    with pytest.raises(ApprovalRequired):
        guard.execute(upstream)

    fork = _intent(
        "payload-url",
        GitHubOperationKind.CREATE_ISSUE,
        FORK,
        payload={"url": "https://api.github.com/repos/DT-UCPH/cuc/issues"},
    )
    assert guard.execute(fork).status == "executed"
    assert adapter.calls == [("create_issue", "payload-url")]


def test_completed_replay_does_not_duplicate_write() -> None:
    guard, adapter = _guard()
    intent = _intent("once", GitHubOperationKind.CREATE_ISSUE, payload={"title": "one"})

    first = guard.execute(intent)
    second = guard.execute(intent)

    assert first.status == "executed"
    assert second.status == "replayed"
    assert second.result_ref == first.result_ref
    assert adapter.calls == [("create_issue", "once")]


def test_operation_id_reuse_with_changed_fingerprint_is_rejected() -> None:
    guard, adapter = _guard()
    original = _intent("stable-id", GitHubOperationKind.CREATE_ISSUE, payload={"title": "one"})
    changed = _intent("stable-id", GitHubOperationKind.CREATE_ISSUE, payload={"title": "two"})

    guard.execute(original)
    with pytest.raises(OperationReplayConflict):
        guard.execute(changed)
    assert adapter.calls == [("create_issue", "stable-id")]


def test_crash_after_provider_write_reconciles_before_retry_and_avoids_duplicate() -> None:
    journal = OperationJournal()
    adapter = FakeGitHubAdapter()
    adapter.fail_after_write.add("crash-write")
    guard, _ = _guard(adapter, journal)
    intent = _intent("crash-write", GitHubOperationKind.CREATE_PR, payload={"title": "PR"})

    with pytest.raises(RuntimeError, match="crash after provider write"):
        guard.execute(intent)
    assert journal.status("crash-write") == "prepared"
    assert adapter.calls == [("create_pr", "crash-write")]

    adapter.fail_after_write.clear()
    restarted = GuardedGitHubSideEffects(
        adapter=adapter,
        journal=OperationJournal.from_dict(journal.to_dict()),
    )
    result = restarted.execute(intent)

    assert result.status == "replayed"
    assert adapter.calls == [
        ("create_pr", "crash-write"),
        ("reconcile", "crash-write"),
    ]


def test_prepared_replay_with_no_provider_result_executes_exactly_one_retry() -> None:
    journal = OperationJournal()
    adapter = FakeGitHubAdapter()
    guard, _ = _guard(adapter, journal)
    intent = _intent("retry-once", GitHubOperationKind.CREATE_ISSUE, payload={"title": "retry"})
    journal.prepare(intent, approval_id=None)

    result = guard.execute(intent)

    assert result.status == "executed"
    assert adapter.calls == [
        ("reconcile", "retry-once"),
        ("create_issue", "retry-once"),
    ]


def test_journal_round_trip_preserves_completed_replay() -> None:
    guard, adapter = _guard()
    intent = _intent("persisted", GitHubOperationKind.CREATE_ISSUE, payload={"title": "persist"})
    first = guard.execute(intent)

    restored = OperationJournal.from_dict(guard.journal.to_dict())
    restarted = GuardedGitHubSideEffects(adapter=adapter, journal=restored)
    second = restarted.execute(intent)

    assert second.status == "replayed"
    assert second.result_ref == first.result_ref
    assert adapter.calls == [("create_issue", "persisted")]


def test_tampered_serialized_journal_is_rejected() -> None:
    guard, _ = _guard()
    intent = _intent("tamper", GitHubOperationKind.CREATE_ISSUE, payload={"title": "safe"})
    guard.execute(intent)
    serialized = copy.deepcopy(guard.journal.to_dict())
    serialized["entries"][0]["fingerprint"] = "0" * 64

    with pytest.raises(ValueError, match="fingerprint"):
        OperationJournal.from_dict(serialized)


def test_dry_run_has_no_transport_or_journal_side_effects() -> None:
    guard, adapter = _guard()
    intent = _intent("dry", GitHubOperationKind.CREATE_ISSUE, payload={"title": "dry"})

    result = guard.execute(intent, dry_run=True)

    assert result.status == "dry-run"
    assert result.intent_fingerprint == intent.fingerprint
    assert adapter.calls == []
    assert guard.journal.status("dry") is None


def test_dry_run_reports_approval_requirement_without_accepting_payload_approval() -> None:
    guard, adapter = _guard()
    intent = _intent(
        "dry-upstream",
        GitHubOperationKind.CREATE_ISSUE,
        UPSTREAM,
        payload={"approved": True},
    )

    result = guard.execute(intent, dry_run=True)
    assert result.status == "dry-run"
    assert result.policy_disposition is PolicyDisposition.REQUIRE_APPROVAL
    assert adapter.calls == []


def test_unknown_operation_string_fails_closed_before_adapter_dispatch() -> None:
    with pytest.raises(ValueError):
        GitHubOperationIntent(
            "generic-request",
            "request",  # type: ignore[arg-type]
            FORK,
            {"method": "POST", "url": "https://api.github.com/repos/DT-UCPH/cuc/issues"},
        )


def test_closed_operation_enum_has_no_generic_http_escape_hatch() -> None:
    forbidden = {"request", "graphql", "rest", "http", "cli", "shell", "raw"}
    values = {kind.value for kind in GitHubOperationKind}
    assert not values.intersection(forbidden)


def test_branch_write_requires_explicit_ref() -> None:
    guard, adapter = _guard()
    intent = _intent("no-ref", GitHubOperationKind.UPDATE_BRANCH)
    with pytest.raises(SideEffectDenied):
        guard.execute(intent)
    assert adapter.calls == []


def test_intent_and_approval_round_trip_are_strict_and_deterministic() -> None:
    intent = _intent(
        "roundtrip",
        GitHubOperationKind.CREATE_PR,
        UPSTREAM,
        ref="main",
        payload={"nested": {"b": 2, "a": [1, True, None]}},
    )
    restored = GitHubOperationIntent.from_dict(intent.to_dict())
    grant = _approval(intent)

    assert restored == intent
    assert restored.fingerprint == intent.fingerprint
    assert HumanApprovalGrant.from_dict(grant.to_dict()) == grant
