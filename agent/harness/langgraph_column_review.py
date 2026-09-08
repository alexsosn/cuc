"""LangGraph orchestration for the skill-defined complete-column review workflow.

The durable scholarly state remains :mod:`harness.column_state`; this module owns only
runtime routing, controlled-effect adapters, and checkpoint boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .column_state import (
    CapabilityRef,
    ColumnCompleted,
    ColumnRunState,
    ColumnTask,
    CompletionGateRecorded,
    EvidenceRecord,
    EvidenceRecorded,
    ReconciliationClosed,
    ReconciliationFindingRecorded,
    RevisitRequested,
    RevisitRequest,
    TokenDecision,
    TokenReviewed,
    TokenRevisited,
    apply_column_event,
)
from .skill_capabilities import SkillCapabilityManifest, SkillProvenance


_REVIEW_SKILL = "review-automatic-parsing"
_DEFAULT_RECURSION_LIMIT = 10_000


@dataclass(frozen=True)
class ReconciliationPlan:
    """Deterministic reconciliation output supplied by the scholarly adapter."""

    findings: tuple[Any, ...]
    revisit_requests: tuple[RevisitRequest, ...]

    def __post_init__(self) -> None:
        from .column_state import CorpusReconciliationFinding

        if any(not isinstance(item, CorpusReconciliationFinding) for item in self.findings):
            raise ValueError("findings must contain CorpusReconciliationFinding values")
        if any(not isinstance(item, RevisitRequest) for item in self.revisit_requests):
            raise ValueError("revisit_requests must contain RevisitRequest values")


@dataclass(frozen=True)
class ColumnReviewAdapters:
    """Narrow side-effect boundary used by the orchestration graph."""

    collect_evidence: Callable[..., tuple[EvidenceRecord, ...]]
    adjudicate: Callable[..., TokenDecision]
    reconcile: Callable[..., ReconciliationPlan]
    verify_completion: Callable[..., Any]
    evaluate: Callable[..., Any]


class _GraphState(TypedDict, total=False):
    column_state: ColumnRunState
    pending_evidence: tuple[EvidenceRecord, ...]
    evaluation: Any
    terminal_status: str


def _provenance_digest(provenance: SkillProvenance) -> str:
    encoded = json.dumps(
        provenance.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_column_task_from_capability(
    manifest: SkillCapabilityManifest,
    provenance: SkillProvenance,
    *,
    task_id: str,
    corpus: str,
    tablet: str,
    column: str,
    repository_revision: str,
    evidence_priority_token_ids: tuple[str, ...] = (),
) -> ColumnTask:
    """Bind a parsing task to the exact authenticated review capability.

    Completion gates are deliberately not a caller parameter: they are copied verbatim
    from the loaded capability manifest so orchestration/model output cannot weaken them.
    """

    if not isinstance(manifest, SkillCapabilityManifest):
        raise ValueError("manifest must be SkillCapabilityManifest")
    if not isinstance(provenance, SkillProvenance):
        raise ValueError("provenance must be SkillProvenance")
    if manifest.canonical_name != _REVIEW_SKILL:
        raise ValueError(f"column review requires {_REVIEW_SKILL!r} capability")
    if manifest.work_unit != "column":
        raise ValueError("review-automatic-parsing capability must use column work unit")
    if (
        provenance.canonical_name != manifest.canonical_name
        or provenance.contract_version != manifest.contract_version
    ):
        raise ValueError("capability provenance identity does not match manifest")

    capability = CapabilityRef(
        canonical_name=manifest.canonical_name,
        contract_version=manifest.contract_version,
        provenance_sha256=_provenance_digest(provenance),
    )
    return ColumnTask(
        task_id=task_id,
        corpus=corpus,
        tablet=tablet,
        column=column,
        repository_revision=repository_revision,
        capability=capability,
        required_completion_gates=manifest.completion_verifiers,
        evidence_priority_token_ids=evidence_priority_token_ids,
    )


def initial_graph_input(state: ColumnRunState) -> _GraphState:
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    return {
        "column_state": state,
        "pending_evidence": (),
        "evaluation": None,
        "terminal_status": "running",
    }


def _token_by_id(state: ColumnRunState, token_id: str):
    return next(token for token in state.snapshot.tokens if token.token_id == token_id)


def _record_evidence(
    state: ColumnRunState,
    evidence: tuple[EvidenceRecord, ...],
    operation_id: str,
) -> ColumnRunState:
    if not evidence:
        raise ValueError("evidence adapter must return at least one evidence record")
    updated = state
    for item in evidence:
        if not isinstance(item, EvidenceRecord):
            raise ValueError("evidence adapter returned a non-EvidenceRecord value")
        updated = apply_column_event(
            updated,
            EvidenceRecorded(f"{operation_id}:record:{item.evidence_id}", item),
        )
    return updated


def compile_column_review_graph(adapters: ColumnReviewAdapters):
    """Compile the complete-column Graph API slice with an in-memory checkpointer."""

    if not isinstance(adapters, ColumnReviewAdapters):
        raise ValueError("adapters must be ColumnReviewAdapters")

    def initial_evidence(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        token_id = state.next_token_id
        if token_id is None:
            raise ValueError("initial evidence requested after complete traversal")
        token = _token_by_id(state, token_id)
        # The final segment is phase-specific so simplistic fixture evidence IDs remain
        # unique across initial and revisit collection while the operation prefix stays
        # stable and inspectable.
        operation_id = f"{state.task.task_id}:initial:{token_id}:evidence:initial"
        evidence = tuple(adapters.collect_evidence(state, token, operation_id))
        updated = _record_evidence(state, evidence, operation_id)
        return {"column_state": updated, "pending_evidence": evidence}

    def initial_adjudicate(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        token_id = state.next_token_id
        if token_id is None:
            raise ValueError("initial adjudication requested after complete traversal")
        token = _token_by_id(state, token_id)
        evidence = tuple(graph_state.get("pending_evidence", ()))
        operation_id = f"{state.task.task_id}:initial:{token_id}:adjudicate"
        decision = adapters.adjudicate(state, token, evidence, operation_id, None)
        if not isinstance(decision, TokenDecision):
            raise ValueError("adjudication adapter must return TokenDecision")
        updated = apply_column_event(state, TokenReviewed(operation_id, decision))
        return {"column_state": updated, "pending_evidence": ()}

    def after_initial(graph_state: _GraphState) -> str:
        return "reconcile" if graph_state["column_state"].initial_pass_complete else "initial_evidence"

    def reconcile(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        operation_id = f"{state.task.task_id}:column:reconcile"
        plan = adapters.reconcile(state, operation_id)
        if not isinstance(plan, ReconciliationPlan):
            raise ValueError("reconciliation adapter must return ReconciliationPlan")
        updated = state
        for finding in plan.findings:
            updated = apply_column_event(
                updated,
                ReconciliationFindingRecorded(
                    f"{operation_id}:finding:{finding.finding_id}", finding
                ),
            )
        for request in plan.revisit_requests:
            updated = apply_column_event(
                updated,
                RevisitRequested(f"{operation_id}:request:{request.request_id}", request),
            )
        return {"column_state": updated}

    def after_reconcile(graph_state: _GraphState) -> str:
        return "revisit_evidence" if graph_state["column_state"].unresolved_revisits else "close_reconciliation"

    def revisit_evidence(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        if not state.unresolved_revisits:
            raise ValueError("revisit evidence requested with no unresolved revisit")
        request = state.unresolved_revisits[0]
        token = _token_by_id(state, request.token_id)
        operation_id = (
            f"{state.task.task_id}:revisit:{request.request_id}:evidence:{request.request_id}"
        )
        evidence = tuple(adapters.collect_evidence(state, token, operation_id))
        updated = _record_evidence(state, evidence, operation_id)
        return {"column_state": updated, "pending_evidence": evidence}

    def revisit_adjudicate(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        if not state.unresolved_revisits:
            raise ValueError("revisit adjudication requested with no unresolved revisit")
        request = state.unresolved_revisits[0]
        token = _token_by_id(state, request.token_id)
        evidence = tuple(graph_state.get("pending_evidence", ()))
        operation_id = f"{state.task.task_id}:revisit:{request.request_id}:adjudicate"
        decision = adapters.adjudicate(state, token, evidence, operation_id, request)
        if not isinstance(decision, TokenDecision):
            raise ValueError("adjudication adapter must return TokenDecision")
        updated = apply_column_event(
            state,
            TokenRevisited(operation_id, request.request_id, decision),
        )
        return {"column_state": updated, "pending_evidence": ()}

    def after_revisit(graph_state: _GraphState) -> str:
        return "revisit_evidence" if graph_state["column_state"].unresolved_revisits else "close_reconciliation"

    def close_reconciliation(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        operation_id = f"{state.task.task_id}:column:reconciliation-closed"
        updated = apply_column_event(state, ReconciliationClosed(operation_id))
        return {"column_state": updated}

    def completion_gate(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        current = {
            item.gate_id: item
            for item in state.gate_results
            if item.decision_revision == state.decision_revision
        }
        gate_id = next(
            (gate for gate in state.task.required_completion_gates if gate not in current),
            None,
        )
        if gate_id is None:
            return {"column_state": state}
        operation_id = f"{state.task.task_id}:gate:{gate_id}"
        result = adapters.verify_completion(state, gate_id, operation_id)
        updated = apply_column_event(
            state,
            CompletionGateRecorded(operation_id, result),
        )
        if not result.passed:
            return {"column_state": updated, "terminal_status": "gate-failed"}
        return {"column_state": updated}

    def after_gate(graph_state: _GraphState) -> str:
        if graph_state.get("terminal_status") == "gate-failed":
            return "end"
        state = graph_state["column_state"]
        current_passed = {
            item.gate_id
            for item in state.gate_results
            if item.decision_revision == state.decision_revision and item.passed
        }
        if all(gate in current_passed for gate in state.task.required_completion_gates):
            return "complete"
        return "completion_gate"

    def complete(graph_state: _GraphState) -> _GraphState:
        state = graph_state["column_state"]
        completion_id = f"{state.task.task_id}:column:completed"
        completed = apply_column_event(state, ColumnCompleted(completion_id))
        operation_id = f"{state.task.task_id}:column:evaluate"
        evaluation = adapters.evaluate(completed, operation_id)
        return {
            "column_state": completed,
            "evaluation": evaluation,
            "terminal_status": "completed",
        }

    builder = StateGraph(_GraphState)
    builder.add_node("initial_evidence", initial_evidence)
    builder.add_node("initial_adjudicate", initial_adjudicate)
    builder.add_node("reconcile", reconcile)
    builder.add_node("revisit_evidence", revisit_evidence)
    builder.add_node("revisit_adjudicate", revisit_adjudicate)
    builder.add_node("close_reconciliation", close_reconciliation)
    builder.add_node("completion_gate", completion_gate)
    builder.add_node("complete", complete)

    builder.add_edge(START, "initial_evidence")
    builder.add_edge("initial_evidence", "initial_adjudicate")
    builder.add_conditional_edges(
        "initial_adjudicate",
        after_initial,
        {"initial_evidence": "initial_evidence", "reconcile": "reconcile"},
    )
    builder.add_conditional_edges(
        "reconcile",
        after_reconcile,
        {
            "revisit_evidence": "revisit_evidence",
            "close_reconciliation": "close_reconciliation",
        },
    )
    builder.add_edge("revisit_evidence", "revisit_adjudicate")
    builder.add_conditional_edges(
        "revisit_adjudicate",
        after_revisit,
        {
            "revisit_evidence": "revisit_evidence",
            "close_reconciliation": "close_reconciliation",
        },
    )
    builder.add_edge("close_reconciliation", "completion_gate")
    builder.add_conditional_edges(
        "completion_gate",
        after_gate,
        {"completion_gate": "completion_gate", "complete": "complete", "end": END},
    )
    builder.add_edge("complete", END)

    compiled = builder.compile(checkpointer=InMemorySaver())

    class _BoundedGraph:
        def invoke(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any):
            effective = dict(config or {})
            effective.setdefault("recursion_limit", _DEFAULT_RECURSION_LIMIT)
            return compiled.invoke(input, config=effective, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(compiled, name)

    return _BoundedGraph()
