from __future__ import annotations

from dataclasses import replace
import importlib
import inspect

import pytest

from harness.contracts import (
    ChangeSet,
    EvalResult,
    FindingSeverity,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    ReviewDisposition,
    RunPhase,
    TaskSpec,
    TestIntent,
    TestKind,
    TestResult,
)
from harness.development_reviewer import (
    DevelopmentFindingCategory,
    DevelopmentReviewFinding,
    DevelopmentReviewReport,
    IndependentReviewer,
)
from harness.github_effects import (
    GitHubAction,
    GitHubEffectGateway,
    GitHubEffectRequest,
    GitHubOperationPermission,
    GitHubTaskPolicy,
    HumanApprovalAuthority,
)


BASE = "a" * 40
HEAD1 = "b" * 40
HEAD2 = "c" * 40


def _runtime():
    try:
        return importlib.import_module("harness.development_controller")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-010 runtime is not implemented yet: {exc}")


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-11",
        "bounded controller",
        "run research-plan-TDD-review without unbounded retries",
        (
            "real RED before implementation",
            "fresh candidate verification",
            "clean independent review",
        ),
    )


def _targeted() -> TestIntent:
    return TestIntent(
        "targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "new behavior fails before implementation",
    )


def _regression() -> TestIntent:
    return TestIntent(
        "regression",
        TestKind.REGRESSION,
        ("python", "-m", "pytest", "-q"),
        "agent",
        "full regression suite",
    )


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[GitHubEffectRequest] = []

    def execute(self, request: GitHubEffectRequest) -> str:
        self.calls.append(request)
        return f"result:{request.operation_id}"


class EmptyReconciler:
    def reconcile(self, request: GitHubEffectRequest):
        runtime = _runtime()
        return runtime._test_not_executed_reconciliation_result()


def _gateway(
    requests: tuple[GitHubEffectRequest, ...] = (),
    *,
    adapter: RecordingAdapter | None = None,
    authority: HumanApprovalAuthority | None = None,
):
    adapter = adapter or RecordingAdapter()
    permissions = tuple(
        GitHubOperationPermission(item.operation_id, item.action)
        for item in requests
        if item.repository.casefold() == "alexsosn/cuc"
    )
    upstream = tuple(
        item.operation_id
        for item in requests
        if item.repository.casefold() == "dt-ucph/cuc"
    )
    policy = GitHubTaskPolicy(
        allowed_fork_write_operations=permissions,
        allowed_upstream_operation_ids=upstream,
    )
    return GitHubEffectGateway(
        policy,
        adapter,
        approval_authority=authority or HumanApprovalAuthority(),
    ), adapter


class ScriptedPorts:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.implementation_count = 0
        self.test_failures_remaining = 0
        self.stale_test_evidence = False
        self.block_research = False
        self.red_mode = "valid"
        self.github_requests: tuple[GitHubEffectRequest, ...] = ()
        self.implementation_cost = 1.0

    def research(self, task: TaskSpec, provenance_refs: tuple[str, ...]):
        self.calls.append("research")
        if self.block_research:
            raise RuntimeError("dependency unavailable")
        return ResearchArtifact("research-1", f"researched {task.task_id}", provenance_refs)

    def plan(self, state):
        self.calls.append("plan")
        assert state.research is not None
        return PlanArtifact("plan-1", "planned", ("write RED", "implement", "verify"))

    def declare_tests(self, state):
        self.calls.append("declare-tests")
        assert state.plan is not None
        return (_targeted(), _regression())

    def run_red(self, intent: TestIntent, baseline_sha: str):
        self.calls.append(f"red:{intent.intent_id}")
        runtime = _runtime()
        if self.red_mode == "green":
            return runtime.RedGateEvidence(
                intent.intent_id,
                baseline_sha,
                GateOutcome.SUCCESS,
                0,
                1,
                0,
                "unexpectedly green baseline",
                ("ci:red",),
            )
        if self.red_mode == "wrong-base":
            baseline_sha = "d" * 40
        outcome = GateOutcome.REGRESSION if self.red_mode == "regression" else GateOutcome.TEST_FAILURE
        return runtime.RedGateEvidence(
            intent.intent_id,
            baseline_sha,
            outcome,
            1,
            10,
            1,
            "expected targeted failure",
            ("ci:red",),
        )

    def implement(self, state, attempt: int):
        self.calls.append(f"implement:{attempt}")
        self.implementation_count += 1
        runtime = _runtime()
        head = HEAD1 if attempt == 1 else HEAD2
        requests = tuple(
            replace(request, operation_id=f"{request.operation_id}-r{attempt}")
            if attempt > 1
            else request
            for request in self.github_requests
        )
        change = ChangeSet(
            f"change-{attempt}",
            f"implementation {attempt}",
            ("agent/harness/example.py",),
            tuple(item.operation_id for item in requests),
        )
        return runtime.ImplementationResult(
            change,
            head,
            requests,
            self.implementation_cost,
            (f"implementation:{attempt}",),
        )

    def run_test(self, intent: TestIntent, change: ChangeSet, head_sha: str):
        self.calls.append(f"test:{intent.intent_id}:{change.change_id}")
        result_head = "e" * 40 if self.stale_test_evidence else head_sha
        if self.test_failures_remaining:
            self.test_failures_remaining -= 1
            return TestResult(
                intent.intent_id,
                change.change_id,
                GateOutcome.TEST_FAILURE,
                result_head,
                result_head,
                1,
                10,
                1,
                "candidate test failed",
                ("ci:test",),
            )
        return TestResult(
            intent.intent_id,
            change.change_id,
            GateOutcome.SUCCESS,
            result_head,
            result_head,
            0,
            11,
            0,
            "candidate test passed",
            ("ci:test",),
        )

    def run_evals(self, state, change: ChangeSet, head_sha: str):
        self.calls.append(f"eval:{change.change_id}")
        return (
            EvalResult(
                "eval-1",
                change.change_id,
                GateOutcome.SUCCESS,
                head_sha,
                head_sha,
                "evaluation passed",
                (("score", 1.0),),
                ("eval:fixture",),
            ),
        )

    def final_diff(self, base_sha: str, head_sha: str):
        self.calls.append("diff")
        return f"diff --git a/example b/example\n# {base_sha}..{head_sha}\n"


