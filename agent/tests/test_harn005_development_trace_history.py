from __future__ import annotations

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
            TestIntent(
                "intent-1",
                TestKind.REGRESSION,
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
    )


def test_development_projection_preserves_all_change_and_operation_ids() -> None:
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
