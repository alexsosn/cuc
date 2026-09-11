from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    try:
        return importlib.import_module("harness.github_effects")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-009 GitHub effect boundary is not implemented yet: {exc}")


class FakeAdapter:
    def __init__(self, *, failure=None) -> None:
        self.calls = []
        self.failure = failure

    def execute(self, request):
        self.calls.append(request)
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        return f"fake:{request.action.value}:{len(self.calls)}"


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterPossibleEffectAdapter:
    def __init__(self, trace) -> None:
        self.calls = []
        self.trace = trace

    def execute(self, request):
        self.calls.append(request)
        self.trace.append("adapter")
        raise SimulatedProcessCrash("process died after the external call began")


def _policy(*actions, operation_ids=()):
    runtime = _runtime()
    return runtime.GitHubTaskPolicy(
        allowed_fork_write_actions=actions,
        allowed_operation_ids=operation_ids,
    )


def _execute(gateway, request, journal, checkpoints=None):
    if checkpoints is None:
        checkpoints = []
    result = gateway.execute_write(request, journal, checkpoint=checkpoints.append)
    return result, checkpoints


def _trusted_upstream_gateway(operation_id, adapter):
    runtime = _runtime()
    authority = runtime.HumanApprovalAuthority()
    gateway = runtime.GitHubEffectGateway(
        _policy(operation_ids=(operation_id,)),
        adapter,
        approval_authority=authority,
    )
    return gateway, authority


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
        "read-upstream-1",
        "DT-UCPH/cuc",
        runtime.GitHubAction.READ,
        {"resource": "README.md"},
    )
    assert gateway.authorize_read(read) is runtime.RepositoryClass.UPSTREAM
    assert adapter.calls == []

    write = runtime.GitHubEffectRequest(
        "write-upstream-1",
        "DT-UCPH/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "x"},
    )
    with pytest.raises(PermissionError, match="read|write"):
        gateway.authorize_read(write)


def test_fork_write_requires_task_policy_and_creates_attributable_receipt():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.CREATE_ISSUE,
            operation_ids=("op-fork-issue", "op-fork-pr"),
        ),
        adapter,
    )
    request = runtime.GitHubEffectRequest(
        "op-fork-issue",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "research finding"},
    )
    (journal, receipt), checkpoints = _execute(
        gateway, request, runtime.GitHubEffectJournal()
    )
    assert len(adapter.calls) == 1
    assert checkpoints[0].uncertain_operations == (request.operation_id,)
    assert journal.uncertain_operations == ()
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
        _execute(gateway, denied, journal)
    assert len(adapter.calls) == 1


def test_write_operation_must_be_declared_by_task_policy_before_any_adapter_or_approval():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(runtime.GitHubAction.CREATE_ISSUE, operation_ids=("declared-op",)),
        adapter,
    )
    undeclared_fork = runtime.GitHubEffectRequest(
        "invented-op",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "not attributable"},
    )
    with pytest.raises(PermissionError, match="operation|declared|policy"):
        _execute(gateway, undeclared_fork, runtime.GitHubEffectJournal())

    undeclared_upstream = runtime.GitHubEffectRequest(
        "invented-upstream-op",
        "DT-UCPH/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "must not even request approval"},
    )
    with pytest.raises(PermissionError, match="operation|declared|policy"):
        _execute(gateway, undeclared_upstream, runtime.GitHubEffectJournal())
    assert adapter.calls == []


def test_upstream_write_interrupts_before_adapter_and_trusted_exact_approval_unlocks_execution():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway, authority = _trusted_upstream_gateway("op-upstream-issue", adapter)
    request = runtime.GitHubEffectRequest(
        "op-upstream-issue",
        "DT-UCPH/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "release-only action"},
    )
    journal = runtime.GitHubEffectJournal()
    checkpoints = []
    with pytest.raises(runtime.HumanApprovalRequired) as captured:
        gateway.execute_write(request, journal, checkpoint=checkpoints.append)
    challenge = captured.value.challenge
    assert adapter.calls == []
    assert checkpoints == []
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
    authority.register(approval)
    approved = journal.with_approval(approval)
    (approved, receipt), checkpoints = _execute(gateway, request, approved)
    assert len(adapter.calls) == 1
    assert checkpoints[0].uncertain_operations == (request.operation_id,)
    assert approved.receipts == (receipt,)
    assert approved.uncertain_operations == ()


