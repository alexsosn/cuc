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
    SharedBenchmarkAdapters,
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
SHA4 = "4" * 64


def _initial_state() -> ColumnRunState:
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
        repository_revision="review-fixture-revision",
    )
    snapshot = ColumnSnapshot(
        "review-benchmark-snapshot",
        "fixture:review-benchmark",
        "review-benchmark-provenance",
        (
            ColumnToken("t1", 1, "1", "a"),
            ColumnToken("t2", 2, "1", "b"),
        ),
    )
    return ColumnRunState.initial(task, snapshot)


def _case(*, tool_policy: str = SHA1) -> BenchmarkCase:
    return BenchmarkCase(
        1,
        "stable-case-id",
        _initial_state(),
        tool_policy,
        SHA2,
        SHA3,
        EvaluationTarget(
            "review-target",
            "reviewed:fixture",
            "reviewed-provenance",
            "score_reviewed_morphology.py",
            "scorer-provenance",
            SHA0,
        ),
    )


def _spec(*, version: str = "v1", config: str = SHA0) -> BenchmarkBackendSpec:
    return BenchmarkBackendSpec(
        "stable-backend-id",
        "fixture-provider",
        "fixture-model",
        version,
        config,
    )


def _shared() -> SharedBenchmarkAdapters:
    def initialize_skill_context(state, operation_id):
        return ("worklist",)

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


def _binding(
    spec: BenchmarkBackendSpec,
    *,
    malformed: bool = False,
    fail_on: str | None = None,
) -> BenchmarkBackendBinding:
    def factory(model_context):
        def adjudicate(state, token, evidence, skill_context, operation_id, revisit_request=None):
            if fail_on == token.token_id:
                raise RuntimeError("backend-private failure text")
            if malformed:
                return object()
            prior = state.latest_decision(token.token_id)
            return TokenDecision(
                f"decision-{state.task.task_id}-{token.token_id}-{len(state.decisions)}",
                token.token_id,
                (f"parse:{token.token_id}",),
                tuple(item.evidence_id for item in evidence),
                "fixture decision",
                revisit_of=None if revisit_request is None else prior.decision_id,
            )

        def reconcile(state, skill_context, operation_id):
            return ReconciliationPlan((), ())

        return BackendDecisionAdapters(adjudicate, reconcile)

    return BenchmarkBackendBinding(spec, factory)


def _single_run(case: BenchmarkCase, spec: BenchmarkBackendSpec):
    return run_benchmark(
        case,
        (_binding(spec),),
        shared_adapters=_shared(),
        trials_per_backend=1,
    ).results[0]


def test_run_id_changes_when_backend_or_workload_identity_changes() -> None:
    baseline = _single_run(_case(), _spec())
    model_changed = _single_run(_case(), _spec(version="v2", config=SHA4))
    workload_changed = _single_run(_case(tool_policy=SHA4), _spec())

    assert baseline.identity.run_id != model_changed.identity.run_id
    assert baseline.identity.run_id != workload_changed.identity.run_id


def test_malformed_backend_output_isolated_and_later_backend_still_runs() -> None:
    bad_spec = replace(_spec(), backend_id="bad")
    good_spec = replace(_spec(), backend_id="good", model_version="v2")
    result = run_benchmark(
        _case(),
        (
            _binding(bad_spec, malformed=True),
            _binding(good_spec),
        ),
        shared_adapters=_shared(),
        trials_per_backend=1,
    )

    assert [item.backend_id for item in result.results] == ["bad", "good"]
    assert result.results[0].terminal_status == "backend-error"
    assert result.results[0].error_type == "ValueError"
    assert result.results[1].terminal_status == "completed"


def test_backend_error_retains_checkpointed_partial_column_state() -> None:
    trial = run_benchmark(
        _case(),
        (_binding(_spec(), fail_on="t2"),),
        shared_adapters=_shared(),
        trials_per_backend=1,
    ).results[0]

    assert trial.terminal_status == "backend-error"
    assert trial.final_state is not None
    assert trial.final_state.cursor.next_index == 1
    assert [item.token_id for item in trial.final_state.initial_decisions] == ["t1"]
    assert trial.evaluation is None
    assert "backend-private failure text" not in trial.to_json()
