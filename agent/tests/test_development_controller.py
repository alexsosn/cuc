from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from harness.contracts import (
    ChangeSet,
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
from harness.development_controller import (
    BoundedDevelopmentController,
    ControllerStopCode,
    DevelopmentControllerPolicy,
    DevelopmentControllerPorts,
    DevelopmentControllerState,
    ImplementationResult,
    RedGateEvidence,
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
    HumanApproval,
    HumanApprovalAuthority,
)


BASE = "a" * 40
HEAD1 = "b" * 40
HEAD2 = "c" * 40
FORK = "alexsosn/cuc"
UPSTREAM = "DT-UCPH/cuc"


class FakeGitHubAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.results: dict[str, str] = {}

    def execute(self, request):
        self.calls.append((request.action.value, request.operation_id))
        result = f"{request.action.value}:{request.operation_id}"
        self.results[request.operation_id] = result
        return result


def task() -> TaskSpec:
    return TaskSpec(
        "issue-11",
        "bounded controller",
        "implement the bounded development controller",
        ("real RED before implementation", "fresh verification", "independent review"),
    )


def targeted() -> TestIntent:
    return TestIntent(
        "targeted",
        TestKind.TARGETED,
        ("python", "-m", "pytest", "tests/test_feature.py"),
        "agent",
        "new behavior fails before implementation",
    )


def regression() -> TestIntent:
    return TestIntent(
        "regression",
        TestKind.REGRESSION,
        ("python", "-m", "pytest", "-q"),
        "agent",
        "full regression suite",
    )


def approval_for(request: GitHubEffectRequest, approval_id="human-1") -> HumanApproval:
    return HumanApproval(
        approval_id,
        "human-user",
        request.operation_id,
        request.request_sha256,
    )


def gateway_for(operations=()):
    operations = tuple(operations)
    fork_permissions = tuple(
        GitHubOperationPermission(item.operation_id, item.action)
        for item in operations
        if item.repository.casefold() == FORK.casefold()
    )
    upstream_ids = tuple(
        item.operation_id
        for item in operations
        if item.repository.casefold() == UPSTREAM.casefold()
    )
    policy = GitHubTaskPolicy(
        allowed_fork_write_operations=fork_permissions,
        allowed_upstream_operation_ids=upstream_ids,
    )
    adapter = FakeGitHubAdapter()
    authority = HumanApprovalAuthority()
    gateway = GitHubEffectGateway(policy, adapter, approval_authority=authority)
    return gateway, adapter, authority


class ScriptedPorts:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.implementation_count = 0
        self.test_failures_remaining = 0
        self.review_rejections_remaining = 0
        self.review_escalations_remaining = 0
        self.github_operations: tuple[GitHubEffectRequest, ...] = ()
        self.block_research = False
        self.stale_verification = False

    def research(self, _task, provenance_refs):
        self.calls.append("research")
        if self.block_research:
            raise RuntimeError("dependency unavailable")
        return ResearchArtifact("research-1", "researched", tuple(provenance_refs))

    def plan(self, state):
        self.calls.append("plan")
        assert state.research is not None
        return PlanArtifact("plan-1", "planned", ("write failing test", "implement"))

    def declare_tests(self, state):
        self.calls.append("declare-tests")
        assert state.plan is not None
        return (targeted(), regression())

    def run_red(self, intent, baseline_sha):
        self.calls.append(f"red:{intent.intent_id}")
        assert baseline_sha == BASE
        return RedGateEvidence(
            intent.intent_id,
            baseline_sha,
            GateOutcome.TEST_FAILURE,
            1,
            0,
            1,
            "expected baseline failure",
            ("ci:red",),
        )

    def implement(self, state, attempt):
        self.calls.append(f"implement:{attempt}")
        self.implementation_count += 1
        head = HEAD1 if self.implementation_count == 1 else HEAD2
        operations = self.github_operations if self.implementation_count == 1 else ()
        return ImplementationResult(
            ChangeSet(
                f"change-{self.implementation_count}",
                "implementation",
                ("agent/harness/example.py",),
                tuple(item.operation_id for item in operations),
            ),
            head,
            operations,
            1.0,
            (f"commit:{head}",),
        )

    def run_test(self, intent, change, head_sha):
        self.calls.append(f"verify:{intent.intent_id}:{change.change_id}")
        if self.test_failures_remaining:
            self.test_failures_remaining -= 1
            return TestResult(
                intent.intent_id,
                change.change_id,
                GateOutcome.TEST_FAILURE,
                head_sha,
                head_sha,
                1,
                0,
                1,
                "still failing",
                ("ci:fail",),
            )
        executed = "d" * 40 if self.stale_verification else head_sha
        return TestResult(
            intent.intent_id,
            change.change_id,
            GateOutcome.SUCCESS,
            head_sha,
            executed,
            0,
            1,
            0,
            "green",
            ("ci:green",),
        )

    def run_evals(self, state, change, head_sha):
        self.calls.append(f"evals:{change.change_id}")
        return ()

    def final_diff(self, base_sha, head_sha):
        self.calls.append(f"diff:{head_sha}")
        return f"diff --git a/x b/x\n+{head_sha}\n"

    def reviewer(self):
        def review(context):
            self.calls.append(f"review:{context.head_sha}")
            if self.review_escalations_remaining:
                self.review_escalations_remaining -= 1
                return DevelopmentReviewReport.create(
                    review_id="review-escalate",
                    reviewer_id="clean-reviewer",
                    review_context_id=context.review_context_id,
                    inspected_sha=context.head_sha,
                    disposition=ReviewDisposition.ESCALATE,
                    summary="human decision required",
                    findings=(),
                )
            if self.review_rejections_remaining:
                self.review_rejections_remaining -= 1
                finding = DevelopmentReviewFinding(
                    "blocker-1",
                    DevelopmentFindingCategory.INVARIANT_RISK,
                    FindingSeverity.MAJOR,
                    "fix required",
                    ("diff",),
                    "agent/harness/example.py",
                    True,
                )
                return DevelopmentReviewReport.create(
                    review_id="review-reject",
                    reviewer_id="clean-reviewer",
                    review_context_id=context.review_context_id,
                    inspected_sha=context.head_sha,
                    disposition=ReviewDisposition.REQUEST_CHANGES,
                    summary="blocking issue",
                    findings=(finding,),
                )
            return DevelopmentReviewReport.create(
                review_id=f"review-{context.head_sha}",
                reviewer_id="clean-reviewer",
                review_context_id=context.review_context_id,
                inspected_sha=context.head_sha,
                disposition=ReviewDisposition.APPROVE,
                summary="approved",
                findings=(),
            )

        return IndependentReviewer(
            "clean-reviewer",
            review,
            implementer_id="implementer",
        )


def make_controller(
    scripted: ScriptedPorts,
    *,
    policy=None,
    gateway=None,
    persist=None,
):
    ports = DevelopmentControllerPorts(
        research=scripted.research,
        plan=scripted.plan,
        declare_tests=scripted.declare_tests,
        run_red=scripted.run_red,
        implement=scripted.implement,
        run_test=scripted.run_test,
        run_evals=scripted.run_evals,
        final_diff=scripted.final_diff,
    )
    if gateway is None:
        gateway, _, _ = gateway_for(scripted.github_operations)
    snapshots = [] if persist is None else persist
    controller = BoundedDevelopmentController(
        policy=policy
        or DevelopmentControllerPolicy(
            max_revision_attempts=3,
            max_verification_executions=20,
            max_review_attempts=3,
            max_github_writes=5,
            max_cost_units=10.0,
            production_mode=True,
        ),
        ports=ports,
        reviewer=scripted.reviewer(),
        github_gateway=gateway,
        persist=snapshots.append,
        policy_refs=("AGENTS.md", "HARN-023", "HARN-009"),
        review_rubric=("correctness", "tests", "side-effect safety"),
        implementer_id="implementer",
    )
    return controller, snapshots


def start(controller):
    return controller.start(
        run_id="run-11",
        task=task(),
        base_sha=BASE,
        provenance_refs=("github:issue:11",),
    )


def test_happy_path_requires_real_red_before_implementation_and_completes() -> None:
    scripted = ScriptedPorts()
    controller, snapshots = make_controller(scripted)
    state = controller.run_until_stop(start(controller))

    assert state.core.phase is RunPhase.COMPLETE
    assert state.stop_code is ControllerStopCode.COMPLETE
    assert state.stop_reason
    assert state.revision_attempts == 1
    assert state.review_attempts == 1
    assert state.red_gate_complete
    assert scripted.calls.index("red:targeted") < scripted.calls.index("implement:1")
    assert scripted.calls.index("implement:1") < scripted.calls.index("review:" + HEAD1)
    assert snapshots


def test_first_implementation_is_impossible_until_targeted_red_is_real_failure() -> None:
    scripted = ScriptedPorts()

    def false_red(intent, baseline_sha):
        scripted.calls.append("false-red")
        return RedGateEvidence(
            intent.intent_id,
            baseline_sha,
            GateOutcome.SUCCESS,
            0,
            1,
            0,
            "model says it is fine",
            (),
        )

    controller, _ = make_controller(scripted)
    controller.ports = replace(controller.ports, run_red=false_red)
    state = start(controller)
    state = controller.step(state)
    state = controller.step(state)
    state = controller.step(state)

    with pytest.raises(ValueError, match="RED|failure|targeted"):
        controller.step(state)
    assert not any(call.startswith("implement:") for call in scripted.calls)


def test_red_evidence_from_wrong_baseline_cannot_unlock_implementation() -> None:
    scripted = ScriptedPorts()

    def stale_red(intent, _baseline_sha):
        return RedGateEvidence(
            intent.intent_id,
            "f" * 40,
            GateOutcome.TEST_FAILURE,
            1,
            0,
            1,
            "stale branch",
            (),
        )

    controller, _ = make_controller(scripted)
    controller.ports = replace(controller.ports, run_red=stale_red)
    state = start(controller)
    for _ in range(3):
        state = controller.step(state)
    with pytest.raises(ValueError, match="baseline|revision|SHA"):
        controller.step(state)
    assert scripted.implementation_count == 0


def test_repeated_test_failure_stops_at_revision_budget_without_unbounded_loop() -> None:
    scripted = ScriptedPorts()
    scripted.test_failures_remaining = 20
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=2,
        max_verification_executions=20,
        max_review_attempts=3,
        max_github_writes=5,
        max_cost_units=10.0,
        production_mode=True,
    )
    controller, _ = make_controller(scripted, policy=policy)
    state = controller.run_until_stop(start(controller))

    assert state.core.phase is RunPhase.BLOCKED
    assert state.stop_code is ControllerStopCode.REVISION_BUDGET_EXHAUSTED
    assert state.revision_attempts == 2
    assert scripted.implementation_count == 2


