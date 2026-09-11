from __future__ import annotations

import importlib
import json

import pytest


def _runtime():
    try:
        return importlib.import_module("harness.github_effects")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-009 GitHub effect boundary is not implemented yet: {exc}")


class FakeAdapter:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls = []
        self.fail_once = fail_once

    def execute(self, request):
        self.calls.append(request)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("transient fake adapter failure")
        return f"fake:{request.action.value}:{len(self.calls)}"


def _policy(*actions):
    runtime = _runtime()
    return runtime.GitHubTaskPolicy(allowed_fork_write_actions=actions)


def test_repository_classification_is_exact_and_urls_do_not_bypass_it():
    runtime = _runtime()
    assert runtime.classify_repository(" alexsosn/CUC ") is runtime.RepositoryClass.FORK
    assert runtime.classify_repository("dt-ucph/cuc") is runtime.RepositoryClass.UPSTREAM
    assert runtime.classify_repository("someone/else") is runtime.RepositoryClass.UNKNOWN
    with pytest.raises(ValueError, match="repository|owner/repo"):
        runtime.classify_repository("https://github.com/alexsosn/cuc")


def test_upstream_read_is_allowed_but_write_cannot_enter_read_path():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(_policy(), adapter)
    read = runtime.GitHubEffectRequest(
        "read-upstream-1", "DT-UCPH/cuc", runtime.GitHubAction.READ, {"resource": "README.md"}
    )
    assert gateway.authorize_read(read) is runtime.RepositoryClass.UPSTREAM
    assert adapter.calls == []

    write = runtime.GitHubEffectRequest(
        "write-upstream-1", "DT-UCPH/cuc", runtime.GitHubAction.CREATE_ISSUE, {"title": "x"}
    )
    with pytest.raises(PermissionError, match="read|write"):
        gateway.authorize_read(write)


def test_fork_write_requires_task_policy_and_creates_attributable_receipt():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(runtime.GitHubAction.CREATE_ISSUE), adapter
    )
    request = runtime.GitHubEffectRequest(
        "op-fork-issue",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "research finding"},
    )
    journal, receipt = gateway.execute_write(request, runtime.GitHubEffectJournal())
    assert len(adapter.calls) == 1
    assert receipt.operation_id == request.operation_id
    assert receipt.request_sha256 == request.request_sha256
    assert receipt.repository == "alexsosn/cuc"
    assert receipt.action is runtime.GitHubAction.CREATE_ISSUE
    assert journal.receipts == (receipt,)

    denied = runtime.GitHubEffectRequest(
        "op-fork-pr",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_PULL_REQUEST,
        {"head": "x", "base": "agent-harness-safety"},
    )
    with pytest.raises(PermissionError, match="policy|allow"):
        gateway.execute_write(denied, journal)
    assert len(adapter.calls) == 1


def test_upstream_write_interrupts_before_adapter_and_exact_approval_unlocks_fake_execution():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(_policy(), adapter)
    request = runtime.GitHubEffectRequest(
        "op-upstream-issue",
        "DT-UCPH/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "release-only action"},
    )
    journal = runtime.GitHubEffectJournal()
    with pytest.raises(runtime.HumanApprovalRequired) as captured:
        gateway.execute_write(request, journal)
    challenge = captured.value.challenge
    assert adapter.calls == []
    assert challenge.operation_id == request.operation_id
    assert challenge.request_sha256 == request.request_sha256
    assert challenge.repository == "DT-UCPH/cuc"
    assert challenge.action is runtime.GitHubAction.CREATE_ISSUE

    approval = runtime.HumanApproval(
        "approval-1",
        "human-user",
        challenge.operation_id,
        challenge.request_sha256,
    )
    approved = journal.with_approval(approval)
    approved, receipt = gateway.execute_write(request, approved)
    assert len(adapter.calls) == 1
    assert approved.receipts == (receipt,)


def test_upstream_approval_cannot_be_substituted_for_another_request():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(_policy(), adapter)
    request = runtime.GitHubEffectRequest(
        "op-upstream-comment",
        "DT-UCPH/cuc",
        runtime.GitHubAction.COMMENT,
        {"issue": 1, "body": "approved body"},
    )
    wrong = runtime.HumanApproval(
        "approval-wrong",
        "human-user",
        request.operation_id,
        "0" * 64,
    )
    journal = runtime.GitHubEffectJournal().with_approval(wrong)
    with pytest.raises(PermissionError, match="approval|digest|request"):
        gateway.execute_write(request, journal)
    assert adapter.calls == []


