from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import importlib
import inspect
import json
from pathlib import Path

import pytest

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    CompletionGateResult,
    CorpusReconciliationFinding,
    EvidenceRecord,
    ReconciliationScope,
    RevisitRequest,
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


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64


def _load_runtime():
    try:
        return importlib.import_module("harness.langgraph_column_review")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-004 runtime is not implemented yet: {exc}")


def _review_capability():
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    return registry, manifest, provenance


def _state(*, priority: tuple[str, ...] = ()) -> ColumnRunState:
    runtime = _load_runtime()
    _, manifest, provenance = _review_capability()
    task = runtime.build_column_task_from_capability(
        manifest,
        provenance,
        task_id="run-ktu-1.1-i",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="test-revision",
        evidence_priority_token_ids=priority,
    )
    snapshot = ColumnSnapshot(
        snapshot_id="snapshot-ktu-1.1-i",
        source_ref="fixture:ktu-1.1:I",
        source_provenance="fixture-provenance",
        tokens=(
            ColumnToken("t1", 1, "1", "a"),
            ColumnToken("t2", 2, "1", "b"),
            ColumnToken("t3", 3, "2", "c"),
        ),
    )
    return ColumnRunState.initial(task, snapshot)


def _evaluation_for(state: ColumnRunState) -> ParsingEvaluationRecord:
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
        evidence_policy_sha256=SHA1,
        permission_policy_sha256=SHA2,
    )
    identity = ParsingRunIdentity(
        state.task.task_id,
        workload,
        "test-provider",
        "test-model",
        "1",
        SHA3,
    )
    target = EvaluationTarget(
        "fixture-target",
        "reviewed:fixture",
        "reviewed-provenance",
        "score_reviewed_morphology.py",
        "scorer-provenance",
        SHA0,
    )
    return ParsingEvaluationRecord(
        1,
        identity,
        target,
        state.decision_revision,
        measure_column_behavior(state),
        (),
        (),
        EfficiencyMetrics(model_calls=0, tool_calls=0, retries=0),
        ("fixture:evaluation",),
    )


class FakeAdapters:
    def __init__(
        self,
        *,
        revisit_token: str | None = None,
        orphan_required_finding: bool = False,
        failed_gate: str | None = None,
        fail_adjudication_once_for: str | None = None,
    ) -> None:
        self.revisit_token = revisit_token
        self.orphan_required_finding = orphan_required_finding
        self.failed_gate = failed_gate
        self.fail_adjudication_once_for = fail_adjudication_once_for
        self.failed_once = False
        self.initialization_calls: list[str] = []
        self.evidence_calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.adjudication_calls: list[tuple[str, str, str | None]] = []
        self.reconciliation_calls: list[str] = []
        self.gate_calls: list[str] = []
        self.evaluation_calls: list[str] = []
        self.evaluations: list[ParsingEvaluationRecord] = []

    def initialize_skill_context(self, state, operation_id):
        self.initialization_calls.append(operation_id)
        return (
            "fixture:legacy-worklist",
            "fixture:eupt-worklist",
            "fixture:tropper-worklist",
            "fixture:lint-worklist",
        )

    def collect_evidence(self, state, token, operation_id):
        full_column = tuple(item.token_id for item in state.snapshot.tokens)
        self.evidence_calls.append((token.token_id, operation_id, full_column))
        suffix = operation_id.rsplit(":", 1)[-1]
        return (
            EvidenceRecord(
                f"ev-{token.token_id}-{suffix}",
                "fixture-evidence",
                f"fixture:{token.token_id}",
                f"prov:{operation_id}",
                f"Evidence for {token.token_id}",
            ),
        )

    def adjudicate(self, state, token, evidence, operation_id, revisit_request=None):
        request_id = None if revisit_request is None else revisit_request.request_id
        self.adjudication_calls.append((token.token_id, operation_id, request_id))
        if (
            self.fail_adjudication_once_for == token.token_id
            and not self.failed_once
            and revisit_request is None
        ):
            self.failed_once = True
            raise RuntimeError(f"intentional failure at {token.token_id}")
        prior = state.latest_decision(token.token_id)
        return TokenDecision(
            decision_id=f"decision-{token.token_id}-{len(self.adjudication_calls)}",
            token_id=token.token_id,
            analyses=(f"parse:{token.token_id}",),
            evidence_ids=tuple(item.evidence_id for item in evidence),
            summary=f"Reviewed {token.token_id}",
            revisit_of=None if revisit_request is None else prior.decision_id,
        )

    def reconcile(self, state, operation_id):
        self.reconciliation_calls.append(operation_id)
        runtime = _load_runtime()
        if self.orphan_required_finding:
            finding = CorpusReconciliationFinding(
                "finding-orphan",
                ReconciliationScope.COLUMN,
                ("t1",),
                (state.decisions[0].evidence_ids[0],),
                "Requires revisit but no revisit request was provided",
                True,
            )
            return runtime.ReconciliationPlan((finding,), ())
        if self.revisit_token is None:
            return runtime.ReconciliationPlan((), ())
        latest = state.latest_decision(self.revisit_token)
        finding = CorpusReconciliationFinding(
            "finding-revisit",
            ReconciliationScope.COLUMN,
            (self.revisit_token,),
            (latest.evidence_ids[0],),
            "Check this token again",
            True,
        )
        request = RevisitRequest(
            "request-revisit",
            self.revisit_token,
            "Resolve reconciliation finding",
            finding.finding_id,
        )
        return runtime.ReconciliationPlan((finding,), (request,))

    def verify_completion(self, state, gate_id, operation_id):
        self.gate_calls.append(gate_id)
        return CompletionGateResult(
            gate_id,
            gate_id != self.failed_gate,
            state.decision_revision,
            (f"gate-evidence:{gate_id}",),
            f"Checked {gate_id}",
        )

    def evaluate(self, state, operation_id):
        self.evaluation_calls.append(operation_id)
        record = _evaluation_for(state)
        self.evaluations.append(record)
        return record


