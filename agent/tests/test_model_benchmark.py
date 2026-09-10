from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path

import pytest

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    EvidenceRecord,
    EvidenceRecorded,
    TokenDecision,
    TokenReviewed,
    apply_column_event,
)
from harness.langgraph_column_review import (
    ReconciliationPlan,
    build_column_task_from_capability,
)
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    measure_column_behavior,
)
from harness.skill_capabilities import SkillCapabilityRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64
GOLD_REF = "reviewed:GOLD-DO-NOT-LEAK"


def _runtime():
    try:
        return importlib.import_module("harness.model_benchmark")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-016 benchmark runtime is not implemented yet: {exc}")


def _base_state() -> ColumnRunState:
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    task = build_column_task_from_capability(
        manifest,
        provenance,
        task_id="benchmark-template",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="fixture-revision",
        evidence_priority_token_ids=("t2",),
    )
    snapshot = ColumnSnapshot(
        "benchmark-snapshot",
        "fixture:benchmark",
        "fixture-provenance",
        (
            ColumnToken("t1", 1, "1", "a"),
            ColumnToken("t2", 2, "1", "b"),
            ColumnToken("t3", 3, "2", "c"),
        ),
    )
    return ColumnRunState.initial(task, snapshot)


def _target(*, suffix: str = "") -> EvaluationTarget:
    return EvaluationTarget(
        f"target{suffix}",
        f"{GOLD_REF}{suffix}",
        f"gold-provenance{suffix}",
        "score_reviewed_morphology.py",
        f"scorer-provenance{suffix}",
        SHA0,
    )


def _case(**overrides):
    runtime = _runtime()
    kwargs = dict(
        protocol_version=1,
        case_id="case-ktu-1.1-i",
        initial_state=_base_state(),
        tool_policy_sha256=SHA1,
        evidence_policy_sha256=SHA2,
        permission_policy_sha256=SHA3,
        evaluation_target=_target(),
    )
    kwargs.update(overrides)
    return runtime.BenchmarkCase(**kwargs)


def _spec(backend_id: str, *, model: str | None = None):
    runtime = _runtime()
    return runtime.BenchmarkBackendSpec(
        backend_id=backend_id,
        model_provider=f"provider-{backend_id}",
        model_id=model or f"model-{backend_id}",
        model_version="2026-09",
        model_config_sha256=SHA0 if backend_id == "a" else SHA1,
    )


class SharedFixture:
    def __init__(self) -> None:
        self.evaluator_targets: list[EvaluationTarget] = []
        self.identities = []
        self.evaluations: list[ParsingEvaluationRecord] = []

    def initialize_skill_context(self, state, operation_id):
        return ("worklist:all-four-passes",)

    def collect_evidence(self, state, token, skill_context, operation_id):
        return (
            EvidenceRecord(
                f"ev-{state.task.task_id}-{token.token_id}",
                "fixture",
                f"fixture:{token.token_id}",
                f"prov:{operation_id}",
                f"evidence for {token.token_id}",
            ),
        )

    def verify_completion(self, state, gate_id, skill_context, operation_id):
        from harness.column_state import CompletionGateResult

        return CompletionGateResult(
            gate_id,
            True,
            state.decision_revision,
            (f"gate:{gate_id}",),
            "passed",
        )

    def evaluate(self, state, skill_context, operation_id, identity, target):
        self.evaluator_targets.append(target)
        self.identities.append(identity)
        record = ParsingEvaluationRecord(
            1,
            identity,
            target,
            state.decision_revision,
            measure_column_behavior(state),
            (),
            (),
            EfficiencyMetrics(
                model_calls=3,
                tool_calls=7,
                retries=0,
                latency_ms=None,
                input_tokens=None,
                output_tokens=None,
                cost=None,
                currency=None,
            ),
            (f"evaluation:{identity.run_id}",),
        )
        self.evaluations.append(record)
        return record


def _shared(fixture: SharedFixture):
    runtime = _runtime()
    return runtime.SharedBenchmarkAdapters(
        initialize_skill_context=fixture.initialize_skill_context,
        collect_evidence=fixture.collect_evidence,
        verify_completion=fixture.verify_completion,
        evaluate=fixture.evaluate,
    )