class ScriptedReviewer:
    def __init__(self, *, rejections: int = 0) -> None:
        self.rejections = rejections
        self.contexts = []

    def review(self, context):
        self.contexts.append(context)
        if self.rejections:
            self.rejections -= 1
            finding = DevelopmentReviewFinding(
                "finding-1",
                DevelopmentFindingCategory.INVARIANT_RISK,
                FindingSeverity.MAJOR,
                "revision required",
                ("review:fixture",),
                "agent/harness/example.py",
                True,
            )
            return DevelopmentReviewReport.create(
                review_id=f"review-{len(self.contexts)}",
                reviewer_id="reviewer",
                review_context_id=context.review_context_id,
                inspected_sha=context.head_sha,
                disposition=ReviewDisposition.REQUEST_CHANGES,
                summary="request changes",
                findings=(finding,),
            )
        return DevelopmentReviewReport.create(
            review_id=f"review-{len(self.contexts)}",
            reviewer_id="reviewer",
            review_context_id=context.review_context_id,
            inspected_sha=context.head_sha,
            disposition=ReviewDisposition.APPROVE,
            summary="approved",
            findings=(),
        )


def _policy(**overrides):
    runtime = _runtime()
    values = dict(
        max_steps=60,
        max_revision_attempts=3,
        max_verification_executions=10,
        max_review_attempts=3,
        max_github_writes=4,
        max_cost_units=20.0,
        require_red=True,
        allow_no_feature_fallback=False,
    )
    values.update(overrides)
    return runtime.DevelopmentControllerPolicy(**values)


def _controller(
    ports: ScriptedPorts,
    reviewer: ScriptedReviewer | None = None,
    *,
    policy=None,
    gateway=None,
    persist=None,
):
    runtime = _runtime()
    reviewer = reviewer or ScriptedReviewer()
    if gateway is None:
        gateway, _ = _gateway(ports.github_requests)
    controller_ports = runtime.DevelopmentControllerPorts(
        research=ports.research,
        plan=ports.plan,
        declare_tests=ports.declare_tests,
        run_red=ports.run_red,
        implement=ports.implement,
        run_test=ports.run_test,
        run_evals=ports.run_evals,
        final_diff=ports.final_diff,
    )
    return runtime.BoundedDevelopmentController(
        policy=policy or _policy(),
        ports=controller_ports,
        reviewer=IndependentReviewer("reviewer", reviewer.review, implementer_id="implementer"),
        github_gateway=gateway,
        persist=persist or (lambda _payload: None),
        policy_refs=("AGENTS.md", "docs/agent-harness/architecture.md"),
        review_rubric=("termination", "issue fidelity", "side-effect safety"),
        implementer_id="implementer",
    )


def _start(controller):
    return controller.start(
        run_id="run-11",
        task=_task(),
        base_sha=BASE,
        provenance_refs=("issue:11", "finding:fixture"),
    )


def test_success_path_runs_real_red_fresh_verification_and_clean_review():
    ports = ScriptedPorts()
    reviewer = ScriptedReviewer()
    persisted: list[dict[str, object]] = []
    controller = _controller(ports, reviewer, persist=persisted.append)

    final = controller.run_until_stop(_start(controller))

    assert final.core.phase is RunPhase.COMPLETE
    assert final.stop_code is _runtime().ControllerStopCode.COMPLETE
    assert final.revision_attempts == 1
    assert final.review_attempts == 1
    assert final.red_gate_complete
    assert final.github_journal.receipts == ()
    assert reviewer.contexts and reviewer.contexts[0].base_sha == BASE
    assert reviewer.contexts[0].head_sha == HEAD1
    assert reviewer.contexts[0].executed_sha == HEAD1
    assert "red:targeted" in ports.calls
    assert ports.calls.index("red:targeted") < ports.calls.index("implement:1")
    assert persisted


