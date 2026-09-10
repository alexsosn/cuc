from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    CompletionGateResult,
    EvidenceRecord,
    TokenDecision,
)
from harness.langgraph_column_review import ReconciliationPlan, build_column_task_from_capability
from harness.model_benchmark import (
    BackendDecisionAdapters,
    BenchmarkBackendBinding,
    BenchmarkBackendSpec,
    BenchmarkCase,
    ComparisonMode,
    SharedBenchmarkAdapters,
    compare_trials,
    run_benchmark,
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


def _state() -> ColumnRunState:
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
    )
    snapshot = ColumnSnapshot(
        "snapshot-id",
        "fixture:snapshot",
        "declared-provenance",
        (
            ColumnToken("t1", 1, "1", "a"),
            ColumnToken("t2", 2, "1", "b"),
        ),
    )
    return ColumnRunState.initial(task, snapshot)


def _case(state: ColumnRunState | None = None) -> BenchmarkCase:
    return BenchmarkCase(
        1,
        "stable-case",
        state or _state(),
        SHA1,
        SHA2,
        SHA3,
        EvaluationTarget(
            "target",
            "reviewed:fixture",
            "reviewed-provenance",
            "score_reviewed_morphology.py",
            "scorer-provenance",
            SHA0,
        ),
    )


def _spec(backend_id: str = "backend", *, version: str = "v1") -> BenchmarkBackendSpec:
    return BenchmarkBackendSpec(
        backend_id,
        "fixture-provider",
        "fixture-model",
        version,
        SHA0,
    )


def _shared() -> SharedBenchmarkAdapters:
    def initialize_skill_context(state, operation_id):
        return ("all-worklist-passes",)

    def collect_evidence(state, token, skill_context, operation_id):
        return (
            EvidenceRecord(
                f"ev-{state.task.task_id}-{token.token_id}",
                "fixture",
                f"fixture:{token.token_id}",
                f"prov:{operation_id}",
                "evidence",
            ),
        )

    def verify_completion(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(
            gate_id,
            True,
            state.decision_revision,
            (f"gate:{gate_id}",),
            "passed",
        )

    def evaluate(state, skill_context, operation_id, identity, target):
        return ParsingEvaluationRecord(
            1,
            identity,
            target,
            state.decision_revision,
            measure_column_behavior(state),
            (),
            (),
            EfficiencyMetrics(),
            (f"evaluation:{identity.run_id}",),
        )

    return SharedBenchmarkAdapters(
        initialize_skill_context,
        collect_evidence,
        verify_completion,
        evaluate,
    )


def _binding(spec: BenchmarkBackendSpec) -> BenchmarkBackendBinding:
    def factory(model_context):
        def adjudicate(state, token, evidence, skill_context, operation_id, revisit_request=None):
            prior = state.latest_decision(token.token_id)
            return TokenDecision(
                f"decision-{state.task.task_id}-{token.token_id}-{len(state.decisions)}",
                token.token_id,
                (f"parse:{token.surface}",),
                tuple(item.evidence_id for item in evidence),
                "fixture decision",
                revisit_of=None if revisit_request is None else prior.decision_id,
            )

        def reconcile(state, skill_context, operation_id):
            return ReconciliationPlan((), ())

        return BackendDecisionAdapters(adjudicate, reconcile)

    return BenchmarkBackendBinding(spec, factory)


def _run(case: BenchmarkCase):
    return run_benchmark(
        case,
        (_binding(_spec()),),
        shared_adapters=_shared(),
        trials_per_backend=1,
    ).results[0]


def test_malformed_backend_factory_result_is_isolated_and_matrix_continues() -> None:
    bad = _spec("bad")
    good = _spec("good", version="v2")

    def malformed_factory(model_context):
        return object()

    result = run_benchmark(
        _case(),
        (
            BenchmarkBackendBinding(bad, malformed_factory),
            _binding(good),
        ),
        shared_adapters=_shared(),
        trials_per_backend=1,
    )

    assert [item.backend_id for item in result.results] == ["bad", "good"]
    assert result.results[0].terminal_status == "backend-error"
    assert result.results[0].error_type == "ValueError"
    assert result.results[1].terminal_status == "completed"


def test_priority_hints_are_part_of_run_identity_and_model_only_comparability() -> None:
    baseline_state = _state()
    changed_task = replace(
        baseline_state.task,
        evidence_priority_token_ids=("t2",),
    )
    changed_state = ColumnRunState.initial(changed_task, baseline_state.snapshot)

    baseline = _run(_case(baseline_state))
    changed = _run(_case(changed_state))

    assert baseline.identity.run_id != changed.identity.run_id
    report = compare_trials(baseline, changed, mode=ComparisonMode.MODEL_ONLY)
    assert not report.comparable
    assert "evidence_priority_token_ids" in report.mismatched_dimensions


def test_exact_snapshot_content_is_bound_even_if_declared_ids_are_reused() -> None:
    baseline_state = _state()
    changed_snapshot = replace(
        baseline_state.snapshot,
        tokens=(
            ColumnToken("t1", 1, "1", "changed"),
            ColumnToken("t2", 2, "1", "b"),
        ),
    )
    changed_state = ColumnRunState.initial(baseline_state.task, changed_snapshot)

    baseline = _run(_case(baseline_state))
    changed = _run(_case(changed_state))

    assert baseline.identity.run_id != changed.identity.run_id
    report = compare_trials(baseline, changed, mode=ComparisonMode.MODEL_ONLY)
    assert not report.comparable
    assert "snapshot_content" in report.mismatched_dimensions
