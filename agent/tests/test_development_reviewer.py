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
    ReviewFinding,
    ReviewResult,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent,
    TestKind,
    TestResult,
)
from harness.state_machine import (
    ChangeRecorded,
    EvalRecorded,
    PlanRecorded,
    ResearchRecorded,
    ResumeRequested,
    ReviewRecorded,
    TestsDeclared,
    TestRecorded,
    VerificationPassed,
    apply_event,
)


HEAD = "a" * 40
EXECUTED = "b" * 40
BASE = "c" * 40
POLICIES = ("AGENTS.md", ".github/AGENT_SAFETY.md")
RUBRIC = (
    "attack stale-revision evidence",
    "check acceptance criteria against final diff",
    "look for missing regression tests",
)
FINAL_DIFF = "diff --git a/agent/example.py b/agent/example.py\n+safe = True\n"

RESEARCH_NARRATIVE = "IMPLEMENTER_CHAIN_OF_THOUGHT_RESEARCH"
PLAN_NARRATIVE = "IMPLEMENTER_SELF_JUSTIFICATION_PLAN"
CHANGE_NARRATIVE = "IMPLEMENTER_CHANGE_RATIONALE"
TEST_NARRATIVE = "IMPLEMENTER_TEST_RESULT_SPIN"
EVAL_NARRATIVE = "IMPLEMENTER_EVAL_RESULT_SPIN"
PRIOR_REVIEW_NARRATIVE = "PRIOR_REVIEW_PERSUASION"


def _runtime():
    try:
        return importlib.import_module("harness.development_reviewer")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-006 reviewer runtime is not implemented yet: {exc}")


def _review_ready_state() -> RunState:
    state = RunState(
        "development-run-1",
        TaskSpec(
            "HARN-006",
            "Independent development reviewer",
            "Build a clean-context reviewer",
            (
                "reviewer context is independent",
                "request changes routes back to implementation",
            ),
        ),
    )
    state = apply_event(
        state,
        ResearchRecorded(
            ResearchArtifact("research-1", RESEARCH_NARRATIVE, ("repo:contracts",))
        ),
    )
    state = apply_event(
        state,
        PlanRecorded(
            PlanArtifact(
                "plan-1",
                PLAN_NARRATIVE,
                ("write tests first", "implement minimal boundary"),
                ("repo:state-machine",),
            )
        ),
    )
    state = apply_event(
        state,
        TestsDeclared(
            (
                TestIntent(
                    "unit",
                    TestKind.TARGETED,
                    ("python", "-m", "pytest", "tests/test_development_reviewer.py"),
                    "agent",
                    "HARN-006 targeted tests",
                ),
                TestIntent(
                    "full",
                    TestKind.REGRESSION,
                    ("python", "-m", "pytest", "-q"),
                    "agent",
                    "full agent regression suite",
                ),
            )
        ),
    )
    state = apply_event(
        state,
        ChangeRecorded(
            ChangeSet(
                "change-1",
                CHANGE_NARRATIVE,
                ("agent/harness/development_reviewer.py", "agent/tests/test_development_reviewer.py"),
                ("op-write-1",),
            )
        ),
    )
    state = apply_event(
        state,
        TestRecorded(
            TestResult(
                "unit",
                "change-1",
                GateOutcome.SUCCESS,
                HEAD,
                EXECUTED,
                0,
                12,
                0,
                TEST_NARRATIVE,
                ("ci:unit",),
            )
        ),
    )
    state = apply_event(
        state,
        TestRecorded(
            TestResult(
                "full",
                "change-1",
                GateOutcome.SUCCESS,
                HEAD,
                EXECUTED,
                0,
                1187,
                0,
                TEST_NARRATIVE,
                ("ci:full",),
            )
        ),
    )
    state = apply_event(
        state,
        EvalRecorded(
            EvalResult(
                "safety-eval",
                "change-1",
                GateOutcome.SUCCESS,
                HEAD,
                EXECUTED,
                EVAL_NARRATIVE,
                (("unsafe_write_count", 0),),
                ("eval:safety",),
            )
        ),
    )
    return apply_event(state, VerificationPassed())


