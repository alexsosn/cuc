from __future__ import annotations

from dataclasses import replace

import pytest

import harness.parsing_evaluation as pe
from harness.column_state import (
    CapabilityRef,
    ColumnCompleted,
    ColumnRunState,
    ColumnSnapshot,
    ColumnTask,
    ColumnToken,
    CompletionGateRecorded,
    CompletionGateResult,
    EvidenceRecord,
    EvidenceRecorded,
    ReconciliationClosed,
    TokenDecision,
    TokenReviewed,
    apply_column_event,
)


SHA0 = "0" * 64
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64


def _completed_state() -> ColumnRunState:
    task = ColumnTask(
        "no-gold-run",
        "CUC",
        "KTU 9.9",
        "I",
        "fixture-revision",
        CapabilityRef("review-automatic-parsing", "1.0.0", SHA0),
        ("review-status-clean",),
    )
    snapshot = ColumnSnapshot(
        "no-gold-snapshot",
        "fixture:no-gold",
        "fixture-provenance",
        (ColumnToken("t1", 1, "I:1", "a"),),
    )
    state = ColumnRunState.initial(task, snapshot)
    evidence = EvidenceRecord("e1", "fixture", "fixture:t1", "prov:e1", "evidence")
    state = apply_column_event(state, EvidenceRecorded("record-e1", evidence))
    state = apply_column_event(
        state,
        TokenReviewed(
            "review-t1",
            TokenDecision("d1", "t1", ("a/",), ("e1",), "reviewed"),
        ),
    )
    state = apply_column_event(state, ReconciliationClosed("close"))
    state = apply_column_event(
        state,
        CompletionGateRecorded(
            "gate-review-status",
            CompletionGateResult(
                "review-status-clean",
                True,
                state.decision_revision,
                ("gate:fixture",),
                "passed",
            ),
        ),
    )
    return apply_column_event(state, ColumnCompleted("complete"))


def _identity(state: ColumnRunState) -> pe.ParsingRunIdentity:
    workload = pe.ParsingWorkloadRef(
        corpus=state.task.corpus,
        tablet=state.task.tablet,
        column=state.task.column,
        snapshot_id=state.snapshot.snapshot_id,
        snapshot_provenance=state.snapshot.source_provenance,
        repository_revision=state.task.repository_revision,
        capability_name=state.task.capability.canonical_name,
        capability_contract_version=state.task.capability.contract_version,
        capability_provenance_sha256=state.task.capability.provenance_sha256,
        tool_policy_sha256=SHA1,
        evidence_policy_sha256=SHA2,
        permission_policy_sha256=SHA3,
    )
    return pe.ParsingRunIdentity(
        state.task.task_id,
        workload,
        "test-double",
        "fixture-model",
        "1",
        SHA0,
    )


def _legacy_gold_target_payload() -> dict[str, object]:
    return {
        "target_id": "gold-target",
        "reviewed_ref": "reviewed/KTU 9.9.tsv#I",
        "reviewed_provenance": "git-blob:abc",
        "scorer_id": "score_reviewed_morphology.py",
        "scorer_provenance": "git-blob:def",
        "feedback_protocol_sha256": SHA0,
    }


def test_legacy_gold_target_payload_defaults_to_gold_kind() -> None:
    target = pe.EvaluationTarget.from_dict(_legacy_gold_target_payload())
    assert target.kind is pe.EvaluationTargetKind.GOLD
    assert target.reviewed_ref == "reviewed/KTU 9.9.tsv#I"
    assert target.reviewed_provenance == "git-blob:abc"

    positional = pe.EvaluationTarget(
        "gold-target",
        "reviewed/KTU 9.9.tsv#I",
        "git-blob:abc",
        "score_reviewed_morphology.py",
        "git-blob:def",
        SHA0,
    )
    assert positional.kind is pe.EvaluationTargetKind.GOLD


def test_no_gold_target_round_trips_without_reviewed_reference() -> None:
    target = pe.EvaluationTarget(
        "no-gold-target",
        None,
        None,
        "measure_column_behavior",
        "harness.parsing_evaluation:measure_column_behavior",
        SHA0,
        pe.EvaluationTargetKind.NO_GOLD,
    )
    restored = pe.EvaluationTarget.from_dict(target.to_dict())
    assert restored == target
    assert restored.kind is pe.EvaluationTargetKind.NO_GOLD
    assert restored.reviewed_ref is None
    assert restored.reviewed_provenance is None


@pytest.mark.parametrize(
    "kwargs",
    (
        {
            "reviewed_ref": None,
            "reviewed_provenance": None,
            "kind": "gold",
        },
        {
            "reviewed_ref": "reviewed/KTU 9.9.tsv#I",
            "reviewed_provenance": "git-blob:abc",
            "kind": "no-gold",
        },
        {
            "reviewed_ref": None,
            "reviewed_provenance": "git-blob:abc",
            "kind": "no-gold",
        },
    ),
)
def test_evaluation_target_rejects_invalid_gold_no_gold_field_combinations(kwargs) -> None:
    with pytest.raises(ValueError, match="gold|reviewed"):
        pe.EvaluationTarget(
            "target",
            kwargs["reviewed_ref"],
            kwargs["reviewed_provenance"],
            "measure_column_behavior",
            "fixture:evaluator",
            SHA0,
            kwargs["kind"],
        )


def test_gold_and_no_gold_records_are_non_comparable_on_target_kind() -> None:
    state = _completed_state()
    identity = _identity(state)
    gold_target = pe.EvaluationTarget.from_dict(_legacy_gold_target_payload())
    no_gold_target = pe.EvaluationTarget(
        gold_target.target_id,
        None,
        None,
        gold_target.scorer_id,
        gold_target.scorer_provenance,
        gold_target.feedback_protocol_sha256,
        pe.EvaluationTargetKind.NO_GOLD,
    )
    measurements = pe.measure_column_behavior(state)
    gold = pe.ParsingEvaluationRecord(
        1,
        identity,
        gold_target,
        state.decision_revision,
        measurements,
        (),
        (),
        pe.EfficiencyMetrics(model_calls=0, tool_calls=0, retries=0),
    )
    no_gold = replace(gold, target=no_gold_target)

    report = pe.compare_evaluation_records(gold, no_gold)
    assert report.comparable is False
    assert "target.kind" in report.mismatched_dimensions


def test_no_gold_factory_emits_behavior_and_efficiency_without_reviewed_scorer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _completed_state()
    identity = _identity(state)

    def forbidden_scorer(*args, **kwargs):
        raise AssertionError("reviewed morphology scorer path must not run")

    monkeypatch.setattr(pe, "measure_morphology_summary", forbidden_scorer)
    record = pe.build_no_gold_evaluation(
        state=state,
        identity=identity,
        target_id="no-gold-target",
        evaluator_id="measure_column_behavior",
        evaluator_provenance="harness.parsing_evaluation:measure_column_behavior",
        feedback_protocol_sha256=SHA0,
        efficiency=pe.EfficiencyMetrics(
            model_calls=1,
            tool_calls=2,
            retries=0,
            input_tokens=10,
            output_tokens=5,
        ),
        artifact_refs=("run:no-gold",),
    )

    assert record.target.kind is pe.EvaluationTargetKind.NO_GOLD
    assert record.target.reviewed_ref is None
    assert record.target.reviewed_provenance is None
    assert record.expert_feedback == ()
    assert record.supplementary_measurements == ()
    assert record.efficiency.model_calls == 1
    assert record.deterministic_measurements
    assert all(item.source == "column-state" for item in record.deterministic_measurements)
    assert all(not item.name.startswith("morphology.") for item in record.deterministic_measurements)
