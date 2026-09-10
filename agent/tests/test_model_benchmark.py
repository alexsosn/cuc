from __future__ import annotations

from dataclasses import replace
import importlib
import json

import pytest

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


def _load_benchmark():
    try:
        return importlib.import_module("harness.model_benchmark")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-016 benchmark is not implemented yet: {exc}")


def _workload(**overrides) -> ParsingWorkloadRef:
    values = dict(
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        snapshot_id="snapshot-ktu-1.1-i",
        snapshot_provenance="snapshot-provenance",
        repository_revision="repo-revision",
        capability_name="review-automatic-parsing",
        capability_contract_version="1.0.0",
        capability_provenance_sha256=SHA0,
        tool_policy_sha256=SHA1,
        evidence_policy_sha256=SHA2,
        permission_policy_sha256=SHA3,
    )
    values.update(overrides)
    return ParsingWorkloadRef(**values)


def _target(**overrides) -> EvaluationTarget:
    values = dict(
        target_id="held-out-ktu-1.1-i",
        reviewed_ref="reviewed/KTU 1.1.tsv",
        reviewed_provenance="reviewed-provenance-secret-marker",
        scorer_id="score_reviewed_morphology.py",
        scorer_provenance="scorer-provenance",
        feedback_protocol_sha256=SHA0,
    )
    values.update(overrides)
    return EvaluationTarget(**values)


def _measurements(*, quality: float = 0.8, complete: bool = True, include_quality: bool = True):
    values = [
        EvaluationMeasurement(
            "behavior.expected_token_count",
            MeasurementKind.NUMERIC,
            3,
            MeasurementScope.COLUMN,
            "column-state",
        ),
        EvaluationMeasurement(
            "behavior.visited_initial_tokens",
            MeasurementKind.NUMERIC,
            3 if complete else 2,
            MeasurementScope.COLUMN,
            "column-state",
        ),
        EvaluationMeasurement(
            "behavior.column_completed",
            MeasurementKind.BOOLEAN,
            complete,
            MeasurementScope.COLUMN,
            "column-state",
        ),
    ]
    if include_quality:
        values.append(
            EvaluationMeasurement(
                "morphology.exact_set_accuracy",
                MeasurementKind.NUMERIC,
                quality,
                MeasurementScope.RUN,
                "reviewed-morphology-scorer",
            )
        )
    return tuple(values)


def _record(
    identity: ParsingRunIdentity,
    *,
    target: EvaluationTarget,
    quality: float = 0.8,
    complete: bool = True,
    include_quality: bool = True,
    efficiency: EfficiencyMetrics | None = None,
) -> ParsingEvaluationRecord:
    return ParsingEvaluationRecord(
        schema_version=1,
        identity=identity,
        target=target,
        decision_revision=3,
        deterministic_measurements=_measurements(
            quality=quality,
            complete=complete,
            include_quality=include_quality,
        ),
        supplementary_measurements=(),
        expert_feedback=(),
        efficiency=efficiency
        or EfficiencyMetrics(
            model_calls=3,
            tool_calls=9,
            retries=0,
            latency_ms=1000,
            input_tokens=100,
            output_tokens=20,
            cost=0.01,
            currency="USD",
        ),
        artifact_refs=(f"artifact:{identity.run_id}",),
    )


class FakeExecutor:
    def __init__(
        self,
        target: EvaluationTarget,
        *,
        quality: float,
        complete: bool = True,
        drift_identity: bool = False,
        drift_target: bool = False,
        omit_quality_on_repetition: int | None = None,
        currency_on_repetition: dict[int, str] | None = None,
        fail_on_repetition: int | None = None,
    ) -> None:
        self.target = target
        self.quality = quality
        self.complete = complete
        self.drift_identity = drift_identity
        self.drift_target = drift_target
        self.omit_quality_on_repetition = omit_quality_on_repetition
        self.currency_on_repetition = currency_on_repetition or {}
        self.fail_on_repetition = fail_on_repetition
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.repetition == self.fail_on_repetition:
            raise RuntimeError("provider failed")
        identity = request.identity
        if self.drift_identity:
            identity = replace(identity, model_version="wrong-version")
        target = self.target
        if self.drift_target:
            target = _target(target_id="different-held-out-target")
        currency = self.currency_on_repetition.get(request.repetition, "USD")
        return _record(
            identity,
            target=target,
            quality=self.quality,
            complete=self.complete,
            include_quality=request.repetition != self.omit_quality_on_repetition,
            efficiency=EfficiencyMetrics(
                model_calls=3,
                tool_calls=9,
                retries=request.repetition - 1,
                latency_ms=900 + 100 * request.repetition,
                input_tokens=100,
                output_tokens=20,
                cost=0.01 * request.repetition,
                currency=currency,
            ),
        )


def _backend(provider: str, model_id: str, version: str, executor):
    benchmark = _load_benchmark()
    return benchmark.BenchmarkBackend(
        provider=provider,
        model_id=model_id,
        model_version=version,
        model_config_sha256=SHA0,
        execute=executor,
    )


def _protocol(*, repetitions: int = 2, workload=None, target=None, context=None):
    benchmark = _load_benchmark()
    return benchmark.BenchmarkProtocol(
        workload=workload or _workload(),
        target=target or _target(),
        repetitions=repetitions,
        workload_context=context or {"column_text": "a b c", "worklist": ["t3"]},
    )