def _binding(spec, *, contexts: list[dict], decisions: list[tuple[str, str]], fail=False, factory_calls=None):
    runtime = _runtime()

    def factory(model_context):
        contexts.append(model_context)
        if factory_calls is not None:
            factory_calls.append(spec.backend_id)

        def adjudicate(state, token, evidence, skill_context, operation_id, revisit_request=None):
            if fail:
                raise RuntimeError("provider secret should never enter benchmark result")
            decisions.append((spec.backend_id, token.token_id))
            prior = state.latest_decision(token.token_id)
            return TokenDecision(
                f"decision-{state.task.task_id}-{token.token_id}-{len(state.decisions)}",
                token.token_id,
                (f"parse:{spec.backend_id}:{token.token_id}",),
                tuple(item.evidence_id for item in evidence),
                "benchmark fixture decision",
                revisit_of=None if revisit_request is None else prior.decision_id,
            )

        def reconcile(state, skill_context, operation_id):
            if fail:
                raise RuntimeError("provider secret should never enter benchmark result")
            return ReconciliationPlan((), ())

        return runtime.BackendDecisionAdapters(adjudicate=adjudicate, reconcile=reconcile)

    return runtime.BenchmarkBackendBinding(spec=spec, factory=factory)


def _run(case=None, bindings=None, trials=1):
    runtime = _runtime()
    fixture = SharedFixture()
    contexts: list[dict] = []
    decisions: list[tuple[str, str]] = []
    if case is None:
        case = _case()
    if bindings is None:
        bindings = (
            _binding(_spec("a"), contexts=contexts, decisions=decisions),
            _binding(_spec("b"), contexts=contexts, decisions=decisions),
        )
    result = runtime.run_benchmark(
        case,
        bindings,
        shared_adapters=_shared(fixture),
        trials_per_backend=trials,
    )
    return result, fixture, contexts, decisions


def test_case_and_backend_contracts_are_deterministic_and_runtime_free():
    runtime = _runtime()
    case = _case()
    spec = _spec("a")
    assert case.to_json() == case.to_json()
    assert runtime.BenchmarkCase.from_json(case.to_json()) == case
    assert spec.to_json() == spec.to_json()
    assert runtime.BenchmarkBackendSpec.from_json(spec.to_json()) == spec
    serialized = spec.to_json()
    assert "factory" not in serialized
    assert "client" not in serialized


def test_case_rejects_partial_or_resumed_state():
    state = _base_state()
    evidence = EvidenceRecord("ev-partial", "fixture", "fixture:t1", "prov", "evidence")
    partial = apply_column_event(state, EvidenceRecorded("event-evidence", evidence))
    partial = apply_column_event(
        partial,
        TokenReviewed(
            "event-review",
            TokenDecision("decision-partial", "t1", ("parse",), ("ev-partial",), "partial"),
        ),
    )
    with pytest.raises(ValueError, match="pristine|partial|cursor|benchmark"):
        _case(initial_state=partial)


