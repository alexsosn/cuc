from __future__ import annotations

from hashlib import sha256
import inspect
import json

import pytest

from harness.contracts import (
    ChangeSet,
    EvalResult,
    FindingSeverity,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    ReviewDisposition,
    RunState,
    TaskSpec,
    TestIntent as HarnessTestIntent,
    TestKind as HarnessTestKind,
    TestResult as HarnessTestResult,
)
from harness.development_reviewer import (
    DevelopmentFindingCategory,
    DevelopmentReviewFinding,
    DevelopmentReviewReport,
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


def _approve(context, *, review_id: str = "independent-review") -> DevelopmentReviewReport:
    return DevelopmentReviewReport.create(
        review_id=review_id,
        reviewer_id="reviewer-b",
        review_context_id=context.review_context_id,
        inspected_sha=context.head_sha,
        disposition=ReviewDisposition.APPROVE,
        summary="approved",
        findings=(),
    )


def _rehash_context_payload(payload: dict) -> dict:
    identity = {key: value for key, value in payload.items() if key != "review_context_id"}
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["review_context_id"] = "review-context-" + sha256(encoded).hexdigest()
    return payload


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


def test_reviewer_revalidates_returned_structured_report_after_adapter_boundary() -> None:
    context = _context()

    def reviewer(packet):
        report = _approve(packet)
        blocker = DevelopmentReviewFinding(
            finding_id="late-blocker",
            category=DevelopmentFindingCategory.INVARIANT_RISK,
            severity=FindingSeverity.CRITICAL,
            summary="blocking finding injected after construction",
            evidence_refs=("diff:x.py",),
            location="x.py:1",
            blocking=True,
        )
        object.__setattr__(report, "findings", (blocker,))
        return report

    with pytest.raises(ValueError, match="report|finding|review|projection"):
        run_independent_development_review(
            context,
            IndependentReviewer("reviewer-b", reviewer),
        )


def test_apply_requires_the_exact_clean_context_and_revalidates_binding() -> None:
    context = _context()
    state = _review_ready_state()
    report = _approve(context)

    parameters = inspect.signature(apply_development_review).parameters
    assert tuple(parameters) == ("state", "context", "report")
    completed = apply_development_review(state, context, report)
    assert completed.review == report.review

    other = build_development_review_context(
        state,
        base_sha=BASE,
        head_sha=HEAD,
        final_diff=context.final_diff + "+other = 2\n",
        policy_refs=context.policy_refs,
        rubric=context.rubric,
    )
    with pytest.raises(ValueError, match="context|binding"):
        apply_development_review(state, other, report)


def test_structured_report_carries_category_location_and_lossless_core_projection() -> None:
    context = _context()
    categories = (
        DevelopmentFindingCategory.MISSING_TEST,
        DevelopmentFindingCategory.INVARIANT_RISK,
        DevelopmentFindingCategory.REGRESSION_RISK,
    )
    findings = tuple(
        DevelopmentReviewFinding(
            finding_id=f"finding-{index}",
            category=category,
            severity=FindingSeverity.MAJOR,
            summary="structured risk",
            evidence_refs=("review:evidence",),
            location=f"agent/file-{index}.py:10",
            blocking=False,
        )
        for index, category in enumerate(categories)
    )
    report = DevelopmentReviewReport.create(
        review_id="structured-review",
        reviewer_id="reviewer-b",
        review_context_id=context.review_context_id,
        inspected_sha=context.head_sha,
        disposition=ReviewDisposition.APPROVE,
        summary="structured review",
        findings=findings,
    )

    restored = DevelopmentReviewReport.from_json(report.to_json())
    assert restored == report
    assert tuple(item.category for item in restored.findings) == categories
    assert tuple(item.location for item in restored.findings) == (
        "agent/file-0.py:10",
        "agent/file-1.py:10",
        "agent/file-2.py:10",
    )
    assert tuple(item.finding_id for item in restored.review.findings) == tuple(
        item.finding_id for item in findings
    )
    assert tuple(item.evidence_refs for item in restored.review.findings) == tuple(
        item.evidence_refs for item in findings
    )


def test_structured_report_rejects_mismatched_core_projection() -> None:
    context = _context()
    finding = DevelopmentReviewFinding(
        finding_id="missing-regression",
        category=DevelopmentFindingCategory.MISSING_TEST,
        severity=FindingSeverity.MAJOR,
        summary="regression test missing",
        evidence_refs=("issue:7",),
        location="agent/tests/",
        blocking=True,
    )
    report = DevelopmentReviewReport.create(
        review_id="projection-review",
        reviewer_id="reviewer-b",
        review_context_id=context.review_context_id,
        inspected_sha=context.head_sha,
        disposition=ReviewDisposition.REQUEST_CHANGES,
        summary="changes required",
        findings=(finding,),
    )
    payload = report.to_dict()
    payload["review"] = dict(payload["review"])
    payload["review"]["findings"] = []
    with pytest.raises(ValueError, match="projection|finding|request-changes"):
        DevelopmentReviewReport.from_dict(payload)


def test_nested_context_deserialization_rejects_scalar_collection_smuggling() -> None:
    context = _context()
    payload = context.to_dict()
    payload["task"] = dict(payload["task"])
    payload["task"]["acceptance_criteria"] = "not-an-array"
    with pytest.raises(ValueError, match="acceptance|iterable|array"):
        type(context).from_dict(payload)

    payload = context.to_dict()
    payload["test_evidence"] = "not-an-array"
    with pytest.raises(ValueError, match="test.evidence|test_evidence|iterable|array"):
        type(context).from_dict(payload)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("test_evidence", "outcome", GateOutcome.TEST_FAILURE.value),
        ("test_evidence", "head_sha", "9" * 40),
        ("eval_evidence", "executed_sha", "8" * 40),
    ),
)
def test_rehashed_serialized_context_cannot_weaken_verified_evidence(
    section: str,
    field: str,
    value: str,
) -> None:
    context = _context()
    payload = context.to_dict()
    payload[section] = [dict(item) for item in payload[section]]
    payload[section][0][field] = value
    _rehash_context_payload(payload)
    with pytest.raises(ValueError, match="success|head|executed|verification|context"):
        type(context).from_dict(payload)