def test_successful_effect_replays_from_serialized_receipt_without_duplicate_adapter_call():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(runtime.GitHubAction.UPDATE_CONTENTS), adapter
    )
    request = runtime.GitHubEffectRequest(
        "op-file-write",
        "alexsosn/cuc",
        runtime.GitHubAction.UPDATE_CONTENTS,
        {"path": "docs/example.md", "content_sha256": "a" * 64},
    )
    journal, receipt = gateway.execute_write(request, runtime.GitHubEffectJournal())
    restored = runtime.GitHubEffectJournal.from_dict(
        json.loads(json.dumps(journal.to_dict(), sort_keys=True))
    )
    restored, replayed = gateway.execute_write(request, restored)
    assert replayed == receipt
    assert len(adapter.calls) == 1

    mutated = runtime.GitHubEffectRequest(
        request.operation_id,
        "alexsosn/cuc",
        runtime.GitHubAction.UPDATE_CONTENTS,
        {"path": "docs/other.md", "content_sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="operation|digest|reuse"):
        gateway.execute_write(mutated, restored)
    assert len(adapter.calls) == 1


def test_upstream_approval_survives_resume_and_transient_failure_does_not_create_receipt():
    runtime = _runtime()
    adapter = FakeAdapter(fail_once=True)
    gateway = runtime.GitHubEffectGateway(_policy(), adapter)
    request = runtime.GitHubEffectRequest(
        "op-upstream-pr",
        "DT-UCPH/cuc",
        runtime.GitHubAction.CREATE_PULL_REQUEST,
        {"head": "alexsosn:release", "base": "main"},
    )
    approval = runtime.HumanApproval(
        "approval-pr",
        "human-user",
        request.operation_id,
        request.request_sha256,
    )
    journal = runtime.GitHubEffectJournal().with_approval(approval)
    restored = runtime.GitHubEffectJournal.from_dict(journal.to_dict())

    with pytest.raises(RuntimeError, match="transient fake adapter failure"):
        gateway.execute_write(request, restored)
    assert restored.receipts == ()

    resumed, receipt = gateway.execute_write(request, restored)
    assert len(adapter.calls) == 2
    assert resumed.receipts == (receipt,)


def test_generic_raw_actions_and_unknown_repository_writes_fail_closed():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(runtime.GitHubAction.CREATE_ISSUE), adapter
    )
    with pytest.raises(ValueError, match="action"):
        runtime.GitHubEffectRequest("op-raw", "alexsosn/cuc", "raw", {"url": "/repos/x/y"})
    assert not hasattr(gateway, "execute_raw")
    assert not hasattr(gateway, "adapter")

    unknown = runtime.GitHubEffectRequest(
        "op-unknown",
        "someone/else",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "must not execute"},
    )
    with pytest.raises(PermissionError, match="unknown|repository"):
        gateway.execute_write(unknown, runtime.GitHubEffectJournal())
    assert adapter.calls == []


def test_journal_rejects_malformed_collections_duplicates_and_invalid_receipts():
    runtime = _runtime()
    with pytest.raises(ValueError, match="approvals"):
        runtime.GitHubEffectJournal.from_dict({"approvals": "not-a-list", "receipts": []})

    approval = runtime.HumanApproval("approval-1", "human", "op-1", "1" * 64)
    with pytest.raises(ValueError, match="approval"):
        runtime.GitHubEffectJournal((approval, approval), ())

    receipt = runtime.GitHubEffectReceipt(
        "op-1",
        "1" * 64,
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        "fake:1",
    )
    with pytest.raises(ValueError, match="receipt|operation"):
        runtime.GitHubEffectJournal((), (receipt, receipt))
    with pytest.raises(ValueError, match="sha|digest"):
        runtime.GitHubEffectReceipt(
            "op-2", "not-a-digest", "alexsosn/cuc", runtime.GitHubAction.CREATE_ISSUE, "x"
        )