def test_round_robin_schedule_and_duplicate_validation():
    runtime = _runtime()
    a, b, c = _spec("a"), _spec("b"), _spec("c")
    schedule = runtime.build_trial_schedule((a, b, c), 3)
    assert [(item.backend_id, item.trial_index, item.schedule_position) for item in schedule] == [
        ("a", 0, 0), ("b", 0, 1), ("c", 0, 2),
        ("a", 1, 3), ("b", 1, 4), ("c", 1, 5),
        ("a", 2, 6), ("b", 2, 7), ("c", 2, 8),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        runtime.build_trial_schedule((a, a), 1)
    for invalid in (0, -1, True):
        with pytest.raises(ValueError, match="trials"):
            runtime.build_trial_schedule((a,), invalid)


def test_real_column_graph_runs_every_token_and_fresh_trial_identity():
    runtime = _runtime()
    contexts: list[dict] = []
    decisions: list[tuple[str, str]] = []
    factory_calls: list[str] = []
    bindings = (
        _binding(_spec("a"), contexts=contexts, decisions=decisions, factory_calls=factory_calls),
        _binding(_spec("b"), contexts=contexts, decisions=decisions, factory_calls=factory_calls),
    )
    result, fixture, _, _ = _run(bindings=bindings, trials=2)
    assert [(item.backend_id, item.trial_index) for item in result.results] == [
        ("a", 0), ("b", 0), ("a", 1), ("b", 1)
    ]
    assert factory_calls == ["a", "b", "a", "b"]
    assert decisions == [
        ("a", "t1"), ("a", "t2"), ("a", "t3"),
        ("b", "t1"), ("b", "t2"), ("b", "t3"),
        ("a", "t1"), ("a", "t2"), ("a", "t3"),
        ("b", "t1"), ("b", "t2"), ("b", "t3"),
    ]
    run_ids = [item.identity.run_id for item in result.results]
    assert len(run_ids) == len(set(run_ids))
    assert all(item.terminal_status == "completed" for item in result.results)
    assert all(item.final_state.completion is not None for item in result.results)
    assert all(item.evaluation is not None for item in result.results)
    assert len(fixture.evaluations) == 4


def test_backend_factory_receives_only_model_safe_context_and_evaluator_keeps_gold():
    result, fixture, contexts, _ = _run(trials=1)
    assert result.results
    assert len(contexts) == 2
    for context in contexts:
        encoded = json.dumps(context, sort_keys=True)
        assert GOLD_REF not in encoded
        assert "reviewed_ref" not in encoded
        assert "scorer_id" not in encoded
        assert "expert_feedback" not in encoded
        assert set(context) == {"run_id", "workload", "model"}
    assert fixture.evaluator_targets == [_target(), _target()]


def test_backend_failure_is_safe_and_does_not_abort_later_trials():
    contexts: list[dict] = []
    decisions: list[tuple[str, str]] = []
    bindings = (
        _binding(_spec("a"), contexts=contexts, decisions=decisions, fail=True),
        _binding(_spec("b"), contexts=contexts, decisions=decisions),
    )
    result, _, _, _ = _run(bindings=bindings)
    first, second = result.results
    assert first.backend_id == "a"
    assert first.terminal_status == "backend-error"
    assert first.error_type == "RuntimeError"
    assert first.evaluation is None
    assert "provider secret" not in first.to_json()
    assert second.backend_id == "b"
    assert second.terminal_status == "completed"
    assert [token for backend, token in decisions if backend == "b"] == ["t1", "t2", "t3"]


def test_completed_result_binds_exact_evaluation_and_preserves_missing_efficiency():
    result, _, _, _ = _run(bindings=(_binding(_spec("a"), contexts=[], decisions=[]),))
    trial = result.results[0]
    assert trial.evaluation.identity == trial.identity
    assert trial.evaluation.decision_revision == trial.final_state.decision_revision
    assert trial.evaluation.efficiency.latency_ms is None
    assert trial.evaluation.efficiency.input_tokens is None
    assert trial.evaluation.efficiency.output_tokens is None
    assert trial.evaluation.efficiency.cost is None
    serialized = trial.to_json()
    assert '"cost":null' in serialized
    assert '"input_tokens":null' in serialized


def test_model_only_comparison_delegates_to_harn015_and_detects_non_model_drift():
    runtime = _runtime()
    left, _, _, _ = _run(bindings=(_binding(_spec("a"), contexts=[], decisions=[]),))
    right, _, _, _ = _run(bindings=(_binding(_spec("b"), contexts=[], decisions=[]),))
    report = runtime.compare_trials(
        left.results[0], right.results[0], mode=runtime.ComparisonMode.MODEL_ONLY
    )
    assert report.comparable
    assert report.mismatched_dimensions == ()

    drift_case = _case(tool_policy_sha256=SHA0)
    drift, _, _, _ = _run(
        case=drift_case,
        bindings=(_binding(_spec("b"), contexts=[], decisions=[]),),
    )
    report = runtime.compare_trials(
        left.results[0], drift.results[0], mode=runtime.ComparisonMode.MODEL_ONLY
    )
    assert not report.comparable
    assert "tool_policy_sha256" in report.mismatched_dimensions


def test_system_variant_reports_allowed_changes_but_rejects_frozen_or_target_drift():
    runtime = _runtime()
    left, _, _, _ = _run(bindings=(_binding(_spec("a"), contexts=[], decisions=[]),))
    variant_case = _case(tool_policy_sha256=SHA0, evidence_policy_sha256=SHA1)
    variant, _, _, _ = _run(
        case=variant_case,
        bindings=(_binding(_spec("b"), contexts=[], decisions=[]),),
    )
    report = runtime.compare_trials(
        left.results[0], variant.results[0], mode=runtime.ComparisonMode.SYSTEM_VARIANT
    )
    assert report.comparable
    assert "tool_policy_sha256" in report.changed_dimensions
    assert "evidence_policy_sha256" in report.changed_dimensions
    assert "model_provider" in report.changed_dimensions

    permission_case = _case(permission_policy_sha256=SHA0)
    permission, _, _, _ = _run(
        case=permission_case,
        bindings=(_binding(_spec("b"), contexts=[], decisions=[]),),
    )
    rejected = runtime.compare_trials(
        left.results[0], permission.results[0], mode=runtime.ComparisonMode.SYSTEM_VARIANT
    )
    assert not rejected.comparable
    assert "permission_policy_sha256" in rejected.mismatched_dimensions

    target_case = _case(evaluation_target=_target(suffix="-other"))
    target, _, _, _ = _run(
        case=target_case,
        bindings=(_binding(_spec("b"), contexts=[], decisions=[]),),
    )
    rejected = runtime.compare_trials(
        left.results[0], target.results[0], mode=runtime.ComparisonMode.SYSTEM_VARIANT
    )
    assert not rejected.comparable
    assert any(item.startswith("target.") for item in rejected.mismatched_dimensions)


def test_benchmark_core_has_no_provider_or_langfuse_dependency():
    runtime = _runtime()
    source = Path(runtime.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("import openai", "import anthropic", "import google.generativeai", "import langfuse"):
        assert forbidden not in source
