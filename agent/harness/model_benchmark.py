"""Provider-neutral benchmarking for complete-column parsing backends.

HARN-004 remains the execution engine and HARN-015 remains the evaluation and
comparability authority. This module freezes benchmark inputs, schedules fresh
trials, composes adapters, and records results; it does not implement morphology
scoring or provider clients.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping

from .column_state import (
    ColumnRunState,
    InvalidColumnTransition,
    ReconciliationFindingRecorded,
    RevisitRequested,
    TokenDecision,
    TokenReviewed,
    TokenRevisited,
    apply_column_event,
)
from .langgraph_column_review import (
    ColumnReviewAdapters,
    ReconciliationPlan,
    compile_column_review_graph,
    initial_graph_input,
)
from .parsing_evaluation import (
    ComparabilityReport,
    EvaluationTarget,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
    compare_evaluation_records,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROTOCOL_VERSION = 1


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256(value: object, field: str) -> str:
    digest = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return digest


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _json_dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(payload: str, field: str) -> Mapping[str, Any]:
    if not isinstance(payload, str):
        raise ValueError(f"{field} must be a string")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    return _mapping(decoded, field)


def _is_pristine(state: ColumnRunState) -> bool:
    return (
        state.cursor.next_index == 0
        and not state.evidence
        and not state.decisions
        and not state.revisit_queue
        and not state.resolved_revisit_request_ids
        and not state.reconciliation_findings
        and not state.gate_results
        and not state.reconciliation_closed
        and state.decision_revision == 0
        and state.completion is None
        and not state.event_receipts
    )


@dataclass(frozen=True)
class BenchmarkCase:
    protocol_version: int
    case_id: str
    initial_state: ColumnRunState
    tool_policy_sha256: str
    evidence_policy_sha256: str
    permission_policy_sha256: str
    evaluation_target: EvaluationTarget

    def __post_init__(self) -> None:
        if (
            isinstance(self.protocol_version, bool)
            or not isinstance(self.protocol_version, int)
            or self.protocol_version != _PROTOCOL_VERSION
        ):
            raise ValueError(f"protocol_version must be {_PROTOCOL_VERSION}")
        if not isinstance(self.initial_state, ColumnRunState):
            raise ValueError("initial_state must be ColumnRunState")
        if not _is_pristine(self.initial_state):
            raise ValueError(
                "benchmark initial_state must be pristine; partial/resumed state is not comparable"
            )
        if not isinstance(self.evaluation_target, EvaluationTarget):
            raise ValueError("evaluation_target must be EvaluationTarget")
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        for field in (
            "tool_policy_sha256",
            "evidence_policy_sha256",
            "permission_policy_sha256",
        ):
            object.__setattr__(self, field, _sha256(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "case_id": self.case_id,
            "initial_state": self.initial_state.to_dict(),
            "tool_policy_sha256": self.tool_policy_sha256,
            "evidence_policy_sha256": self.evidence_policy_sha256,
            "permission_policy_sha256": self.permission_policy_sha256,
            "evaluation_target": self.evaluation_target.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BenchmarkCase":
        p = _mapping(payload, "BenchmarkCase payload")
        return cls(
            p["protocol_version"],
            p["case_id"],
            ColumnRunState.from_dict(p["initial_state"]),
            p["tool_policy_sha256"],
            p["evidence_policy_sha256"],
            p["permission_policy_sha256"],
            EvaluationTarget.from_dict(p["evaluation_target"]),
        )

    def to_json(self) -> str:
        return _json_dump(self.to_dict())

    @classmethod
    def from_json(cls, payload: str) -> "BenchmarkCase":
        return cls.from_dict(_json_load(payload, "BenchmarkCase JSON"))


@dataclass(frozen=True)
class BenchmarkBackendSpec:
    backend_id: str
    model_provider: str
    model_id: str
    model_version: str
    model_config_sha256: str

    def __post_init__(self) -> None:
        for field in ("backend_id", "model_provider", "model_id", "model_version"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(
            self,
            "model_config_sha256",
            _sha256(self.model_config_sha256, "model_config_sha256"),
        )

    @property
    def model_identity(self) -> tuple[str, str, str, str]:
        return (
            self.model_provider,
            self.model_id,
            self.model_version,
            self.model_config_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_id": self.backend_id,
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_config_sha256": self.model_config_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BenchmarkBackendSpec":
        p = _mapping(payload, "BenchmarkBackendSpec payload")
        return cls(
            p["backend_id"],
            p["model_provider"],
            p["model_id"],
            p["model_version"],
            p["model_config_sha256"],
        )

    def to_json(self) -> str:
        return _json_dump(self.to_dict())

    @classmethod
    def from_json(cls, payload: str) -> "BenchmarkBackendSpec":
        return cls.from_dict(_json_load(payload, "BenchmarkBackendSpec JSON"))


@dataclass(frozen=True)
class ScheduledTrial:
    backend_id: str
    trial_index: int
    schedule_position: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_id", _required_text(self.backend_id, "backend_id"))
        for field in ("trial_index", "schedule_position"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")


@dataclass(frozen=True)
class BackendDecisionAdapters:
    adjudicate: Callable[..., Any]
    reconcile: Callable[..., ReconciliationPlan]

    def __post_init__(self) -> None:
        if not callable(self.adjudicate) or not callable(self.reconcile):
            raise ValueError("backend decision adapters must be callable")


@dataclass(frozen=True)
class BenchmarkBackendBinding:
    spec: BenchmarkBackendSpec
    factory: Callable[[dict[str, object]], BackendDecisionAdapters]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, BenchmarkBackendSpec):
            raise ValueError("spec must be BenchmarkBackendSpec")
        if not callable(self.factory):
            raise ValueError("factory must be callable")


@dataclass(frozen=True)
class SharedBenchmarkAdapters:
    initialize_skill_context: Callable[..., Any]
    collect_evidence: Callable[..., Any]
    verify_completion: Callable[..., Any]
    evaluate: Callable[..., ParsingEvaluationRecord]

    def __post_init__(self) -> None:
        for field in (
            "initialize_skill_context",
            "collect_evidence",
            "verify_completion",
            "evaluate",
        ):
            if not callable(getattr(self, field)):
                raise ValueError(f"{field} must be callable")


@dataclass(frozen=True)
class BenchmarkTrialResult:
    case_id: str
    backend_id: str
    trial_index: int
    schedule_position: int
    identity: ParsingRunIdentity
    terminal_status: str
    final_state: ColumnRunState | None
    evaluation: ParsingEvaluationRecord | None
    error_type: str | None = None
    provider_artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        object.__setattr__(self, "backend_id", _required_text(self.backend_id, "backend_id"))
        object.__setattr__(
            self, "terminal_status", _required_text(self.terminal_status, "terminal_status")
        )
        for field in ("trial_index", "schedule_position"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if not isinstance(self.identity, ParsingRunIdentity):
            raise ValueError("identity must be ParsingRunIdentity")
        if self.final_state is not None and not isinstance(self.final_state, ColumnRunState):
            raise ValueError("final_state must be ColumnRunState or None")
        if self.evaluation is not None and not isinstance(
            self.evaluation, ParsingEvaluationRecord
        ):
            raise ValueError("evaluation must be ParsingEvaluationRecord or None")
        error_type = (
            None if self.error_type is None else _required_text(self.error_type, "error_type")
        )
        refs = tuple(self.provider_artifact_refs)
        if any(not isinstance(item, str) or not item.strip() for item in refs):
            raise ValueError("provider_artifact_refs must contain non-empty strings")
        refs = tuple(item.strip() for item in refs)
        if len(refs) != len(set(refs)):
            raise ValueError("provider_artifact_refs must not contain duplicates")
        object.__setattr__(self, "error_type", error_type)
        object.__setattr__(self, "provider_artifact_refs", refs)

        if self.terminal_status == "completed":
            if self.final_state is None or self.final_state.completion is None:
                raise ValueError("completed benchmark trial requires completed final_state")
            if not self.final_state.initial_pass_complete:
                raise ValueError("completed benchmark trial requires complete initial traversal")
            if not self.final_state.reconciliation_closed or self.final_state.unresolved_revisits:
                raise ValueError(
                    "completed benchmark trial requires resolved closed reconciliation"
                )
            if self.evaluation is None:
                raise ValueError("completed benchmark trial requires evaluation")
            if self.evaluation.identity != self.identity:
                raise ValueError(
                    "completed benchmark evaluation identity does not match trial identity"
                )
            if self.evaluation.decision_revision != self.final_state.decision_revision:
                raise ValueError(
                    "completed benchmark evaluation revision does not match final state"
                )
            if self.final_state.task.task_id != self.identity.run_id:
                raise ValueError(
                    "completed benchmark final state run id does not match trial identity"
                )
            if self.error_type is not None:
                raise ValueError("completed benchmark trial cannot carry error_type")
        elif self.terminal_status == "backend-error":
            if self.evaluation is not None:
                raise ValueError("backend-error trial cannot carry evaluation")
            if self.error_type is None:
                raise ValueError("backend-error trial requires safe error_type")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "backend_id": self.backend_id,
            "trial_index": self.trial_index,
            "schedule_position": self.schedule_position,
            "identity": self.identity.to_dict(),
            "terminal_status": self.terminal_status,
            "final_state": None if self.final_state is None else self.final_state.to_dict(),
            "evaluation": None if self.evaluation is None else self.evaluation.to_dict(),
            "error_type": self.error_type,
            "provider_artifact_refs": list(self.provider_artifact_refs),
        }

    def to_json(self) -> str:
        return _json_dump(self.to_dict())


@dataclass(frozen=True)
class BenchmarkRunResult:
    case_id: str
    results: tuple[BenchmarkTrialResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "case_id"))
        results = tuple(self.results)
        if any(not isinstance(item, BenchmarkTrialResult) for item in results):
            raise ValueError("results must contain BenchmarkTrialResult values")
        object.__setattr__(self, "results", results)

    def to_dict(self) -> dict[str, object]:
        return {"case_id": self.case_id, "results": [item.to_dict() for item in self.results]}

    def to_json(self) -> str:
        return _json_dump(self.to_dict())


class ComparisonMode(str, Enum):
    MODEL_ONLY = "model-only"
    SYSTEM_VARIANT = "system-variant"


@dataclass(frozen=True)
class BenchmarkComparison:
    comparable: bool
    mismatched_dimensions: tuple[str, ...]
    changed_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.comparable, bool):
            raise ValueError("comparable must be boolean")
        mismatches = tuple(sorted(set(self.mismatched_dimensions)))
        changed = tuple(sorted(set(self.changed_dimensions)))
        if self.comparable != (not mismatches):
            raise ValueError("comparable must match whether mismatch dimensions are empty")
        object.__setattr__(self, "mismatched_dimensions", mismatches)
        object.__setattr__(self, "changed_dimensions", changed)


class _BackendExecutionError(RuntimeError):
    def __init__(self, error_type: str) -> None:
        super().__init__("backend execution failed")
        self.error_type = _required_text(error_type, "error_type")


def build_trial_schedule(
    backends: tuple[BenchmarkBackendSpec, ...] | list[BenchmarkBackendSpec],
    trials_per_backend: int,
) -> tuple[ScheduledTrial, ...]:
    specs = tuple(backends)
    if not specs or any(not isinstance(item, BenchmarkBackendSpec) for item in specs):
        raise ValueError("backends must contain at least one BenchmarkBackendSpec")
    if (
        isinstance(trials_per_backend, bool)
        or not isinstance(trials_per_backend, int)
        or trials_per_backend <= 0
    ):
        raise ValueError("trials_per_backend must be a positive integer")
    ids = tuple(item.backend_id for item in specs)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate backend ids are not allowed")
    schedule: list[ScheduledTrial] = []
    position = 0
    for trial_index in range(trials_per_backend):
        for spec in specs:
            schedule.append(ScheduledTrial(spec.backend_id, trial_index, position))
            position += 1
    return tuple(schedule)


def _workload(case: BenchmarkCase, state: ColumnRunState) -> ParsingWorkloadRef:
    return ParsingWorkloadRef(
        corpus=state.task.corpus,
        tablet=state.task.tablet,
        column=state.task.column,
        snapshot_id=state.snapshot.snapshot_id,
        snapshot_provenance=state.snapshot.source_provenance,
        repository_revision=state.task.repository_revision,
        capability_name=state.task.capability.canonical_name,
        capability_contract_version=state.task.capability.contract_version,
        capability_provenance_sha256=state.task.capability.provenance_sha256,
        tool_policy_sha256=case.tool_policy_sha256,
        evidence_policy_sha256=case.evidence_policy_sha256,
        permission_policy_sha256=case.permission_policy_sha256,
    )


def _execution_input(state: ColumnRunState) -> dict[str, object]:
    """Canonical model-visible inputs not represented by ParsingWorkloadRef."""

    return {
        "evidence_priority_token_ids": list(state.task.evidence_priority_token_ids),
        "snapshot_content": state.snapshot.to_dict(),
    }


def _trial_run_id(case: BenchmarkCase, spec: BenchmarkBackendSpec, trial_index: int) -> str:
    # Bind the run ID to the exact model and model-visible workload, not to the
    # scheduler alias. The evaluator target is deliberately excluded: changing a
    # held-out target must not change the model execution identity.
    payload = {
        "protocol_version": case.protocol_version,
        "case_id": case.case_id,
        "workload": _workload(case, case.initial_state).to_dict(),
        "execution_input": _execution_input(case.initial_state),
        "model": {
            "provider": spec.model_provider,
            "id": spec.model_id,
            "version": spec.model_version,
            "config_sha256": spec.model_config_sha256,
        },
        "trial_index": trial_index,
    }
    digest = sha256(_json_dump(payload).encode("utf-8")).hexdigest()
    return f"benchmark-{digest}"


def _identity(
    case: BenchmarkCase,
    spec: BenchmarkBackendSpec,
    state: ColumnRunState,
    trial_index: int,
) -> ParsingRunIdentity:
    return ParsingRunIdentity(
        run_id=state.task.task_id,
        workload=_workload(case, state),
        model_provider=spec.model_provider,
        model_id=spec.model_id,
        model_version=spec.model_version,
        model_config_sha256=spec.model_config_sha256,
    )


def _call_backend(function: Callable[..., Any], *args: Any) -> Any:
    try:
        return function(*args)
    except _BackendExecutionError:
        raise
    except Exception as exc:
        raise _BackendExecutionError(type(exc).__name__) from None


def _validate_backend_transition(validation: Callable[[], Any]) -> None:
    try:
        validation()
    except (InvalidColumnTransition, ValueError) as exc:
        raise _BackendExecutionError(type(exc).__name__) from None


def _adjudication_wrapper(function: Callable[..., Any]) -> Callable[..., TokenDecision]:
    """Validate provider decisions against the authoritative domain transition."""

    def wrapped(
        state: ColumnRunState,
        token: Any,
        evidence: Any,
        skill_context: Any,
        operation_id: str,
        revisit_request: Any = None,
    ) -> TokenDecision:
        decision = _call_backend(
            function,
            state,
            token,
            evidence,
            skill_context,
            operation_id,
            revisit_request,
        )
        if not isinstance(decision, TokenDecision):
            raise _BackendExecutionError("ValueError")
        if revisit_request is None:
            _validate_backend_transition(
                lambda: apply_column_event(state, TokenReviewed(operation_id, decision))
            )
        else:
            _validate_backend_transition(
                lambda: apply_column_event(
                    state,
                    TokenRevisited(operation_id, revisit_request.request_id, decision),
                )
            )
        return decision

    return wrapped


def _reconciliation_wrapper(function: Callable[..., Any]) -> Callable[..., ReconciliationPlan]:
    """Validate provider reconciliation output without reimplementing state rules."""

    def wrapped(
        state: ColumnRunState,
        skill_context: Any,
        operation_id: str,
    ) -> ReconciliationPlan:
        plan = _call_backend(function, state, skill_context, operation_id)
        if not isinstance(plan, ReconciliationPlan):
            raise _BackendExecutionError("ValueError")

        def validate() -> None:
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
                    RevisitRequested(
                        f"{operation_id}:request:{request.request_id}", request
                    ),
                )

        _validate_backend_transition(validate)
        return plan

    return wrapped


def _checkpointed_column_state(graph: Any, config: Mapping[str, Any]) -> ColumnRunState | None:
    """Recover the latest durable HARN-004 state without masking a backend failure."""

    try:
        snapshot = graph.get_state(config)
        values = getattr(snapshot, "values", None)
        if isinstance(values, Mapping):
            state = values.get("column_state")
            if isinstance(state, ColumnRunState):
                return state
    except Exception:
        return None
    return None


def _validate_completed_graph_output(
    output: Mapping[str, Any],
    identity: ParsingRunIdentity,
    expected_target: EvaluationTarget,
) -> tuple[ColumnRunState, ParsingEvaluationRecord]:
    state = output.get("column_state")
    evaluation = output.get("evaluation")
    if not isinstance(state, ColumnRunState):
        raise ValueError("completed graph output lacks ColumnRunState")
    if not isinstance(evaluation, ParsingEvaluationRecord):
        raise ValueError("completed graph output lacks ParsingEvaluationRecord")
    if state.completion is None or not state.initial_pass_complete:
        raise ValueError("completed graph output lacks complete column traversal")
    if not state.reconciliation_closed or state.unresolved_revisits:
        raise ValueError("completed graph output has unresolved reconciliation")
    if state.task.required_completion_gates != state.completion.gate_ids:
        raise ValueError("completed graph output used incomplete completion gates")
    if evaluation.identity != identity:
        raise ValueError("evaluation identity does not match benchmark trial")
    if evaluation.target != expected_target:
        raise ValueError("evaluation target does not match frozen benchmark target")
    if evaluation.decision_revision != state.decision_revision:
        raise ValueError("evaluation revision does not match completed state")
    return state, evaluation


def run_benchmark(
    case: BenchmarkCase,
    bindings: tuple[BenchmarkBackendBinding, ...] | list[BenchmarkBackendBinding],
    *,
    shared_adapters: SharedBenchmarkAdapters,
    trials_per_backend: int = 1,
) -> BenchmarkRunResult:
    if not isinstance(case, BenchmarkCase):
        raise ValueError("case must be BenchmarkCase")
    if not isinstance(shared_adapters, SharedBenchmarkAdapters):
        raise ValueError("shared_adapters must be SharedBenchmarkAdapters")
    bindings_tuple = tuple(bindings)
    if not bindings_tuple or any(
        not isinstance(item, BenchmarkBackendBinding) for item in bindings_tuple
    ):
        raise ValueError("bindings must contain at least one BenchmarkBackendBinding")

    specs = tuple(item.spec for item in bindings_tuple)
    model_identities = tuple(item.model_identity for item in specs)
    if len(model_identities) != len(set(model_identities)):
        raise ValueError(
            "duplicate model identity cannot be represented as distinct benchmark arms"
        )

    schedule = build_trial_schedule(specs, trials_per_backend)
    by_id = {item.spec.backend_id: item for item in bindings_tuple}
    results: list[BenchmarkTrialResult] = []

    for scheduled in schedule:
        binding = by_id[scheduled.backend_id]
        run_id = _trial_run_id(case, binding.spec, scheduled.trial_index)
        task = replace(case.initial_state.task, task_id=run_id)
        trial_state = ColumnRunState.initial(task, case.initial_state.snapshot)
        identity = _identity(case, binding.spec, trial_state, scheduled.trial_index)
        graph: Any | None = None
        config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

        try:
            try:
                decision_adapters = binding.factory(identity.model_context_metadata())
            except Exception as exc:
                raise _BackendExecutionError(type(exc).__name__) from None
            if not isinstance(decision_adapters, BackendDecisionAdapters):
                raise _BackendExecutionError("ValueError")

            def evaluate(state: ColumnRunState, skill_context: Any, operation_id: str):
                return shared_adapters.evaluate(
                    state,
                    skill_context,
                    operation_id,
                    identity,
                    case.evaluation_target,
                )

            graph_adapters = ColumnReviewAdapters(
                initialize_skill_context=shared_adapters.initialize_skill_context,
                collect_evidence=shared_adapters.collect_evidence,
                adjudicate=_adjudication_wrapper(decision_adapters.adjudicate),
                reconcile=_reconciliation_wrapper(decision_adapters.reconcile),
                verify_completion=shared_adapters.verify_completion,
                evaluate=evaluate,
            )
            graph = compile_column_review_graph(graph_adapters)
            output = graph.invoke(initial_graph_input(trial_state), config=config)
        except _BackendExecutionError as exc:
            partial_state = (
                _checkpointed_column_state(graph, config) if graph is not None else None
            )
            results.append(
                BenchmarkTrialResult(
                    case.case_id,
                    binding.spec.backend_id,
                    scheduled.trial_index,
                    scheduled.schedule_position,
                    identity,
                    "backend-error",
                    partial_state,
                    None,
                    exc.error_type,
                )
            )
            continue

        status = output.get("terminal_status")
        if status == "completed":
            final_state, evaluation = _validate_completed_graph_output(
                output, identity, case.evaluation_target
            )
            results.append(
                BenchmarkTrialResult(
                    case.case_id,
                    binding.spec.backend_id,
                    scheduled.trial_index,
                    scheduled.schedule_position,
                    identity,
                    "completed",
                    final_state,
                    evaluation,
                )
            )
            continue

        final_state = output.get("column_state")
        if final_state is not None and not isinstance(final_state, ColumnRunState):
            raise ValueError("graph output column_state must be ColumnRunState")
        results.append(
            BenchmarkTrialResult(
                case.case_id,
                binding.spec.backend_id,
                scheduled.trial_index,
                scheduled.schedule_position,
                identity,
                _required_text(status or "incomplete", "terminal_status"),
                final_state,
                None,
            )
        )

    return BenchmarkRunResult(case.case_id, tuple(results))


def _require_completed_pair(
    left: BenchmarkTrialResult,
    right: BenchmarkTrialResult,
) -> tuple[ParsingEvaluationRecord, ParsingEvaluationRecord]:
    if not isinstance(left, BenchmarkTrialResult) or not isinstance(
        right, BenchmarkTrialResult
    ):
        raise ValueError("comparison requires BenchmarkTrialResult values")
    if left.terminal_status != "completed" or right.terminal_status != "completed":
        raise ValueError("comparison requires completed benchmark trials")
    if left.evaluation is None or right.evaluation is None:
        raise ValueError("comparison requires completed evaluation records")
    return left.evaluation, right.evaluation


def _target_mismatches(
    left: EvaluationTarget, right: EvaluationTarget
) -> tuple[str, ...]:
    fields = (
        "target_id",
        "reviewed_ref",
        "reviewed_provenance",
        "scorer_id",
        "scorer_provenance",
        "feedback_protocol_sha256",
    )
    return tuple(
        f"target.{field}"
        for field in fields
        if getattr(left, field) != getattr(right, field)
    )


def _execution_input_mismatches(
    left: BenchmarkTrialResult,
    right: BenchmarkTrialResult,
) -> tuple[str, ...]:
    """Compare exact model-visible column inputs omitted from ParsingWorkloadRef."""

    if left.final_state is None or right.final_state is None:
        raise ValueError("execution-input comparison requires completed final states")
    mismatches: list[str] = []
    if (
        left.final_state.task.evidence_priority_token_ids
        != right.final_state.task.evidence_priority_token_ids
    ):
        mismatches.append("evidence_priority_token_ids")
    if left.final_state.snapshot.to_dict() != right.final_state.snapshot.to_dict():
        mismatches.append("snapshot_content")
    return tuple(mismatches)


def _deterministic_measurement_schema(record: ParsingEvaluationRecord) -> tuple[tuple[object, ...], ...]:
    """Return an order-independent schema signature without comparing outcome values."""

    return tuple(
        sorted(
            (
                item.name,
                item.kind.value,
                item.scope.value,
                item.source,
                item.token_id,
            )
            for item in record.deterministic_measurements
        )
    )


def _evaluation_schema_mismatches(
    left: ParsingEvaluationRecord,
    right: ParsingEvaluationRecord,
) -> tuple[str, ...]:
    if _deterministic_measurement_schema(left) != _deterministic_measurement_schema(right):
        return ("deterministic_measurement_schema",)
    return ()


def compare_trials(
    left: BenchmarkTrialResult,
    right: BenchmarkTrialResult,
    *,
    mode: ComparisonMode,
) -> BenchmarkComparison:
    left_eval, right_eval = _require_completed_pair(left, right)
    try:
        comparison_mode = mode if isinstance(mode, ComparisonMode) else ComparisonMode(mode)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid comparison mode: {mode!r}") from exc

    execution_mismatches = _execution_input_mismatches(left, right)
    evaluation_schema_mismatches = _evaluation_schema_mismatches(left_eval, right_eval)
    if comparison_mode is ComparisonMode.MODEL_ONLY:
        report: ComparabilityReport = compare_evaluation_records(
            left_eval,
            right_eval,
            ignore_model_identity=True,
        )
        mismatches = (
            tuple(report.mismatched_dimensions)
            + execution_mismatches
            + evaluation_schema_mismatches
        )
        return BenchmarkComparison(not mismatches, mismatches, ())

    lw = left.identity.workload
    rw = right.identity.workload
    frozen_workload_fields = (
        "corpus",
        "tablet",
        "column",
        "snapshot_id",
        "snapshot_provenance",
        "repository_revision",
        "permission_policy_sha256",
    )
    allowed_workload_fields = (
        "capability_name",
        "capability_contract_version",
        "capability_provenance_sha256",
        "tool_policy_sha256",
        "evidence_policy_sha256",
    )
    model_fields = (
        "model_provider",
        "model_id",
        "model_version",
        "model_config_sha256",
    )

    mismatches = [
        field
        for field in frozen_workload_fields
        if getattr(lw, field) != getattr(rw, field)
    ]
    mismatches.extend(execution_mismatches)
    mismatches.extend(evaluation_schema_mismatches)
    mismatches.extend(_target_mismatches(left_eval.target, right_eval.target))
    if left_eval.schema_version != right_eval.schema_version:
        mismatches.append("schema_version")

    changed = [
        field
        for field in allowed_workload_fields
        if getattr(lw, field) != getattr(rw, field)
    ]
    changed.extend(
        field
        for field in model_fields
        if getattr(left.identity, field) != getattr(right.identity, field)
    )
    return BenchmarkComparison(not mismatches, tuple(mismatches), tuple(changed))
