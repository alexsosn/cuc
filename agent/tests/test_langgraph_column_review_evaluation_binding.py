from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    CompletionGateResult,
    EvidenceRecord,
    TokenDecision,
)
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
    measure_column_behavior,
)
from harness.skill_capabilities import SkillCapabilityRegistry
import harness.langgraph_column_review as runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64


def _state() -> ColumnRunState:
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    task = runtime.build_column_task_from_capability(
        manifest,
        provenance,
        task_id="eval-binding-run",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="fixture-revision",
    )
    return ColumnRunState.initial(
        task,
        ColumnSnapshot(
            "eval-binding-snapshot",
            "fixture:eval-binding",
            "fixture-provenance",
            (ColumnToken("t1", 1, "1", "a"),),
        ),
    )


def _record(state: ColumnRunState) -> ParsingEvaluationRecord:
    workload = ParsingWorkloadRef(
        corpus=state.task.corpus,
        tablet=state.task.tablet,
        column=state.task.column,
        snapshot_id=state.snapshot.snapshot_id,
        snapshot_provenance=state.snapshot.source_provenance,
        repository_revision=state.task.repository_revision,
        capability_name=state.task.capability.canonical_name,
        capability_contract_version=state.task.capability.contract_version,
        capability_provenance_sha256=state.task.capability.provenance_sha256,
        tool_policy_sha256=SHA0,
        evidence_policy_sha256=SHA0,
        permission_policy_sha256=SHA0,
    )
    return ParsingEvaluationRecord(
        1,
        ParsingRunIdentity("eval-binding-run", workload, "fixture", "fixture", "1", SHA0),
        EvaluationTarget(
            "fixture-target",
            "reviewed:fixture",
            "reviewed-provenance",
            "score_reviewed_morphology.py",
            "scorer-provenance",
            SHA0,
        ),
        state.decision_revision,
        measure_column_behavior(state),
        (),
        (),
        EfficiencyMetrics(model_calls=0, tool_calls=0, retries=0),
        ("fixture:evaluation",),
    )


def _graph(evaluation_mutator):
    context = ("worklist:legacy", "worklist:eupt", "worklist:tropper", "worklist:lint")

    def initialize_skill_context(state, operation_id):
        return context

    def collect_evidence(state, token, skill_context, operation_id):
        return (
            EvidenceRecord("ev-t1", "fixture", "fixture:t1", "fixture-provenance", "fixture evidence"),
        )

    def adjudicate(state, token, evidence, skill_context, operation_id, revisit_request=None):
        return TokenDecision(
            "decision-t1",
            token.token_id,
            ("parse:t1",),
            tuple(item.evidence_id for item in evidence),
            "fixture decision",
        )

    def reconcile(state, skill_context, operation_id):
        return runtime.ReconciliationPlan((), ())

    def verify_completion(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(
            gate_id,
            True,
            state.decision_revision,
            (f"gate:{gate_id}",),
            "passed",
        )

    def evaluate(state, skill_context, operation_id):
        return evaluation_mutator(_record(state))

    return runtime.compile_column_review_graph(
        runtime.ColumnReviewAdapters(
            initialize_skill_context=initialize_skill_context,
            collect_evidence=collect_evidence,
            adjudicate=adjudicate,
            reconcile=reconcile,
            verify_completion=verify_completion,
            evaluate=evaluate,
        )
    )


def test_evaluation_revision_must_match_completed_column_state() -> None:
    graph = _graph(lambda record: replace(record, decision_revision=record.decision_revision + 1))
    with pytest.raises(ValueError, match="evaluation.*revision|revision.*evaluation"):
        graph.invoke(
            runtime.initial_graph_input(_state()),
            config={"configurable": {"thread_id": "wrong-eval-revision"}},
        )


def test_evaluation_workload_must_match_completed_column_state() -> None:
    def wrong_snapshot(record: ParsingEvaluationRecord) -> ParsingEvaluationRecord:
        workload = replace(record.identity.workload, snapshot_id="another-snapshot")
        return replace(record, identity=replace(record.identity, workload=workload))

    graph = _graph(wrong_snapshot)
    with pytest.raises(ValueError, match="evaluation.*workload|workload.*evaluation"):
        graph.invoke(
            runtime.initial_graph_input(_state()),
            config={"configurable": {"thread_id": "wrong-eval-workload"}},
        )