def test_reviewer_rejection_causes_fresh_bounded_revision_and_review() -> None:
    scripted = ScriptedPorts()
    scripted.review_rejections_remaining = 1
    controller, _ = make_controller(scripted)
    state = controller.run_until_stop(start(controller))

    assert state.core.phase is RunPhase.COMPLETE
    assert state.revision_attempts == 2
    assert state.review_attempts == 2
    assert "review:" + HEAD1 in scripted.calls
    assert "review:" + HEAD2 in scripted.calls


def test_reviewer_escalation_pauses_and_human_resume_retries_review_boundedly() -> None:
    scripted = ScriptedPorts()
    scripted.review_escalations_remaining = 1
    controller, _ = make_controller(scripted)
    paused = controller.run_until_stop(start(controller))

    assert paused.core.phase is RunPhase.AWAITING_HUMAN
    assert paused.stop_code is ControllerStopCode.NEEDS_HUMAN
    assert paused.review_attempts == 1

    resumed = controller.resume(paused)
    finished = controller.run_until_stop(resumed)
    assert finished.core.phase is RunPhase.COMPLETE
    assert finished.review_attempts == 2


def test_blocked_dependency_has_explicit_terminal_reason() -> None:
    scripted = ScriptedPorts()
    scripted.block_research = True
    controller, _ = make_controller(scripted)
    state = controller.run_until_stop(start(controller))

    assert state.core.phase is RunPhase.BLOCKED
    assert state.stop_code is ControllerStopCode.BLOCKED_EXECUTION
    assert "dependency unavailable" in state.stop_reason