def _compiled(fake: FakeAdapters):
    runtime = _load_runtime()
    adapters = runtime.ColumnReviewAdapters(
        initialize_skill_context=fake.initialize_skill_context,
        collect_evidence=fake.collect_evidence,
        adjudicate=fake.adjudicate,
        reconcile=fake.reconcile,
        verify_completion=fake.verify_completion,
        evaluate=fake.evaluate,
    )
    return runtime.compile_column_review_graph(adapters)


def _invoke(graph, state: ColumnRunState, *, thread_id: str = "thread-1"):
    runtime = _load_runtime()
    return graph.invoke(
        runtime.initial_graph_input(state),
        config={"configurable": {"thread_id": thread_id}},
    )


def test_capability_binding_derives_identity_and_exact_gate_order():
    runtime = _load_runtime()
    _, manifest, provenance = _review_capability()
    task = runtime.build_column_task_from_capability(
        manifest,
        provenance,
        task_id="task",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="rev",
    )
    expected_digest = sha256(
        json.dumps(
            provenance.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert task.capability.canonical_name == manifest.canonical_name
    assert task.capability.contract_version == manifest.contract_version
    assert task.capability.provenance_sha256 == expected_digest
    assert task.required_completion_gates == manifest.completion_verifiers
    assert "required_completion_gates" not in inspect.signature(
        runtime.build_column_task_from_capability
    ).parameters


def test_capability_binding_rejects_wrong_skill_and_mismatched_provenance():
    runtime = _load_runtime()
    registry, manifest, provenance = _review_capability()
    other = registry.get("regenerate-automatic-parsing")
    other_provenance = registry.provenance("regenerate-automatic-parsing")
    kwargs = dict(
        task_id="task",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="rev",
    )
    with pytest.raises(ValueError, match="review-automatic-parsing"):
        runtime.build_column_task_from_capability(other, other_provenance, **kwargs)
    with pytest.raises(ValueError, match="provenance"):
        runtime.build_column_task_from_capability(
            manifest,
            replace(provenance, contract_version="9.9.9"),
            **kwargs,
        )


def test_graph_reviews_every_token_in_textual_order_with_full_column_context():
    fake = FakeAdapters()
    result = _invoke(_compiled(fake), _state(priority=("t3",)))
    final = result["column_state"]
    assert [item[0] for item in fake.adjudication_calls] == ["t1", "t2", "t3"]
    assert [item[0] for item in fake.evidence_calls] == ["t1", "t2", "t3"]
    assert all(item[2] == ("t1", "t2", "t3") for item in fake.evidence_calls)
    assert fake.initialization_calls == ["run-ktu-1.1-i:column:initialize-skill-context"]
    assert final.initial_pass_complete
    assert final.completion is not None
    assert result["terminal_status"] == "completed"


def test_checkpoint_resume_uses_domain_cursor_and_does_not_repeat_completed_tokens():
    fake = FakeAdapters(fail_adjudication_once_for="t3")
    graph = _compiled(fake)
    state = _state()
    config = {"configurable": {"thread_id": "resume-thread"}}
    runtime = _load_runtime()
    with pytest.raises(RuntimeError, match="intentional failure"):
        graph.invoke(runtime.initial_graph_input(state), config=config)
    result = graph.invoke(None, config=config)
    assert result["column_state"].completion is not None
    assert len(fake.initialization_calls) == 1
    assert [item[0] for item in fake.evidence_calls].count("t1") == 1
    assert [item[0] for item in fake.evidence_calls].count("t2") == 1
    assert [item[0] for item in fake.adjudication_calls].count("t1") == 1
    assert [item[0] for item in fake.adjudication_calls].count("t2") == 1
    assert [item[0] for item in fake.adjudication_calls].count("t3") == 2
    t3_ops = [item[1] for item in fake.adjudication_calls if item[0] == "t3"]
    assert len(set(t3_ops)) == 1


def test_different_checkpoint_thread_starts_a_fresh_column_run():
    fake = FakeAdapters()
    graph = _compiled(fake)
    _invoke(graph, _state(), thread_id="thread-a")
    _invoke(graph, _state(), thread_id="thread-b")
    assert len(fake.initialization_calls) == 2
    assert [item[0] for item in fake.adjudication_calls] == [
        "t1", "t2", "t3", "t1", "t2", "t3"
    ]


def test_reconciliation_revisit_preserves_request_and_prior_decision_provenance():
    fake = FakeAdapters(revisit_token="t1")
    result = _invoke(_compiled(fake), _state())
    final = result["column_state"]
    history = final.decision_history("t1")
    assert len(history) == 2
    assert history[1].revisit_of == history[0].decision_id
    assert history[1].revisit_request_id == "request-revisit"
    assert final.resolved_revisit_request_ids == ("request-revisit",)
    assert [item[0] for item in fake.adjudication_calls] == ["t1", "t2", "t3", "t1"]


def test_required_finding_without_revisit_cannot_false_complete():
    fake = FakeAdapters(orphan_required_finding=True)
    with pytest.raises(Exception, match="revisit"):
        _invoke(_compiled(fake), _state())
    assert not fake.evaluation_calls


def test_completion_verifiers_run_in_manifest_order_and_failure_blocks_evaluation():
    _, manifest, _ = _review_capability()
    fake = FakeAdapters(failed_gate=manifest.completion_verifiers[1])
    result = _invoke(_compiled(fake), _state())
    assert fake.gate_calls == list(manifest.completion_verifiers[:2])
    assert result["terminal_status"] == "gate-failed"
    assert result["column_state"].completion is None
    assert not fake.evaluation_calls


def test_successful_evaluation_is_forwarded_without_recomputation():
    fake = FakeAdapters()
    result = _invoke(_compiled(fake), _state())
    assert len(fake.evaluations) == 1
    assert result["evaluation"] == fake.evaluations[0]
    assert result["evaluation"].artifact_refs == ("fixture:evaluation",)
    assert result["evaluation"].decision_revision == result["column_state"].decision_revision


def test_operation_ids_are_stable_and_phase_specific():
    fake = FakeAdapters(revisit_token="t1")
    _invoke(_compiled(fake), _state())
    all_ids = list(fake.initialization_calls)
    all_ids += [item[1] for item in fake.evidence_calls]
    all_ids += [item[1] for item in fake.adjudication_calls]
    all_ids += fake.reconciliation_calls + fake.evaluation_calls
    assert len(all_ids) == len(set(all_ids))
    assert any(":column:initialize-skill-context" in value for value in all_ids)
    assert any(":initial:t1:evidence" in value for value in all_ids)
    assert any(":revisit:request-revisit:evidence" in value for value in all_ids)


def test_deterministic_domain_modules_remain_langgraph_free():
    for relative in (
        "agent/harness/column_state.py",
        "agent/harness/skill_capabilities.py",
        "agent/harness/parsing_evaluation.py",
        "agent/harness/_parsing_evaluation_core.py",
    ):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "langgraph" not in text.lower(), relative