def test_upstream_approval_cannot_be_substituted_for_another_request():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway, authority = _trusted_upstream_gateway("op-upstream-comment", adapter)
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
    authority.register(wrong)
    journal = runtime.GitHubEffectJournal().with_approval(wrong)
    with pytest.raises(PermissionError, match="approval|digest|request"):
        _execute(gateway, request, journal)
    assert adapter.calls == []


def test_successful_effect_replays_from_serialized_receipt_without_duplicate_adapter_call():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.UPDATE_CONTENTS,
            operation_ids=("op-file-write",),
        ),
        adapter,
    )
    request = runtime.GitHubEffectRequest(
        "op-file-write",
        "alexsosn/cuc",
        runtime.GitHubAction.UPDATE_CONTENTS,
        {"path": "docs/example.md", "content_sha256": "a" * 64},
    )
    (journal, receipt), _ = _execute(gateway, request, runtime.GitHubEffectJournal())
    restored = runtime.GitHubEffectJournal.from_dict(
        json.loads(json.dumps(journal.to_dict(), sort_keys=True))
    )
    (restored, replayed), checkpoints = _execute(gateway, request, restored)
    assert replayed == receipt
    assert checkpoints == []
    assert len(adapter.calls) == 1

    mutated = runtime.GitHubEffectRequest(
        request.operation_id,
        "alexsosn/cuc",
        runtime.GitHubAction.UPDATE_CONTENTS,
        {"path": "docs/other.md", "content_sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="operation|digest|reuse"):
        _execute(gateway, mutated, restored)
    assert len(adapter.calls) == 1


def test_upstream_approval_and_separate_trusted_authority_survive_resume_and_safe_retry():
    runtime = _runtime()
    adapter = FakeAdapter(failure=runtime.AdapterEffectNotExecuted("preflight failed"))
    gateway, authority = _trusted_upstream_gateway("op-upstream-pr", adapter)
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
    authority.register(approval)
    journal = runtime.GitHubEffectJournal().with_approval(approval)
    restored = runtime.GitHubEffectJournal.from_dict(journal.to_dict())
    checkpoints = []

    with pytest.raises(runtime.AdapterEffectNotExecuted, match="preflight failed"):
        gateway.execute_write(request, restored, checkpoint=checkpoints.append)
    assert checkpoints[0].uncertain_operations == (request.operation_id,)
    assert checkpoints[-1].uncertain_operations == ()
    safe_to_retry = checkpoints[-1]

    (resumed, receipt), _ = _execute(gateway, request, safe_to_retry)
    assert len(adapter.calls) == 2
    assert resumed.receipts == (receipt,)


def test_ambiguous_adapter_failure_blocks_automatic_retry_after_possible_external_effect():
    runtime = _runtime()
    adapter = FakeAdapter(failure=RuntimeError("response lost after possible write"))
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.CREATE_ISSUE,
            operation_ids=("op-ambiguous",),
        ),
        adapter,
    )
    request = runtime.GitHubEffectRequest(
        "op-ambiguous",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "could already exist"},
    )
    checkpoints = []
    with pytest.raises(runtime.GitHubEffectOutcomeUnknown) as captured:
        gateway.execute_write(
            request,
            runtime.GitHubEffectJournal(),
            checkpoint=checkpoints.append,
        )
    uncertain = captured.value.journal
    assert checkpoints == [uncertain]
    assert uncertain.uncertain_operations == (request.operation_id,)
    assert uncertain.receipts == ()
    assert len(adapter.calls) == 1

    restored = runtime.GitHubEffectJournal.from_dict(uncertain.to_dict())
    retry_checkpoints = []
    with pytest.raises(runtime.GitHubEffectOutcomeUnknown, match="reconcile|uncertain|unknown"):
        gateway.execute_write(request, restored, checkpoint=retry_checkpoints.append)
    assert retry_checkpoints == []
    assert len(adapter.calls) == 1


