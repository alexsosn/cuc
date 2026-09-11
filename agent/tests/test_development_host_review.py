from __future__ import annotations

import errno
from pathlib import Path

import pytest

from harness.contracts import (
    ChangeSet,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent,
    TestKind,
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


FORK = "alexsosn/cuc"
BASE = "a" * 40
HEAD = "b" * 40


def _runtime():
    import harness.development_host as runtime

    return runtime


def test_directory_fsync_propagates_real_io_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported directory fsync may be tolerated; EIO must never be hidden."""

    runtime = _runtime()
    monkeypatch.setattr(runtime.os, "open", lambda _path, _flags: 123)
    monkeypatch.setattr(runtime.os, "close", lambda _fd: None)

    def fail_fsync(_fd):
        raise OSError(errno.EIO, "simulated directory I/O failure")

    monkeypatch.setattr(runtime.os, "fsync", fail_fsync)
    with pytest.raises(OSError) as caught:
        runtime._fsync_directory(Path("."))
    assert caught.value.errno == errno.EIO


def _request() -> GitHubEffectRequest:
    return GitHubEffectRequest(
        "operation-1",
        FORK,
        GitHubAction.UPDATE_CONTENTS,
        {"path": "agent/harness/development_host.py"},
    )


def _blocked_state(request: GitHubEffectRequest) -> DevelopmentControllerState:
    task = TaskSpec(
        "issue-53-review",
        "reconciliation durability",
        "recover an uncertain GitHub effect without a stranded controller state",
        ("atomic reconciliation checkpoint",),
    )
    research = ResearchArtifact("research-review", "review finding", ("pr:60",))
    plan = PlanArtifact("plan-review", "review fix", ("test", "fix"), ("pr:60",))
    intent = TestIntent(
        "review-regression",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_development_host_review.py"),
        "agent",
        "reconciliation restart durability",
    )
    core = RunState(
        "review-run",
        task,
        phase=RunPhase.BLOCKED,
        research=research,
        plan=plan,
        test_intents=(intent,),
        resume_phase=RunPhase.IMPLEMENT,
        pause_reason="trusted reconciliation required",
    )
    change = ChangeSet(
        "change-review",
        "pending write",
        ("agent/harness/development_host.py",),
        (request.operation_id,),
    )
    pending = ImplementationResult(
        change,
        HEAD,
        (request,),
        0.0,
        ("fixture:review",),
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
        raise AssertionError("reconciliation must not dispatch the write adapter")


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
        raise RuntimeError("unused review fixture port")

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
        raise RuntimeError("unused review fixture reviewer")

    return IndependentReviewer("reviewer", unused, "implementer")


def test_reconciliation_checkpoint_cannot_strand_resolved_journal_in_blocked_state(
    tmp_path: Path,
) -> None:
    """One durable reconciliation transition must include journal + controller resume."""

    runtime = _runtime()
    request = _request()
    initial = _blocked_state(request)

    class FailOnSecondReconciliationSave(runtime.AtomicJsonDevelopmentHostStore):
        armed = False
        reconciliation_saves = 0

        def save(self, envelope):
            if self.armed:
                self.reconciliation_saves += 1
                if self.reconciliation_saves == 2:
                    raise OSError("simulated crash window after resolved journal checkpoint")
            return super().save(envelope)

    store = FailOnSecondReconciliationSave(tmp_path / "host.json")
    store.save(runtime.DevelopmentHostEnvelope(1, initial, ()))
    adapter = _NoWriteAdapter()
    reconciler = _ExecutedReconciler()
    host = runtime.TrustedDevelopmentHost(
        store,
        controller_policy=DevelopmentControllerPolicy(
            max_revision_attempts=1,
            max_verification_executions=1,
            max_review_attempts=1,
            max_github_writes=1,
            require_red=False,
            production_mode=True,
        ),
        controller_ports=_ports(),
        reviewer=_reviewer(),
        github_policy=GitHubTaskPolicy(
            allowed_fork_write_operations=(
                GitHubOperationPermission(request.operation_id, request.action),
            )
        ),
        github_adapter=adapter,
        github_reconciler=reconciler,
        policy_refs=("policy:fork-only",),
        review_rubric=("independent review",),
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
    assert durable.controller_state.core.phase is RunPhase.IMPLEMENT
