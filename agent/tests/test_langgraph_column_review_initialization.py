from __future__ import annotations

from hashlib import sha256
import inspect
import json
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
        task_id="init-fixture",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="fixture-revision",
    )
    return ColumnRunState.initial(
        task,
        ColumnSnapshot(
            "init-snapshot",
            "fixture:init",
            "fixture-provenance",
            (ColumnToken("t1", 1, "1", "a"),),
        ),
    )


def _evaluation(state: ColumnRunState) -> ParsingEvaluationRecord:
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
        ParsingRunIdentity("init-fixture", workload, "fixture", "fixture", "1", SHA0),
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


def test_skill_context_initializes_once_before_tokens_and_survives_resume() -> None:
    parameters = inspect.signature(runtime.ColumnReviewAdapters).parameters
    if "initialize_skill_context" not in parameters:
        pytest.fail("ColumnReviewAdapters lacks mandatory initialize_skill_context stage")

    calls: list[str] = []
    failed_once = False

    def initialize_skill_context(state, operation_id):
        calls.append(f"initialize:{operation_id}")
        return ("worklist:legacy", "worklist:eupt", "worklist:tropper", "worklist:lint")

    def collect_evidence(state, token, operation_id):
        calls.append(f"evidence:{token.token_id}")
        return (
            EvidenceRecord(
                "ev-t1",
                "fixture",
                "fixture:t1",
                "fixture-provenance",
                "fixture evidence",
            ),
        )

    def adjudicate(state, token, evidence, operation_id, revisit_request=None):
        nonlocal failed_once
        calls.append(f"adjudicate:{token.token_id}")
        if not failed_once:
            failed_once = True
            raise RuntimeError("intentional initialization-resume failure")
        return TokenDecision(
            "decision-t1",
            token.token_id,
            ("parse:t1",),
            tuple(item.evidence_id for item in evidence),
            "fixture decision",
        )

    def reconcile(state, operation_id):
        calls.append("reconcile")
        return runtime.ReconciliationPlan((), ())

    def verify_completion(state, gate_id, operation_id):
        calls.append(f"gate:{gate_id}")
        return CompletionGateResult(
            gate_id,
            True,
            state.decision_revision,
            (f"gate:{gate_id}",),
            "passed",
        )

    def evaluate(state, operation_id):
        calls.append("evaluate")
        return _evaluation(state)

    adapters = runtime.ColumnReviewAdapters(
        initialize_skill_context=initialize_skill_context,
        collect_evidence=collect_evidence,
        adjudicate=adjudicate,
        reconcile=reconcile,
        verify_completion=verify_completion,
        evaluate=evaluate,
    )
    graph = runtime.compile_column_review_graph(adapters)
    config = {"configurable": {"thread_id": "init-resume"}}
    with pytest.raises(RuntimeError, match="intentional initialization-resume failure"):
        graph.invoke(runtime.initial_graph_input(_state()), config=config)
    result = graph.invoke(None, config=config)

    assert result["terminal_status"] == "completed"
    assert sum(item.startswith("initialize:") for item in calls) == 1
    assert calls[0].startswith("initialize:")
    assert calls.index("evidence:t1") > 0
    assert calls.count("evidence:t1") == 1
    assert calls.count("adjudicate:t1") == 2