def test_stale_executed_revision_is_explicit_policy_block_not_review_success() -> None:
    scripted = ScriptedPorts()
    scripted.stale_verification = True
    controller, _ = make_controller(scripted)
    state = controller.run_until_stop(start(controller))

    assert state.core.phase is RunPhase.BLOCKED
    assert state.stop_code is ControllerStopCode.POLICY_BLOCK
    assert "revision" in state.stop_reason.casefold() or "sha" in state.stop_reason.casefold()
    assert not any(call.startswith("review:") for call in scripted.calls)


def test_verification_budget_blocks_before_over_budget_test_port_call() -> None:
    scripted = ScriptedPorts()
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=3,
        max_verification_executions=1,
        max_review_attempts=3,
        max_github_writes=5,
        max_cost_units=10.0,
        production_mode=True,
    )
    controller, _ = make_controller(scripted, policy=policy)
    state = controller.run_until_stop(start(controller))

    assert state.stop_code is ControllerStopCode.VERIFICATION_BUDGET_EXHAUSTED
    verify_calls = [call for call in scripted.calls if call.startswith("verify:")]
    assert len(verify_calls) == 1


def test_review_budget_blocks_before_extra_reviewer_call() -> None:
    scripted = ScriptedPorts()
    scripted.review_rejections_remaining = 2
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=3,
        max_verification_executions=20,
        max_review_attempts=1,
        max_github_writes=5,
        max_cost_units=10.0,
        production_mode=True,
    )
    controller, _ = make_controller(scripted, policy=policy)
    state = controller.run_until_stop(start(controller))

    assert state.stop_code is ControllerStopCode.REVIEW_BUDGET_EXHAUSTED
    assert state.review_attempts == 1