def test_two_distinct_model_backends_run_on_identical_fixed_workload():
    benchmark = _load_benchmark()
    target = _target()
    a_exec = FakeExecutor(target, quality=0.75)
    b_exec = FakeExecutor(target, quality=0.85)
    report = benchmark.run_benchmark(
        _protocol(target=target),
        (
            _backend("provider-b", "model-b", "2026-09-01", b_exec),
            _backend("provider-a", "model-a", "2026-08-15", a_exec),
        ),
    )

    assert len(report.runs) == 4
    assert [run.repetition for run in report.runs] == [1, 2, 1, 2]
    assert [run.record.identity.model_provider for run in report.runs] == [
        "provider-a", "provider-a", "provider-b", "provider-b"
    ]
    assert all(run.record.identity.workload == _workload() for run in report.runs)
    assert all(run.record.target == target for run in report.runs)
    assert len(report.arm_summaries) == 2
    assert report.arm_summaries[0].repetitions == 2
    assert report.arm_summaries[1].repetitions == 2


def test_backend_request_contains_no_evaluator_target_or_gold_material():
    benchmark = _load_benchmark()
    target = _target()
    executor = FakeExecutor(target, quality=0.8)
    other = FakeExecutor(target, quality=0.7)
    benchmark.run_benchmark(
        _protocol(target=target, repetitions=1),
        (
            _backend("p1", "m1", "v1", executor),
            _backend("p2", "m2", "v2", other),
        ),
    )
    request = executor.requests[0]
    assert not hasattr(request, "target")
    serialized = json.dumps(request.to_dict(), sort_keys=True)
    for forbidden in (
        target.target_id,
        target.reviewed_ref,
        target.reviewed_provenance,
        target.scorer_id,
        target.scorer_provenance,
    ):
        assert forbidden not in serialized


def test_protocol_rejects_held_out_target_material_in_workload_context():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(Exception, match="held|target|leak|reviewed"):
        benchmark.BenchmarkProtocol(
            workload=_workload(),
            target=target,
            repetitions=1,
            workload_context={"notes": target.reviewed_provenance},
        )


@pytest.mark.parametrize(
    "workload",
    (
        _workload(repository_revision="different"),
        _workload(snapshot_id="different"),
        _workload(capability_provenance_sha256="a" * 64),
        _workload(tool_policy_sha256="b" * 64),
        _workload(evidence_policy_sha256="c" * 64),
        _workload(permission_policy_sha256="d" * 64),
    ),
)
def test_returned_record_with_workload_drift_is_rejected(workload):
    benchmark = _load_benchmark()
    target = _target()

    def drifted(request):
        return _record(replace(request.identity, workload=workload), target=target)

    with pytest.raises(ValueError, match="identity|workload|compar"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=1),
            (
                _backend("p1", "m1", "v1", drifted),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_returned_record_target_drift_is_rejected():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="target|compar"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=1),
            (
                _backend("p1", "m1", "v1", FakeExecutor(target, quality=0.8, drift_target=True)),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_returned_record_must_match_exact_issued_model_identity():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="identity|model|version"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=1),
            (
                _backend("p1", "m1", "v1", FakeExecutor(target, quality=0.8, drift_identity=True)),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_duplicate_model_identity_cannot_masquerade_as_two_arms():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="distinct|duplicate|identity"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=1),
            (
                _backend("p", "m", "v", FakeExecutor(target, quality=0.8)),
                _backend("p", "m", "v", FakeExecutor(target, quality=0.9)),
            ),
        )


def test_incomplete_or_skipped_column_is_rejected_not_averaged():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="complete|token|visited"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=1),
            (
                _backend("p1", "m1", "v1", FakeExecutor(target, quality=0.9, complete=False)),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_backend_failure_aborts_instead_of_creating_survivor_bias():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(RuntimeError, match="provider failed"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=2),
            (
                _backend("p1", "m1", "v1", FakeExecutor(target, quality=0.8, fail_on_repetition=2)),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_metric_schema_drift_between_repetitions_is_rejected():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="measurement|schema|metric"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=2),
            (
                _backend("p1", "m1", "v1", FakeExecutor(target, quality=0.8, omit_quality_on_repetition=2)),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_mixed_cost_currency_within_arm_is_rejected():
    benchmark = _load_benchmark()
    target = _target()
    with pytest.raises(ValueError, match="currency|cost"):
        benchmark.run_benchmark(
            _protocol(target=target, repetitions=2),
            (
                _backend(
                    "p1",
                    "m1",
                    "v1",
                    FakeExecutor(target, quality=0.8, currency_on_repetition={2: "EUR"}),
                ),
                _backend("p2", "m2", "v2", FakeExecutor(target, quality=0.7)),
            ),
        )


def test_report_is_deterministic_and_keeps_quality_separate_from_efficiency():
    benchmark = _load_benchmark()
    target = _target()

    def make_backends(reverse=False):
        values = [
            _backend("provider-a", "model-a", "v1", FakeExecutor(target, quality=0.75)),
            _backend("provider-b", "model-b", "v2", FakeExecutor(target, quality=0.85)),
        ]
        return tuple(reversed(values)) if reverse else tuple(values)

    protocol = _protocol(target=target, repetitions=2)
    first = benchmark.run_benchmark(protocol, make_backends(False))
    second = benchmark.run_benchmark(protocol, make_backends(True))
    assert first.to_json() == second.to_json()

    payload = first.to_dict()
    assert "arm_summaries" in payload
    assert "composite_score" not in json.dumps(payload)
    assert "winner" not in payload
    for arm in payload["arm_summaries"]:
        assert "quality_means" in arm
        assert "efficiency_means" in arm
        assert "morphology.exact_set_accuracy" in arm["quality_means"]
        assert "latency_ms" in arm["efficiency_means"]


def test_benchmark_module_does_not_duplicate_scorer_or_import_provider_frameworks():
    benchmark = _load_benchmark()
    source = open(benchmark.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "reviewed_evaluation",
        "score_reviewed_morphology",
        "openai",
        "anthropic",
        "langchain",
        "langgraph",
        "langfuse",
    ):
        assert forbidden not in source
