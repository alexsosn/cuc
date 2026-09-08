"""Public parsing-evaluation API with strict state-bound feedback validation."""

from __future__ import annotations

from . import _parsing_evaluation_core as _core
from ._parsing_evaluation_core import *  # noqa: F401,F403


def validate_feedback_against_state(feedback: ExpertFeedback, state: ColumnRunState) -> None:
    """Validate feedback against the exact current column-state decision."""
    _core.validate_feedback_against_state(feedback, state)
    if feedback.scope is FeedbackScope.TOKEN:
        latest = state.latest_decision(feedback.token_id)
        if latest is None or latest.decision_id != feedback.decision_id:
            raise ValueError(
                "feedback decision_id must reference the latest token decision at the column run revision"
            )


def compare_evaluation_records(
    left: ParsingEvaluationRecord,
    right: ParsingEvaluationRecord,
    *,
    ignore_model_identity: bool = False,
) -> ComparabilityReport:
    """Compare benchmark inputs/evaluator policy without treating run outcomes as inputs."""
    report = _core.compare_evaluation_records(
        left,
        right,
        ignore_model_identity=ignore_model_identity,
    )
    mismatches = tuple(
        dimension
        for dimension in report.mismatched_dimensions
        if dimension != "decision_revision"
    )
    return ComparabilityReport(not mismatches, mismatches)
