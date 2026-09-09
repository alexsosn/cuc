from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64
SHA4 = "4" * 64
SHA5 = "5" * 64
SHA6 = "6" * 64


def _telemetry():
    try:
        return importlib.import_module("harness.telemetry")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-005 provider-neutral telemetry is not implemented yet: {exc}")


def _parsing_fixture():
    column = importlib.import_module("harness.column_state")
    evaluation = importlib.import_module("harness.parsing_evaluation")

    task = column.ColumnTask(
        "parse-run-1",
        "CUC",
        "KTU 1.5",
        "I",
        "repo-revision-1",
        column.CapabilityRef(
            "review-automatic-parsing",
            "1.0.0",
            SHA2,
        ),
        ("review-status-clean", "lint-error-delta-no-regression"),
        ("t2",),
    )
    snapshot = column.ColumnSnapshot(
        "snapshot-1",
        "auto_parsing/0.2.8/KTU 1.5.tsv",
        "snapshot-provenance-1",
        (
            column.ColumnToken("t1", 1, "1.5:I:1", "RAW-SURFACE-MUST-NOT-LEAK"),
            column.ColumnToken("t2", 2, "1.5:I:2", "another-surface"),
        ),
    )
    state = column.ColumnRunState.initial(task, snapshot)
    state = column.apply_column_event(
        state,
        column.EvidenceRecorded(
            "evidence-event-1",
            column.EvidenceRecord(
                "ev1",
                "dulat",
                "DULAT:entry-secret",
                "dulat:provenance",
                "RAW-EVIDENCE-SUMMARY-MUST-NOT-LEAK",
            ),
        ),
    )
    state = column.apply_column_event(
        state,
        column.TokenReviewed(
            "review-t1",
            column.TokenDecision(
                "decision-1",
                "t1",
                ("RAW-ANALYSIS-MUST-NOT-LEAK",),
                ("ev1",),
                "RAW-DECISION-SUMMARY-MUST-NOT-LEAK",
            ),
        ),
    )

    workload = evaluation.ParsingWorkloadRef(
        corpus="CUC",
        tablet="KTU 1.5",
        column="I",
        snapshot_id="snapshot-1",
        snapshot_provenance="snapshot-provenance-1",
        repository_revision="repo-revision-1",
        capability_name="review-automatic-parsing",
        capability_contract_version="1.0.0",
        capability_provenance_sha256=SHA2,
        tool_policy_sha256=SHA3,
        evidence_policy_sha256=SHA4,
        permission_policy_sha256=SHA5,
    )
    identity = evaluation.ParsingRunIdentity(
        "parse-run-1",
        workload,
        "provider-a",
        "model-a",
        "2026-09-09",
        SHA6,
    )
    target = evaluation.EvaluationTarget(
        "held-out-target",
        "reviewed/SECRET-GOLD.tsv",
        "SECRET-GOLD-PROVENANCE",
        "score_reviewed_morphology.py",
        "scorer:provenance",
        SHA1,
    )
    measurements = (
        evaluation.EvaluationMeasurement(
            "morphology.exact_set_accuracy",
            evaluation.MeasurementKind.NUMERIC,
            0.1234567890123456,
            evaluation.MeasurementScope.RUN,
            "reviewed-morphology-scorer",
            ("artifact:scorer-json",),
            deterministic=True,
        ),
        evaluation.EvaluationMeasurement(
            "behavior.column_completed",
            evaluation.MeasurementKind.BOOLEAN,
            False,
            evaluation.MeasurementScope.COLUMN,
            "column-state",
            ("state:revision-1",),
            deterministic=True,
        ),
    )
    feedback = evaluation.ExpertFeedback(
        "feedback-1",
        "parse-run-1",
        1,
        evaluation.FeedbackScope.TOKEN,
        evaluation.FeedbackDisposition.CORRECT,
        "expert:reviewer-1",
        "RAW-EXPERT-RATIONALE-MUST-NOT-LEAK",
        ("publication:ref",),
        token_id="t1",
        decision_id="decision-1",
        corrected_analyses=("RAW-CORRECTED-ANALYSIS-MUST-NOT-LEAK",),
    )
    record = evaluation.ParsingEvaluationRecord(
        1,
        identity,
        target,
        1,
        measurements,
        (),
        (feedback,),
        evaluation.EfficiencyMetrics(model_calls=2, tool_calls=4, retries=1),
        ("artifact:scorer-json",),
    )
    return state, record


def _development_fixture():
    contracts = importlib.import_module("harness.contracts")
    task = contracts.TaskSpec(
        "HARN-005",
        "Langfuse sidecar",
        "Add optional telemetry without changing correctness",
        ("optional", "failure-isolated"),
    )
    return contracts.RunState(run_id="dev-run-1", task=task)


