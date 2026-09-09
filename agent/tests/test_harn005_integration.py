from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from harness import langfuse_sidecar as sidecar_api
from harness import telemetry
from harness.column_state import (
    CapabilityRef,
    ColumnRunState,
    ColumnSnapshot,
    ColumnTask,
    ColumnToken,
    CompletionGateResult,
    EvidenceRecord,
    TokenDecision,
)
from harness.contracts import RunState, TaskSpec
from harness.langgraph_column_review import ColumnReviewAdapters, ReconciliationPlan
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
    measure_column_behavior,
)


SHA = "1" * 64


def _column_state() -> ColumnRunState:
    task = ColumnTask(
        "parse-run",
        "CUC",
        "KTU 1.1",
        "I",
        "repo-rev",
        CapabilityRef("review-automatic-parsing", "1.0.0", SHA),
        ("review-status-clean",),
    )
    snapshot = ColumnSnapshot(
        "snapshot",
        "fixture:column",
        "snapshot-prov",
        (ColumnToken("t1", 1, "1", "surface-must-not-be-exported"),),
    )
    return ColumnRunState.initial(task, snapshot)


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
        tool_policy_sha256=SHA,
        evidence_policy_sha256=SHA,
        permission_policy_sha256=SHA,
    )
    return ParsingEvaluationRecord(
        1,
        ParsingRunIdentity("parse-run", workload, "provider", "model", "v1", SHA),
        EvaluationTarget(
            "target",
            "reviewed:held-out",
            "held-out-prov",
            "score_reviewed_morphology.py",
            "scorer-prov",
            SHA,
        ),
        state.decision_revision,
        measure_column_behavior(state),
        (),
        (),
        EfficiencyMetrics(model_calls=0, tool_calls=0, retries=0),
        ("artifact:evaluation",),
    )


@dataclass
class RecordingSidecar:
    fail: bool = False

    def __post_init__(self) -> None:
        self.observations = []
        self.scores = []
        self.traces = []

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("telemetry-only failure")

    def emit_observation(self, projection):
        self._maybe_fail()
        self.observations.append(projection)
        return telemetry.TelemetryOutcome(True, True)

    def emit_score(self, projection):
        self._maybe_fail()
        self.scores.append(projection)
        return telemetry.TelemetryOutcome(True, True)

    def emit_trace(self, projection):
        self._maybe_fail()
        self.traces.append(projection)
        return telemetry.TelemetryOutcome(True, True)