def _context(state: RunState | None = None, **overrides):
    runtime = _runtime()
    values = dict(
        base_sha=BASE,
        head_sha=HEAD,
        final_diff=FINAL_DIFF,
        policy_refs=POLICIES,
        rubric=RUBRIC,
    )
    values.update(overrides)
    return runtime.build_development_review_context(state or _review_ready_state(), **values)


def test_clean_context_is_deterministic_roundtrippable_and_reviewable() -> None:
    runtime = _runtime()
    context = _context()

    assert context.review_context_id.startswith("review-context-")
    assert context.head_sha == HEAD
    assert context.executed_sha == EXECUTED
    assert context.base_sha == BASE
    assert context.final_diff == FINAL_DIFF
    assert context.policy_refs == POLICIES
    assert context.rubric == RUBRIC
    assert context.task.acceptance_criteria == (
        "reviewer context is independent",
        "request changes routes back to implementation",
    )
    assert tuple(item.intent_id for item in context.test_evidence) == ("unit", "full")
    assert tuple(item.eval_id for item in context.eval_evidence) == ("safety-eval",)
    assert context.to_json() == context.to_json()
    assert runtime.DevelopmentReviewContext.from_json(context.to_json()) == context


def test_context_default_packet_excludes_implementer_and_prior_review_narrative() -> None:
    runtime = _runtime()
    state = _review_ready_state()
    escalated = ReviewResult(
        "prior-review",
        "reviewer-old",
        "prior-context",
        HEAD,
        ReviewDisposition.ESCALATE,
        PRIOR_REVIEW_NARRATIVE,
        (),
    )
    paused = apply_event(state, ReviewRecorded(escalated))
    resumed = apply_event(paused, ResumeRequested())
    context = _context(resumed)
    serialized = context.to_json()

    for forbidden in (
        RESEARCH_NARRATIVE,
        PLAN_NARRATIVE,
        CHANGE_NARRATIVE,
        TEST_NARRATIVE,
        EVAL_NARRATIVE,
        PRIOR_REVIEW_NARRATIVE,
    ):
        assert forbidden not in serialized

    parameters = inspect.signature(runtime.build_development_review_context).parameters
    assert "metadata" not in parameters
    assert "implementation_context" not in parameters
    assert not any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def test_context_identity_binds_diff_head_policy_and_rubric_and_cannot_be_forged() -> None:
    runtime = _runtime()
    original = _context()
    variants = (
        _context(final_diff=FINAL_DIFF + "+extra = True\n"),
        _context(policy_refs=POLICIES + ("CLAUDE.md",)),
        _context(rubric=RUBRIC + ("attack hidden coupling",)),
    )
    assert all(item.review_context_id != original.review_context_id for item in variants)

    payload = original.to_dict()
    payload["review_context_id"] = "review-context-" + "0" * 64
    with pytest.raises(ValueError, match="context|digest|identity"):
        runtime.DevelopmentReviewContext.from_dict(payload)

    payload = original.to_dict()
    payload["diff_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="diff|digest"):
        runtime.DevelopmentReviewContext.from_dict(payload)


def test_context_rejects_wrong_phase_and_stale_head() -> None:
    state = _review_ready_state()
    with pytest.raises(ValueError, match="review"):
        _context(replace(state, phase=RunPhase.COMPLETE))
    with pytest.raises(ValueError, match="head|verified|stale"):
        _context(head_sha="d" * 40)


def test_context_fails_closed_on_mixed_or_non_success_verification_evidence() -> None:
    state = _review_ready_state()
    mixed = replace(
        state,
        test_results=state.test_results
        + (
            TestResult(
                "unit",
                "change-1",
                GateOutcome.SUCCESS,
                HEAD,
                "e" * 40,
                0,
                1,
                0,
                "newer rerun",
                ("ci:rerun",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="executed|verification|mixed"):
        _context(mixed)

    failed = replace(
        state,
        test_results=state.test_results
        + (
            TestResult(
                "unit",
                "change-1",
                GateOutcome.TEST_FAILURE,
                HEAD,
                EXECUTED,
                1,
                11,
                1,
                "fresh failure",
                ("ci:failure",),
            ),
        ),
        verified_head_sha=HEAD,
    )
    with pytest.raises(ValueError, match="success|test|verification"):
        _context(failed)


def test_reviewer_receives_only_serializable_clean_context_and_result_is_bound() -> None:
    runtime = _runtime()
    context = _context()
    seen = []

    def review(packet):
        seen.append(packet)
        return ReviewResult(
            "review-1",
            "reviewer-clean",
            packet.review_context_id,
            packet.head_sha,
            ReviewDisposition.APPROVE,
            "No blockers",
            (),
        )

    binding = runtime.IndependentReviewer(
        reviewer_id="reviewer-clean",
        implementer_id="implementer-runner",
        review=review,
    )
    result = runtime.run_independent_development_review(context, binding)
    assert seen == [context]
    assert result.review_context_id == context.review_context_id
    assert result.inspected_sha == HEAD


def test_reviewer_identity_and_returned_context_head_are_fail_closed() -> None:
    runtime = _runtime()
    context = _context()

    with pytest.raises(ValueError, match="independent|implementer|reviewer"):
        runtime.IndependentReviewer("same-agent", lambda _: None, implementer_id="same-agent")

    def wrong(packet):
        return ReviewResult(
            "review-2",
            "other-reviewer",
            "wrong-context",
            "f" * 40,
            ReviewDisposition.APPROVE,
            "wrong binding",
            (),
        )

    binding = runtime.IndependentReviewer("reviewer-clean", wrong)
    with pytest.raises(ValueError, match="reviewer|context|head"):
        runtime.run_independent_development_review(context, binding)


def test_seeded_defect_becomes_structured_blocker_and_routes_via_harn002() -> None:
    runtime = _runtime()
    unsafe_diff = "diff --git a/x.py b/x.py\n+subprocess.run(user_input, shell=True)\n"
    context = _context(final_diff=unsafe_diff)

    def deterministic_reviewer(packet):
        findings = ()
        disposition = ReviewDisposition.APPROVE
        if "shell=True" in packet.final_diff:
            findings = (
                ReviewFinding(
                    "unsafe-shell",
                    FindingSeverity.CRITICAL,
                    "Untrusted input reaches a shell",
                    ("diff:x.py",),
                    True,
                ),
            )
            disposition = ReviewDisposition.REQUEST_CHANGES
        return ReviewResult(
            "review-defect",
            "reviewer-clean",
            packet.review_context_id,
            packet.head_sha,
            disposition,
            "Synthetic independent review",
            findings,
        )

    result = runtime.run_independent_development_review(
        context,
        runtime.IndependentReviewer("reviewer-clean", deterministic_reviewer),
    )
    assert result.disposition is ReviewDisposition.REQUEST_CHANGES
    assert result.findings[0].finding_id == "unsafe-shell"

    revised = runtime.apply_development_review(_review_ready_state(), result)
    assert revised.phase is RunPhase.IMPLEMENT
    assert revised.review == result
    assert revised.verified_head_sha is None


def test_approve_routes_via_existing_harn002_state_machine() -> None:
    runtime = _runtime()
    context = _context()
    result = ReviewResult(
        "review-ok",
        "reviewer-clean",
        context.review_context_id,
        context.head_sha,
        ReviewDisposition.APPROVE,
        "Approved",
        (),
    )
    completed = runtime.apply_development_review(_review_ready_state(), result)
    assert completed.phase is RunPhase.COMPLETE
    assert completed.review == result


def test_reviewer_core_is_framework_provider_and_scholarly_feedback_free() -> None:
    runtime = _runtime()
    source = open(runtime.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "langgraph",
        "langfuse",
        "openai",
        "anthropic",
        "parsing_evaluation",
        "expertfeedback",
        "columnrunstate",
    ):
        assert forbidden not in source