def test_parsing_projection_uses_exact_queryable_identity_without_raw_scholarly_payloads():
    telemetry = _telemetry()
    state, record = _parsing_fixture()

    projection = telemetry.build_parsing_trace_projection(state, record)

    assert projection.trace_name == "cuc.parsing.column-review"
    assert projection.run_type is telemetry.TelemetryRunType.PARSING
    assert projection.run_id == "parse-run-1"
    assert projection.metadata["run_type"] == "parsing"
    assert projection.metadata["corpus"] == "CUC"
    assert projection.metadata["tablet"] == "KTU 1.5"
    assert projection.metadata["column"] == "I"
    assert projection.metadata["snapshot_id"] == "snapshot-1"
    assert projection.metadata["snapshot_provenance"] == "snapshot-provenance-1"
    assert projection.metadata["repository_revision"] == "repo-revision-1"
    assert projection.metadata["capability_name"] == "review-automatic-parsing"
    assert projection.metadata["capability_contract_version"] == "1.0.0"
    assert projection.metadata["capability_provenance_sha256"] == SHA2
    assert projection.metadata["model_provider"] == "provider-a"
    assert projection.metadata["model_id"] == "model-a"
    assert projection.metadata["model_version"] == "2026-09-09"
    assert projection.metadata["model_config_sha256"] == SHA6
    assert projection.metadata["tool_policy_sha256"] == SHA3
    assert projection.metadata["evidence_policy_sha256"] == SHA4
    assert projection.metadata["permission_policy_sha256"] == SHA5
    assert projection.metadata["decision_revision"] == 1
    assert projection.metadata["next_token_index"] == 1

    encoded = json.dumps(projection.to_dict(), sort_keys=True)
    for forbidden in (
        "RAW-SURFACE-MUST-NOT-LEAK",
        "RAW-EVIDENCE-SUMMARY-MUST-NOT-LEAK",
        "RAW-ANALYSIS-MUST-NOT-LEAK",
        "RAW-DECISION-SUMMARY-MUST-NOT-LEAK",
        "RAW-EXPERT-RATIONALE-MUST-NOT-LEAK",
        "RAW-CORRECTED-ANALYSIS-MUST-NOT-LEAK",
        "reviewed/SECRET-GOLD.tsv",
        "SECRET-GOLD-PROVENANCE",
    ):
        assert forbidden not in encoded


def test_score_projection_forwards_authoritative_harn015_scalars_exactly():
    telemetry = _telemetry()
    _, record = _parsing_fixture()

    scores = telemetry.project_parsing_scores(record)
    by_name = {score.name: score for score in scores}

    assert by_name["morphology.exact_set_accuracy"].value == 0.1234567890123456
    assert by_name["morphology.exact_set_accuracy"].data_type == "NUMERIC"
    assert by_name["behavior.column_completed"].value is False
    assert by_name["behavior.column_completed"].data_type == "BOOLEAN"
    assert all(score.run_type is telemetry.TelemetryRunType.PARSING for score in scores)
    assert all(score.run_id == "parse-run-1" for score in scores)


def test_development_projection_is_separate_and_requires_runstate_plus_git_context():
    telemetry = _telemetry()
    state = _development_fixture()
    context = telemetry.DevelopmentTraceContext(
        repository="alexsosn/cuc",
        issue_ref="#6",
        base_branch="agent-harness-safety",
        head_branch="harn-005-langfuse-sidecar",
        head_sha="head-sha-1",
        executed_sha="merge-sha-1",
        model_provider="provider-dev",
        model_id="model-dev",
        model_version="v1",
    )

    projection = telemetry.build_development_trace_projection(state, context)

    assert projection.trace_name == "cuc.development.run"
    assert projection.run_type is telemetry.TelemetryRunType.DEVELOPMENT
    assert projection.run_id == "dev-run-1"
    assert projection.metadata["run_type"] == "development"
    assert projection.metadata["repository"] == "alexsosn/cuc"
    assert projection.metadata["issue_ref"] == "#6"
    assert projection.metadata["head_sha"] == "head-sha-1"
    assert projection.metadata["executed_sha"] == "merge-sha-1"
    assert projection.metadata["model_id"] == "model-dev"
    assert projection.metadata["phase"] == state.phase.value

    _, parsing_record = _parsing_fixture()
    with pytest.raises((TypeError, ValueError)):
        telemetry.build_development_trace_projection(parsing_record, context)


def test_provider_neutral_module_has_no_langfuse_or_scorer_dependency_and_domain_state_stays_clean():
    telemetry = _telemetry()
    state, _ = _parsing_fixture()

    source = Path(inspect.getsourcefile(telemetry)).read_text(encoding="utf-8").lower()
    assert "import langfuse" not in source
    assert "score_reviewed_morphology" not in source
    assert "reviewed_evaluation" not in source

    encoded_state = state.to_json().lower()
    for forbidden in ("langfuse", "trace_id", "observation_id", "telemetry"):
        assert forbidden not in encoded_state