def test_implementation_is_blocked_until_real_targeted_red_is_recorded():
    ports = ScriptedPorts()
    controller = _controller(ports)
    state = _start(controller)
    state = controller.step(state)  # research
    state = controller.step(state)  # plan
    state = controller.step(state)  # test design
    assert state.core.phase is RunPhase.IMPLEMENT

    state = controller.step(state)  # RED, not implementation
    assert ports.implementation_count == 0
    assert state.red_gate_complete
    assert state.red_evidence[0].baseline_sha == BASE

    for _ in range(2):
        state = controller.step(state)
        if ports.implementation_count:
            break
    assert ports.implementation_count == 1


@pytest.mark.parametrize("mode", ["green", "wrong-base", "regression"])
def test_invalid_red_evidence_fails_closed(mode: str):
    ports = ScriptedPorts()
    ports.red_mode = mode
    controller = _controller(ports)
    state = _start(controller)
    for _ in range(3):
        state = controller.step(state)
    with pytest.raises(ValueError, match="RED|baseline|failure|regression"):
        controller.step(state)
    assert ports.implementation_count == 0


def test_candidate_test_failure_routes_through_bounded_revision():
    ports = ScriptedPorts()
    ports.test_failures_remaining = 1
    controller = _controller(ports)

    final = controller.run_until_stop(_start(controller))

    assert final.core.phase is RunPhase.COMPLETE
    assert ports.implementation_count == 2
    assert final.revision_attempts == 2
    assert final.current_head_sha == HEAD2


def test_reviewer_rejection_routes_through_bounded_revision():
    ports = ScriptedPorts()
    reviewer = ScriptedReviewer(rejections=1)
    controller = _controller(ports, reviewer)

    final = controller.run_until_stop(_start(controller))

    assert final.core.phase is RunPhase.COMPLETE
    assert ports.implementation_count == 2
    assert final.review_attempts == 2
    assert len(reviewer.contexts) == 2


def test_stale_candidate_test_revision_is_rejected():
    ports = ScriptedPorts()
    ports.stale_test_evidence = True
    controller = _controller(ports)
    with pytest.raises(ValueError, match="head|executed|revision|stale"):
        controller.run_until_stop(_start(controller))


def test_blocked_research_has_explicit_terminal_reason():
    ports = ScriptedPorts()
    ports.block_research = True
    controller = _controller(ports)

    final = controller.run_until_stop(_start(controller))

    assert final.core.phase is RunPhase.BLOCKED
    assert final.stop_code is _runtime().ControllerStopCode.BLOCKED_EXECUTION
    assert "dependency unavailable" in final.stop_reason


def test_revision_and_total_step_budgets_terminate_explicitly():
    ports = ScriptedPorts()
    controller = _controller(ports, policy=_policy(max_revision_attempts=0))
    state = controller.run_until_stop(_start(controller))
    assert state.stop_code is _runtime().ControllerStopCode.REVISION_BUDGET_EXHAUSTED

    ports2 = ScriptedPorts()
    controller2 = _controller(ports2, policy=_policy(max_steps=2))
    state2 = controller2.run_until_stop(_start(controller2))
    assert state2.stop_code is _runtime().ControllerStopCode.STEP_BUDGET_EXHAUSTED


def test_cost_budget_stops_before_unbounded_revision_work():
    ports = ScriptedPorts()
    ports.implementation_cost = 5.0
    controller = _controller(ports, policy=_policy(max_cost_units=1.0))
    final = controller.run_until_stop(_start(controller))
    assert final.stop_code is _runtime().ControllerStopCode.COST_BUDGET_EXHAUSTED
    assert final.core.phase is RunPhase.BLOCKED


def test_fallback_policy_is_explicit_and_closed():
    policy = _policy(allow_no_feature_fallback=True)
    assert policy.fallback_allowed("performance")
    assert policy.fallback_allowed("stability")
    assert policy.fallback_allowed("ergonomics")
    assert policy.fallback_allowed("documentation")
    assert policy.fallback_allowed("edge-cases")
    assert not policy.fallback_allowed("new-feature")
    assert not _policy(allow_no_feature_fallback=False).fallback_allowed("performance")


def test_controller_state_round_trip_preserves_red_provenance_journal_and_counters():
    ports = ScriptedPorts()
    controller = _controller(ports)
    state = _start(controller)
    for _ in range(5):
        state = controller.step(state)
    runtime = _runtime()
    restored = runtime.DevelopmentControllerState.from_dict(state.to_dict())
    assert restored == state
    assert restored.provenance_refs == ("issue:11", "finding:fixture")
    assert restored.red_evidence == state.red_evidence
    assert restored.github_journal == state.github_journal
    assert restored.steps_used == state.steps_used


def test_runtime_uses_canonical_gateway_only_and_stays_framework_neutral():
    runtime = _runtime()
    source = inspect.getsource(runtime)
    assert "github_side_effects" not in source
    assert "langgraph" not in source.lower()
    assert "deepagents" not in source.lower()
    assert "HumanApprovalAuthority" not in source or ".register(" not in source
