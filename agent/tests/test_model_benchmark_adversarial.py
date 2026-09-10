from __future__ import annotations

from dataclasses import replace

import pytest

from harness.model_benchmark import BenchmarkBackend, BenchmarkProtocol, run_benchmark
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationMeasurement,
    EvaluationTarget,
    MeasurementKind,
    MeasurementScope,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
)


SHA0 = "0" * 64
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64


def _workload() -> ParsingWorkloadRef:
    return ParsingWorkloadRef(
        "CUC",
        "KTU 1.1",
        "I",
        "snapshot-ktu-1.1-i",
        "snapshot-provenance",
        "repo-revision",
        "review-automatic-parsing",
        "1.0.0",
        SHA0,
        SHA1,
        SHA2,
        SHA3,
    )


def _target() -> EvaluationTarget:
    return EvaluationTarget(
        "held-out-ktu-1.1-i",
        "reviewed/KTU 1.1.tsv",
        "reviewed-provenance",
        "score-reviewed",
        "scorer-provenance",
        SHA0,
    )


def _measurements(*, quality: float = 0.8, unresolved_revisits: int = 0, unresolved_findings: int = 0):
    return (
        EvaluationMeasurement(
            "behavior.expected_token_count", MeasurementKind.NUMERIC, 3,
            MeasurementScope.COLUMN, "column-state"
        ),
        EvaluationMeasurement(
            "behavior.visited_initial_tokens", MeasurementKind.NUMERIC, 3,
            MeasurementScope.COLUMN, "column-state"
        ),
        EvaluationMeasurement(
            "behavior.unresolved_revisit_count", MeasurementKind.NUMERIC, unresolved_revisits,
            MeasurementScope.COLUMN, "column-state"
        ),
        EvaluationMeasurement(
            "behavior.unresolved_required_finding_count", MeasurementKind.NUMERIC, unresolved_findings,
            MeasurementScope.COLUMN, "column-state"
        ),
        EvaluationMeasurement(
            "behavior.column_completed", MeasurementKind.BOOLEAN, True,
            MeasurementScope.COLUMN, "column-state"
        ),
        EvaluationMeasurement(
            "morphology.exact_set_accuracy", MeasurementKind.NUMERIC, quality,
            MeasurementScope.RUN, "reviewed-morphology-scorer"
        ),
    )


def _record(identity, *, measurements=None, decision_revision: int = 3):
    return ParsingEvaluationRecord(
        1,
        identity,
        _target(),
        decision_revision,
        measurements or _measurements(),
        (),
        (),
        EfficiencyMetrics(model_calls=3, tool_calls=9, retries=0),
        (f"artifact:{identity.run_id}",),
    )


def _backend(provider: str, execute):
    return BenchmarkBackend(provider, f"model-{provider}", f"version-{provider}", SHA0, execute)


def _protocol(repetitions: int = 1):
    return BenchmarkProtocol(
        _workload(),
        _target(),
        repetitions,
        {"column_text": "a b c", "worklist": ["t3"]},
    )


def test_cross_arm_measurement_schema_drift_is_rejected() -> None:
    def full(request):
        return _record(request.identity)

    def reduced(request):
        values = tuple(
            item for item in _measurements()
            if item.name != "morphology.exact_set_accuracy"
        )
        return _record(request.identity, measurements=values)

    with pytest.raises(ValueError, match="measurement|schema|metric|protocol"):
        run_benchmark(_protocol(), (_backend("a", full), _backend("b", reduced)))


def test_completed_flag_cannot_hide_unresolved_reconciliation_work() -> None:
    def unresolved(request):
        return _record(
            request.identity,
            measurements=_measurements(unresolved_revisits=1, unresolved_findings=1),
        )

    def clean(request):
        return _record(request.identity, measurements=_measurements())

    with pytest.raises(ValueError, match="revisit|finding|complete|reconciliation"):
        run_benchmark(_protocol(), (_backend("a", unresolved), _backend("b", clean)))


def test_summary_rejects_ambiguous_numeric_metric_names_across_sources() -> None:
    def ambiguous(request):
        extras = (
            EvaluationMeasurement(
                "quality.shared", MeasurementKind.NUMERIC, 0.25,
                MeasurementScope.RUN, "source-a"
            ),
            EvaluationMeasurement(
                "quality.shared", MeasurementKind.NUMERIC, 0.75,
                MeasurementScope.RUN, "source-b"
            ),
        )
        return _record(request.identity, measurements=_measurements() + extras)

    with pytest.raises(ValueError, match="ambiguous|measurement|metric|source"):
        run_benchmark(_protocol(), (_backend("a", ambiguous), _backend("b", ambiguous)))


def test_decision_revision_is_an_outcome_and_may_differ_between_model_arms() -> None:
    def three(request):
        return _record(request.identity, decision_revision=3)

    def four(request):
        return _record(request.identity, decision_revision=4)

    report = run_benchmark(_protocol(), (_backend("a", three), _backend("b", four)))
    assert [run.record.decision_revision for run in report.runs] == [3, 4]


def test_workload_context_is_immutable_inside_backend_requests() -> None:
    mutation_errors = []

    def executor(request):
        try:
            request.workload_context["column_text"] = "changed"
        except TypeError as exc:
            mutation_errors.append(exc)
        return _record(request.identity)

    run_benchmark(_protocol(), (_backend("a", executor), _backend("b", executor)))
    assert len(mutation_errors) == 2
