from __future__ import annotations

import errno
from pathlib import Path

import pytest

import harness.production_host as production_host
from harness.contracts import (
    ChangeSet,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent as ContractTestIntent,
    TestKind as ContractTestKind,
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
    GitHubEffectRequest,
    GitHubEffectUncertainRequest,
    GitHubOperationPermission,
    GitHubReconciliationDisposition,
    GitHubReconciliationResult,
    GitHubTaskPolicy,
)
from harness.production_host import (
    AtomicHostStateStore,
    ProductionDevelopmentHost,
    ProductionHostEnvelope,
)


FORK = "alexsosn/cuc"
BASE = "a" * 40
HEAD = "b" * 40


def _request() -> GitHubEffectRequest:
    return GitHubEffectRequest(
        "operation-orphan-regression",
        FORK,
        GitHubAction.CREATE_ISSUE,
        {"title": "orphaned branch recovery regression"},
    )


def _blocked_state(request: GitHubEffectRequest) -> DevelopmentControllerState:
    task = TaskSpec(
        "issue-53-orphan-regression",
        "atomic reconciliation recovery",
        "persist resolved GitHub journal and resumed controller state atomically",
        ("no crash window may strand a resolved journal in BLOCKED state",),
    )
    intent = ContractTestIntent(
        "orphan-regression",
        ContractTestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_production_host_superseded_branch_regressions.py"),
        "agent",
        "atomic reconciliation recovery regression",
    )
    core = RunState(
        "run-orphan-regression",
        task,
        phase=RunPhase.BLOCKED,
        research=ResearchArtifact("research-orphan", "compared orphaned host branches", ("pr:60", "pr:61")),
        plan=PlanArtifact("plan-orphan", "port unique crash-window invariant", ("RED", "fix")),
        test_intents=(intent,),
        resume_phase=RunPhase.IMPLEMENT,
        pause_reason="trusted reconciliation required",
    )
    pending = ImplementationResult(
        ChangeSet(
            "change-orphan",
            "pending GitHub effect",
            ("agent/harness/production_host.py",),
            (request.operation_id,),
        ),
        HEAD,
        (request,),
    )
    uncertain = GitHubEffectUncertainRequest(
        request.operation_id,
        request.request_sha256,
        request.repository,
        request.action,
    )
    return DevelopmentControllerState(
        schema_version=1,
        base_sha=BASE,
        core=core,
        pending_implementation=pending,
        pending_operation_index=0,
        current_head_sha=HEAD,
        github_journal=GitHubEffectJournal(uncertain_requests=(uncertain,)),
        stop_code=ControllerStopCode.BLOCKED_EXECUTION,
        stop_reason="trusted reconciliation required",
        audit_events=("fixture:blocked",),
    )


class _NoWriteAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _request):
        self.calls += 1
        raise AssertionError("trusted reconciliation must never dispatch the write adapter")


class _ExecutedReconciler:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile(self, request):
        self.calls += 1
        return GitHubReconciliationResult(
            GitHubReconciliationDisposition.EXECUTED,
            f"reconciled:{request.operation_id}",
        )


def _ports() -> DevelopmentControllerPorts:
    def unused(*_args, **_kwargs):
        raise AssertionError("model-facing port must not be called")

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


def _reviewer() -> IndependentReviewer:
    def unused(_context):
        raise AssertionError("reviewer must not be called")

    return IndependentReviewer("reviewer", unused, implementer_id="implementer")


def test_reconciliation_is_one_durable_transition_not_two_save_crash_window(tmp_path) -> None:
    """Resolved journal + resumed controller must be one durable host transition.

    A crash after persisting only the resolved journal strands a BLOCKED controller
    whose uncertainty is already gone, making neither reconcile nor resume valid.
    """

    request = _request()
    initial = _blocked_state(request)

    class FailOnSecondReconciliationSave(AtomicHostStateStore):
        armed = False
        reconciliation_saves = 0

        def save(self, envelope):
            if self.armed:
                self.reconciliation_saves += 1
                if self.reconciliation_saves == 2:
                    raise OSError("simulated crash after resolved-journal checkpoint")
            return super().save(envelope)

    store = FailOnSecondReconciliationSave(tmp_path / "host.json")
    store.save(ProductionHostEnvelope(1, initial, ()))
    adapter = _NoWriteAdapter()
    reconciler = _ExecutedReconciler()
    host = ProductionDevelopmentHost(
        store=store,
        policy=DevelopmentControllerPolicy(
            max_revision_attempts=1,
            max_verification_executions=1,
            max_review_attempts=1,
            max_github_writes=1,
            require_red=False,
            production_mode=True,
        ),
        ports=_ports(),
        reviewer=_reviewer(),
        github_policy=GitHubTaskPolicy(
            allowed_fork_write_operations=(
                GitHubOperationPermission(request.operation_id, request.action),
            )
        ),
        github_adapter=adapter,
        github_reconciler=reconciler,
        policy_refs=("AGENTS.md", "HARN-023", "issue:53"),
        review_rubric=("atomic recovery",),
        implementer_id="implementer",
    )
    store.armed = True

    reconciled = host.reconcile_uncertain()

    assert store.reconciliation_saves == 1
    assert reconciler.calls == 1
    assert adapter.calls == 0
    assert reconciled.core.phase is RunPhase.IMPLEMENT
    assert reconciled.stop_code is None
    assert reconciled.github_journal.uncertain_for(request.operation_id) is None
    assert reconciled.github_journal.receipt_for(request.operation_id) is not None

    durable = store.load()
    assert durable is not None
    assert durable.controller_state == reconciled


def test_directory_fsync_propagates_real_io_failure(monkeypatch) -> None:
    """Only unsupported directory fsync is tolerated; real I/O failure is fatal."""

    monkeypatch.setattr(production_host.os, "open", lambda _path, _flags: 123)
    monkeypatch.setattr(production_host.os, "close", lambda _fd: None)

    def fail_fsync(_fd):
        raise OSError(errno.EIO, "simulated directory I/O failure")

    monkeypatch.setattr(production_host.os, "fsync", fail_fsync)
    with pytest.raises(OSError) as caught:
        AtomicHostStateStore._fsync_parent_directory(Path("."))
    assert caught.value.errno == errno.EIO
