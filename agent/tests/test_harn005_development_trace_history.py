from __future__ import annotations

from harness.contracts import (
    ChangeSet,
    EvalResult,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent as HarnessTestIntent,
    TestKind as HarnessTestKind,
    TestResult,
)
from harness.telemetry import DevelopmentTraceContext, build_development_trace_projection


def _state() -> RunState:
    return RunState(
        run_id="dev-run-history",
        task=TaskSpec(
            "HARN-005",
            "Telemetry",
            "Preserve the full development operation history",
            ("queryable-history",),
        ),
        phase=RunPhase.VERIFY,
        research=ResearchArtifact("research-1", "research complete"),
        plan=PlanArtifact("plan-1", "plan complete", ("test", "implement")),
        test_intents=(
            HarnessTestIntent(
                "intent-1",
                HarnessTestKind.REGRESSION,
                ("pytest", "-q"),
                "agent",
                "regression suite",
            ),
        ),
        changes=(
            ChangeSet(
                "change-1",
                "first implementation iteration",
                ("agent/harness/first.py",),
                ("op:first:research", "op:first:write"),
            ),
            ChangeSet(
                "change-2",
                "review-driven implementation iteration",
                ("agent/harness/second.py",),
                ("op:second:test", "op:second:write"),
            ),
        ),
        test_results=(
            TestResult(
                "intent-1",
                "change-2",
                GateOutcome.SUCCESS,
                "head-change-2",
                "executed-change-2",
                0,
                1167,
                0,
                "full suite green",
            ),
        ),
        eval_results=(
            EvalResult(
                "eval-1",
                "change-2",
                GateOutcome.SUCCESS,
                "head-change-2",
                "executed-change-2",
                "evaluation green",
                (("quality", 1.0),),
            ),
        ),
    )


def test_development_projection_preserves_full_phase_change_and_gate_identity() -> None:
    state = _state()
    context = DevelopmentTraceContext(
        "alexsosn/cuc",
        "#6",
        "agent-harness-safety",
        "harn-005-langfuse-sidecar",
        "head-sha",
        "executed-sha",
    )

    projection = build_development_trace_projection(state, context)

    # Research -> plan -> TDD identity must remain queryable independently of the
    # current phase; change history alone cannot identify the artifacts that led to it.
    assert projection.metadata["research_artifact_id"] == "research-1"
    assert projection.metadata["plan_id"] == "plan-1"
    assert projection.metadata["test_intent_ids"] == ("intent-1",)

    assert projection.metadata["change_count"] == 2
    assert projection.metadata["change_ids"] == ("change-1", "change-2")
    assert projection.metadata["operation_ids"] == (
        "op:first:research",
        "op:first:write",
        "op:second:test",
        "op:second:write",
    )
    # Keep convenience fields for the current iteration without losing history.
    assert projection.metadata["latest_change_id"] == "change-2"
    assert projection.metadata["latest_operation_ids"] == (
        "op:second:test",
        "op:second:write",
    )

    # A global PR head/executed pair is insufficient for a multi-iteration run: each
    # durable gate result must retain the revision on which it actually executed.
    assert projection.metadata["test_result_intent_ids"] == ("intent-1",)
    assert projection.metadata["test_result_change_ids"] == ("change-2",)
    assert projection.metadata["test_result_head_shas"] == ("head-change-2",)
    assert projection.metadata["test_result_executed_shas"] == ("executed-change-2",)
    assert projection.metadata["eval_result_ids"] == ("eval-1",)
    assert projection.metadata["eval_result_change_ids"] == ("change-2",)
    assert projection.metadata["eval_result_head_shas"] == ("head-change-2",)
    assert projection.metadata["eval_result_executed_shas"] == ("executed-change-2",)
