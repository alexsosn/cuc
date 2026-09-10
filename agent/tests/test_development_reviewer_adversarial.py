from __future__ import annotations

import inspect

import pytest

from harness import contracts
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
    RunState,
    TaskSpec,
    TestIntent as HarnessTestIntent,
    TestKind as HarnessTestKind,
    TestResult as HarnessTestResult,
)
from harness.development_reviewer import (
    IndependentReviewer,
    apply_development_review,
    build_development_review_context,
    run_independent_development_review,
)
from harness.state_machine import (
    ChangeRecorded,
    EvalRecorded,
    PlanRecorded,
    ResearchRecorded,
    TestsDeclared as HarnessTestsDeclared,
    TestRecorded as HarnessTestRecorded,
    VerificationPassed,
    apply_event,
)


HEAD = "1" * 40
EXECUTED = "2" * 40
BASE = "3" * 40


def _review_ready_state() -> RunState:
    state = RunState(
        "dev-run-adversarial",
        TaskSpec(
            "HARN-006",
            "Independent development review",
            "Reject context-correlation bypasses",
            ("clean context", "structured findings", "bound review application"),
        ),
    )
    state = apply_event(
        state,
        ResearchRecorded(ResearchArtifact("research", "implementation framing", ("issue:7",))),
    )
    state = apply_event(
        state,
        PlanRecorded(PlanArtifact("plan", "implementation rationale", ("test", "fix"), ("issue:7",))),
    )
    state = apply_event(
        state,
        HarnessTestsDeclared(
            (
                HarnessTestIntent(
                    "targeted",
                    HarnessTestKind.TARGETED,
                    ("python", "-m", "pytest", "tests/test_development_reviewer.py"),
                    "agent",
                    "targeted",
                ),
                HarnessTestIntent(
                    "regression",
                    HarnessTestKind.REGRESSION,
                    ("python", "-m", "pytest", "-q"),
                    "agent",
                    "regression",
                ),
            )
        ),
    )
    state = apply_event(
        state,
        ChangeRecorded(ChangeSet("change", "implementation story", ("agent/example.py",))),
    )
    for intent_id in ("targeted", "regression"):
        state = apply_event(
            state,
            HarnessTestRecorded(
                HarnessTestResult(
                    intent_id,
                    "change",
                    GateOutcome.SUCCESS,
                    HEAD,
                    EXECUTED,
                    0,
                    3,
                    0,
                    "green",
                    (f"ci:{intent_id}",),
                )
            ),
        )
    state = apply_event(
        state,
        EvalRecorded(
            EvalResult(
                "eval",
                "change",
                GateOutcome.SUCCESS,
                HEAD,
                EXECUTED,
                "green",
                (("regressions", 0),),
                ("eval:1",),
            )
        ),
    )
    return apply_event(state, VerificationPassed())


def _context():
    return build_development_review_context(
        _review_ready_state(),
        base_sha=BASE,
        head_sha=HEAD,
        final_diff="diff --git a/agent/example.py b/agent/example.py\n+value = 1\n",
        policy_refs=("AGENTS.md",),
        rubric=("attack context leakage",),
    )


def _approve(context, *, review_id: str = "independent-review") -> ReviewResult:
    return ReviewResult(
        review_id,
        "reviewer-b",
        context.review_context_id,
        context.head_sha,
        ReviewDisposition.APPROVE,
        "approved",
        (),
    )


def test_reviewer_revalidates_context_before_exposing_it_to_adapter() -> None:
    context = _context()
    object.__setattr__(
        context,
        "final_diff",
        "diff --git a/x.py b/x.py\n+subprocess.run(user_input, shell=True)\n",
    )
    called = False

    def reviewer(packet):
        nonlocal called
        called = True
        return _approve(packet)

    with pytest.raises(ValueError, match="context|diff|digest|identity"):
        run_independent_development_review(
            context,
            IndependentReviewer("reviewer-b", reviewer),
        )
    assert not called


def test_reviewer_revalidates_returned_review_contract_after_adapter_boundary() -> None:
    context = _context()

    def reviewer(packet):
        result = _approve(packet)
        blocker = ReviewFinding(
            "late-blocker",
            FindingSeverity.CRITICAL,
            "blocking finding injected after construction",
            ("diff:x.py",),
            True,
        )
        object.__setattr__(result, "findings", (blocker,))
        return result

    with pytest.raises(ValueError, match="approved|blocking|review"):
        run_independent_development_review(
            context,
            IndependentReviewer("reviewer-b", reviewer),
        )


def test_apply_requires_the_exact_clean_context_and_revalidates_binding() -> None:
    context = _context()
    state = _review_ready_state()
    result = _approve(context)

    parameters = inspect.signature(apply_development_review).parameters
    assert tuple(parameters) == ("state", "context", "result")
    completed = apply_development_review(state, context, result)
    assert completed.review == result

    other = build_development_review_context(
        state,
        base_sha=BASE,
        head_sha=HEAD,
        final_diff=context.final_diff + "+other = 2\n",
        policy_refs=context.policy_refs,
        rubric=context.rubric,
    )
    with pytest.raises(ValueError, match="context|binding"):
        apply_development_review(state, other, result)


def test_review_findings_have_structured_category_and_location() -> None:
    FindingCategory = getattr(contracts, "FindingCategory", None)
    assert FindingCategory is not None, "HARN-006 requires structured finding categories"

    cases = (
        (FindingCategory.MISSING_TEST, "agent/tests/test_x.py"),
        (FindingCategory.INVARIANT_RISK, "agent/harness/state_machine.py:review"),
        (FindingCategory.REGRESSION_RISK, "agent/harness/contracts.py:ReviewResult"),
    )
    for index, (category, location) in enumerate(cases):
        finding = ReviewFinding(
            f"finding-{index}",
            FindingSeverity.MAJOR,
            "structured risk",
            ("review:evidence",),
            False,
            category=category,
            location=location,
        )
        payload = finding.to_dict()
        assert payload["category"] == category.value
        assert payload["location"] == location
        restored = ReviewFinding.from_dict(payload)
        assert restored.category is category
        assert restored.location == location


def test_old_review_finding_payload_remains_backward_compatible() -> None:
    payload = {
        "finding_id": "legacy-finding",
        "severity": "minor",
        "summary": "legacy payload",
        "evidence_refs": ["legacy:evidence"],
        "blocking": False,
    }
    restored = ReviewFinding.from_dict(payload)
    assert restored.finding_id == "legacy-finding"
    assert restored.location is None
    assert restored.category.value == "general"


def test_nested_context_deserialization_rejects_scalar_collection_smuggling() -> None:
    context = _context()
    payload = context.to_dict()
    payload["task"] = dict(payload["task"])
    payload["task"]["acceptance_criteria"] = "not-an-array"
    with pytest.raises(ValueError, match="acceptance|iterable|array"):
        type(context).from_dict(payload)

    payload = context.to_dict()
    payload["test_evidence"] = "not-an-array"
    with pytest.raises(ValueError, match="test_evidence|iterable|array"):
        type(context).from_dict(payload)