def test_cost_budget_blocks_before_second_implementation() -> None:
    scripted = ScriptedPorts()
    scripted.review_rejections_remaining = 1
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=3,
        max_verification_executions=20,
        max_review_attempts=3,
        max_github_writes=5,
        max_cost_units=1.5,
        production_mode=True,
    )
    controller, _ = make_controller(scripted, policy=policy)
    state = controller.run_until_stop(start(controller))

    assert state.stop_code is ControllerStopCode.COST_BUDGET_EXHAUSTED
    assert scripted.implementation_count == 1
    assert state.cost_units == 1.0


def test_approval_interrupt_persists_pending_implementation_and_resumes_same_operation() -> None:
    scripted = ScriptedPorts()
    request = GitHubEffectRequest(
        "upstream-op-1",
        UPSTREAM,
        GitHubAction.CREATE_ISSUE,
        {"title": "requires human"},
    )
    scripted.github_operations = (request,)
    gateway, adapter, authority = gateway_for(scripted.github_operations)
    controller, _ = make_controller(scripted, gateway=gateway)
    state = start(controller)

    while state.core.phase is not RunPhase.AWAITING_HUMAN:
        state = controller.step(state)

    assert state.stop_code is ControllerStopCode.NEEDS_HUMAN
    assert state.pending_implementation is not None
    assert state.pending_operation_id == request.operation_id
    assert scripted.implementation_count == 1
    assert adapter.calls == []

    approval = approval_for(request)
    authority.register(approval)
    resumed = controller.resume(state, approval=approval)
    finished = controller.run_until_stop(resumed)

    assert finished.core.phase is RunPhase.COMPLETE
    assert scripted.implementation_count == 1
    assert adapter.calls == [(GitHubAction.CREATE_ISSUE.value, request.operation_id)]


def test_github_write_budget_blocks_before_provider_write() -> None:
    scripted = ScriptedPorts()
    scripted.github_operations = (
        GitHubEffectRequest(
            "fork-write-1",
            FORK,
            GitHubAction.CREATE_ISSUE,
            {"title": "one"},
        ),
    )
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=3,
        max_verification_executions=20,
        max_review_attempts=3,
        max_github_writes=0,
        max_cost_units=10.0,
        production_mode=True,
    )
    gateway, adapter, _ = gateway_for(scripted.github_operations)
    controller, _ = make_controller(scripted, policy=policy, gateway=gateway)
    state = controller.run_until_stop(start(controller))

    assert state.stop_code is ControllerStopCode.GITHUB_WRITE_BUDGET_EXHAUSTED
    assert adapter.calls == []


def test_serialized_restart_does_not_repeat_research_plan_or_red() -> None:
    scripted = ScriptedPorts()
    controller, _ = make_controller(scripted)
    state = start(controller)
    for _ in range(4):
        state = controller.step(state)
    assert state.red_gate_complete
    assert scripted.implementation_count == 0

    restored = DevelopmentControllerState.from_dict(state.to_dict())
    before = list(scripted.calls)
    finished = controller.run_until_stop(restored)

    assert finished.core.phase is RunPhase.COMPLETE
    assert scripted.calls.count("research") == 1
    assert scripted.calls.count("plan") == 1
    assert scripted.calls.count("declare-tests") == 1
    assert scripted.calls.count("red:targeted") == 1
    assert scripted.calls[: len(before)] == before


def test_controller_uses_only_harn023_canonical_gateway() -> None:
    import harness.development_controller as module

    source = inspect.getsource(module)
    assert "github_side_effects" not in source
    assert "GuardedGitHubSideEffects" not in source
    assert "github_effects" in source
    assert "GitHubEffectGateway" in source


def test_state_round_trip_preserves_terminal_reason_provenance_and_journal() -> None:
    scripted = ScriptedPorts()
    scripted.block_research = True
    controller, _ = make_controller(scripted)
    blocked = controller.run_until_stop(start(controller))
    restored = DevelopmentControllerState.from_dict(blocked.to_dict())

    assert restored == blocked
    assert restored.stop_code is ControllerStopCode.BLOCKED_EXECUTION
    assert restored.provenance_refs == ("github:issue:11",)
    assert restored.github_journal == blocked.github_journal
    assert restored.stop_reason


def test_no_feature_fallback_is_disabled_by_default_and_category_allowlisted() -> None:
    policy = DevelopmentControllerPolicy(
        max_revision_attempts=1,
        max_verification_executions=1,
        max_review_attempts=1,
        max_github_writes=1,
        production_mode=False,
    )
    assert not policy.fallback_allowed("documentation")
    assert not policy.fallback_allowed("new-feature")

    enabled = replace(policy, allow_no_feature_fallback=True)
    for category in ("performance", "stability", "ergonomics", "documentation", "edge-cases"):
        assert enabled.fallback_allowed(category)
    assert not enabled.fallback_allowed("new-feature")
