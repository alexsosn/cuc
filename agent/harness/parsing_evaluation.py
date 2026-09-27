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


def _validate_identity_against_state(
    identity: ParsingRunIdentity,
    state: ColumnRunState,
) -> None:
    if not isinstance(identity, ParsingRunIdentity):
        raise ValueError("identity must be ParsingRunIdentity")
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    if identity.run_id != state.task.task_id:
        raise ValueError("evaluation identity run_id does not match column state")

    workload = identity.workload
    expected = {
        "corpus": state.task.corpus,
        "tablet": state.task.tablet,
        "column": state.task.column,
        "snapshot_id": state.snapshot.snapshot_id,
        "snapshot_provenance": state.snapshot.source_provenance,
        "repository_revision": state.task.repository_revision,
        "capability_name": state.task.capability.canonical_name,
        "capability_contract_version": state.task.capability.contract_version,
        "capability_provenance_sha256": state.task.capability.provenance_sha256,
    }
    mismatches = tuple(
        field
        for field, expected_value in expected.items()
        if getattr(workload, field) != expected_value
    )
    if mismatches:
        raise ValueError(
            "evaluation identity workload does not match column state: "
            + ", ".join(mismatches)
        )


def build_no_gold_evaluation(
    *,
    state: ColumnRunState,
    identity: ParsingRunIdentity,
    target_id: str,
    evaluator_id: str,
    evaluator_provenance: str,
    feedback_protocol_sha256: str,
    efficiency: EfficiencyMetrics,
    artifact_refs: tuple[str, ...] = (),
) -> ParsingEvaluationRecord:
    """Build a completed-column behavior/efficiency record without reviewed gold.

    This path intentionally has no reviewed-morphology scorer input or callback.
    """
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    if state.completion is None:
        raise ValueError("no-gold evaluation requires a completed column state")
    if not isinstance(efficiency, EfficiencyMetrics):
        raise ValueError("efficiency must be EfficiencyMetrics")
    _validate_identity_against_state(identity, state)

    target = EvaluationTarget(
        target_id,
        None,
        None,
        evaluator_id,
        evaluator_provenance,
        feedback_protocol_sha256,
        EvaluationTargetKind.NO_GOLD,
    )
    return ParsingEvaluationRecord(
        1,
        identity,
        target,
        state.decision_revision,
        measure_column_behavior(state),
        (),
        (),
        efficiency,
        artifact_refs,
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