def test_pre_dispatch_checkpoint_is_durable_before_adapter_and_process_kill_cannot_duplicate():
    runtime = _runtime()
    trace = []
    persisted = []
    adapter = CrashAfterPossibleEffectAdapter(trace)
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.CREATE_ISSUE,
            operation_ids=("op-crash-window",),
        ),
        adapter,
    )
    request = runtime.GitHubEffectRequest(
        "op-crash-window",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "external call may complete before process death"},
    )

    def checkpoint(journal):
        trace.append("checkpoint")
        persisted.append(runtime.GitHubEffectJournal.from_dict(journal.to_dict()))

    with pytest.raises(SimulatedProcessCrash):
        gateway.execute_write(
            request,
            runtime.GitHubEffectJournal(),
            checkpoint=checkpoint,
        )
    assert trace == ["checkpoint", "adapter"]
    assert persisted[-1].uncertain_operations == (request.operation_id,)
    assert len(adapter.calls) == 1

    with pytest.raises(runtime.GitHubEffectOutcomeUnknown, match="reconcile|uncertain|unknown"):
        gateway.execute_write(request, persisted[-1], checkpoint=checkpoint)
    assert len(adapter.calls) == 1
    assert trace == ["checkpoint", "adapter"]


def test_write_path_cannot_omit_checkpoint_and_checkpoint_failure_prevents_adapter_call():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.CREATE_ISSUE,
            operation_ids=("op-needs-checkpoint",),
        ),
        adapter,
    )
    request = runtime.GitHubEffectRequest(
        "op-needs-checkpoint",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "checkpoint first"},
    )
    with pytest.raises(TypeError):
        gateway.execute_write(request, runtime.GitHubEffectJournal())
    assert adapter.calls == []

    def broken_checkpoint(_journal):
        raise RuntimeError("durable store unavailable")

    with pytest.raises(RuntimeError, match="durable store unavailable"):
        gateway.execute_write(
            request,
            runtime.GitHubEffectJournal(),
            checkpoint=broken_checkpoint,
        )
    assert adapter.calls == []


def test_generic_raw_actions_and_unknown_repository_writes_fail_closed():
    runtime = _runtime()
    adapter = FakeAdapter()
    gateway = runtime.GitHubEffectGateway(
        _policy(
            runtime.GitHubAction.CREATE_ISSUE,
            operation_ids=("op-unknown",),
        ),
        adapter,
    )
    with pytest.raises(ValueError, match="action"):
        runtime.GitHubEffectRequest(
            "op-raw", "alexsosn/cuc", "raw", {"url": "/repos/x/y"}
        )
    assert not hasattr(gateway, "execute_raw")
    assert not hasattr(gateway, "adapter")

    unknown = runtime.GitHubEffectRequest(
        "op-unknown",
        "someone/else",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "must not execute"},
    )
    with pytest.raises(PermissionError, match="unknown|repository"):
        _execute(gateway, unknown, runtime.GitHubEffectJournal())
    assert adapter.calls == []


def test_repository_safety_static_guard_covers_development_harness_transport_bypass():
    text = (REPO_ROOT / "agent" / "tests" / "test_repository_safety.py").read_text(
        encoding="utf-8"
    )
    assert 'REPO_ROOT / "agent" / "harness"' in text
    assert "DIRECT_GITHUB_TRANSPORT_MARKERS" in text


def test_journal_rejects_malformed_collections_duplicates_and_invalid_receipts():
    runtime = _runtime()
    with pytest.raises(ValueError, match="approvals"):
        runtime.GitHubEffectJournal.from_dict(
            {"approvals": "not-a-list", "receipts": [], "uncertain_requests": []}
        )

    approval = runtime.HumanApproval("approval-1", "human", "op-1", "1" * 64)
    with pytest.raises(ValueError, match="approval"):
        runtime.GitHubEffectJournal((approval, approval), (), ())

    receipt = runtime.GitHubEffectReceipt(
        "op-1",
        "1" * 64,
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        "fake:1",
    )
    with pytest.raises(ValueError, match="receipt|operation"):
        runtime.GitHubEffectJournal((), (receipt, receipt), ())
    with pytest.raises(ValueError, match="sha|digest"):
        runtime.GitHubEffectReceipt(
            "op-2",
            "not-a-digest",
            "alexsosn/cuc",
            runtime.GitHubAction.CREATE_ISSUE,
            "x",
        )