def _adapters(calls: list[tuple[str, str]]):
    def initialize(state, operation_id):
        calls.append(("initialize", operation_id))
        return ("worklist:all-four-passes",)

    def evidence(state, token, skill_context, operation_id):
        calls.append(("evidence", operation_id))
        return (EvidenceRecord("ev1", "fixture", "fixture:t1", "prov", "secret summary"),)

    def adjudicate(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls.append(("adjudicate", operation_id))
        return TokenDecision("d1", token.token_id, ("secret-analysis",), ("ev1",), "secret decision")

    def reconcile(state, skill_context, operation_id):
        calls.append(("reconcile", operation_id))
        return ReconciliationPlan((), ())

    def gate(state, gate_id, skill_context, operation_id):
        calls.append(("gate", operation_id))
        return CompletionGateResult(gate_id, True, state.decision_revision, ("gate:ref",), "ok")

    def evaluate(state, skill_context, operation_id):
        calls.append(("evaluate", operation_id))
        return _evaluation(state)

    return ColumnReviewAdapters(initialize, evidence, adjudicate, reconcile, gate, evaluate)


def test_wrapper_preserves_domain_calls_and_operation_ids_while_telemetry_is_best_effort():
    if not hasattr(sidecar_api, "wrap_column_review_adapters"):
        pytest.fail("HARN-005 requires wrap_column_review_adapters")

    state = _column_state()
    token = state.snapshot.tokens[0]
    calls: list[tuple[str, str]] = []
    wrapped = sidecar_api.wrap_column_review_adapters(_adapters(calls), RecordingSidecar(fail=True))

    context = wrapped.initialize_skill_context(state, "op:init")
    evidence = wrapped.collect_evidence(state, token, context, "op:evidence")
    decision = wrapped.adjudicate(state, token, evidence, context, "op:adjudicate", None)
    plan = wrapped.reconcile(state, context, "op:reconcile")
    gate = wrapped.verify_completion(state, "review-status-clean", context, "op:gate")
    evaluation = wrapped.evaluate(state, context, "op:evaluate")

    assert context == ("worklist:all-four-passes",)
    assert evidence[0].evidence_id == "ev1"
    assert decision.decision_id == "d1"
    assert plan == ReconciliationPlan((), ())
    assert gate.passed is True
    assert isinstance(evaluation, ParsingEvaluationRecord)
    assert calls == [
        ("initialize", "op:init"),
        ("evidence", "op:evidence"),
        ("adjudicate", "op:adjudicate"),
        ("reconcile", "op:reconcile"),
        ("gate", "op:gate"),
        ("evaluate", "op:evaluate"),
    ]


def test_failed_domain_operation_is_observed_without_masking_or_leaking_exception():
    state = _column_state()
    token = state.snapshot.tokens[0]
    sink = RecordingSidecar()
    sentinel = RuntimeError("RAW-FAILURE-MESSAGE-MUST-NOT-LEAK")

    def fail_adjudicate(
        state,
        token,
        evidence_records,
        skill_context,
        operation_id,
        revisit_request=None,
    ):
        raise sentinel

    wrapped = sidecar_api.wrap_column_review_adapters(
        replace(_adapters([]), adjudicate=fail_adjudicate),
        sink,
    )

    with pytest.raises(RuntimeError) as caught:
        wrapped.adjudicate(state, token, (), ("worklist:all-four-passes",), "op:failed", None)

    assert caught.value is sentinel
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.operation_id == "op:failed"
    assert observation.name == "cuc.parsing.adjudicate"
    assert observation.metadata["outcome"] == "error"
    assert observation.metadata["error_type"] == "RuntimeError"
    assert observation.metadata["corpus"] == "CUC"
    assert observation.metadata["tablet"] == "KTU 1.1"
    assert observation.metadata["column"] == "I"
    assert observation.metadata["repository_revision"] == "repo-rev"
    assert observation.metadata["capability_name"] == "review-automatic-parsing"
    assert "RAW-FAILURE-MESSAGE-MUST-NOT-LEAK" not in repr(observation.metadata)


def test_final_evaluation_exports_one_bound_parsing_root_trace():
    state = _column_state()
    calls: list[tuple[str, str]] = []
    sink = RecordingSidecar()
    wrapped = sidecar_api.wrap_column_review_adapters(_adapters(calls), sink)

    result = wrapped.evaluate(state, ("worklist:all-four-passes",), "op:evaluate")

    assert isinstance(result, ParsingEvaluationRecord)
    assert len(sink.traces) == 1
    trace = sink.traces[0]
    assert trace.run_type is telemetry.TelemetryRunType.PARSING
    assert trace.trace_name == "cuc.parsing.column-review"
    assert trace.run_id == state.task.task_id
    assert trace.metadata["model_provider"] == "provider"
    assert trace.metadata["model_id"] == "model"
    assert trace.metadata["model_version"] == "v1"
    assert trace.metadata["tool_policy_sha256"] == SHA
    assert trace.metadata["evidence_policy_sha256"] == SHA
    assert trace.metadata["permission_policy_sha256"] == SHA
    assert trace.metadata["evaluation_artifact_refs"] == ("artifact:evaluation",)
    assert "surface-must-not-be-exported" not in repr(trace.metadata)


def test_wrapper_propagates_pure_projection_contract_errors():
    state = _column_state()
    bad = _evaluation(state)
    bad = replace(bad, identity=replace(bad.identity, run_id="wrong-run"))
    adapters = replace(
        _adapters([]),
        evaluate=lambda state, skill_context, operation_id: bad,
    )
    wrapped = sidecar_api.wrap_column_review_adapters(adapters, RecordingSidecar())

    with pytest.raises(ValueError, match="evaluation does not match column state"):
        wrapped.evaluate(state, ("worklist:all-four-passes",), "op:evaluate")


def test_development_emitter_is_best_effort_and_uses_development_projection():
    if not hasattr(sidecar_api, "emit_development_run"):
        pytest.fail("HARN-005 requires emit_development_run")

    state = RunState(
        "dev-run",
        TaskSpec("HARN-005", "Telemetry", "Observe development", ("optional",)),
    )
    context = telemetry.DevelopmentTraceContext(
        "alexsosn/cuc",
        "#6",
        "agent-harness-safety",
        "harn-005-langfuse-sidecar",
        "head",
        None,
    )
    sink = RecordingSidecar()
    outcome = sidecar_api.emit_development_run(state, context, sink)

    assert outcome.delivered is True
    assert len(sink.traces) == 1
    assert sink.traces[0].run_type is telemetry.TelemetryRunType.DEVELOPMENT
    assert sink.traces[0].trace_name == "cuc.development.run"

    failing = RecordingSidecar(fail=True)
    outcome = sidecar_api.emit_development_run(state, context, failing)
    assert outcome.delivered is False


def test_default_client_factory_exports_only_explicit_langfuse_spans(monkeypatch):
    sentinel_filter = object()
    captured = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    real_import = sidecar_api.importlib.import_module

    def fake_import(name):
        if name == "langfuse":
            return SimpleNamespace(Langfuse=FakeLangfuse)
        if name == "langfuse.span_filter":
            return SimpleNamespace(is_langfuse_span=sentinel_filter)
        return real_import(name)

    monkeypatch.setattr(sidecar_api.importlib, "import_module", fake_import)
    sidecar_api._default_client_factory(public_key="pk", secret_key="sk")

    assert captured["should_export_span"] is sentinel_filter
