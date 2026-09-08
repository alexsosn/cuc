"""Framework-neutral evaluation contracts for complete-column parsing runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from typing import Any, Mapping

from reviewed_evaluation.models import MetricSummary

from .column_state import ColumnRunState


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _sha256(value: object, field: str) -> str:
    digest = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return digest


def _text_tuple(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
    try:
        raw = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    result = tuple(_required_text(item, field) for item in raw)
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _enum(value: object, enum_type: type[Enum], field: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_int_or_none(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field)


def _nonnegative_number_or_none(value: object, field: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative finite number or None")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a non-negative finite number or None")
    return value


def _json_payload(payload: str, field: str) -> Mapping[str, Any]:
    if not isinstance(payload, str):
        raise ValueError(f"{field} must be a string")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    return _mapping(decoded, field)


def _objects(value: object, cls: type, field: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be an iterable of {cls.__name__}")
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of {cls.__name__}") from exc
    if any(not isinstance(item, cls) for item in items):
        raise ValueError(f"{field} must contain only {cls.__name__}")
    return items


class MeasurementKind(str, Enum):
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    CATEGORICAL = "categorical"
    TEXT = "text"


class MeasurementScope(str, Enum):
    TOKEN = "token"
    COLUMN = "column"
    RUN = "run"


class FeedbackScope(str, Enum):
    TOKEN = "token"
    COLUMN = "column"


class FeedbackDisposition(str, Enum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"
    NEEDS_REVIEW = "needs-review"


@dataclass(frozen=True)
class ParsingWorkloadRef:
    corpus: str
    tablet: str
    column: str
    snapshot_id: str
    snapshot_provenance: str
    repository_revision: str
    capability_name: str
    capability_contract_version: str
    capability_provenance_sha256: str
    tool_policy_sha256: str
    evidence_policy_sha256: str
    permission_policy_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "corpus", "tablet", "column", "snapshot_id", "snapshot_provenance",
            "repository_revision", "capability_name", "capability_contract_version",
        ):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        for field in (
            "capability_provenance_sha256", "tool_policy_sha256",
            "evidence_policy_sha256", "permission_policy_sha256",
        ):
            object.__setattr__(self, field, _sha256(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus": self.corpus,
            "tablet": self.tablet,
            "column": self.column,
            "snapshot_id": self.snapshot_id,
            "snapshot_provenance": self.snapshot_provenance,
            "repository_revision": self.repository_revision,
            "capability_name": self.capability_name,
            "capability_contract_version": self.capability_contract_version,
            "capability_provenance_sha256": self.capability_provenance_sha256,
            "tool_policy_sha256": self.tool_policy_sha256,
            "evidence_policy_sha256": self.evidence_policy_sha256,
            "permission_policy_sha256": self.permission_policy_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParsingWorkloadRef":
        p = _mapping(payload, "ParsingWorkloadRef payload")
        return cls(
            p["corpus"], p["tablet"], p["column"], p["snapshot_id"],
            p["snapshot_provenance"], p["repository_revision"], p["capability_name"],
            p["capability_contract_version"], p["capability_provenance_sha256"],
            p["tool_policy_sha256"], p["evidence_policy_sha256"], p["permission_policy_sha256"],
        )


@dataclass(frozen=True)
class ParsingRunIdentity:
    run_id: str
    workload: ParsingWorkloadRef
    model_provider: str
    model_id: str
    model_version: str
    model_config_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.workload, ParsingWorkloadRef):
            raise ValueError("workload must be ParsingWorkloadRef")
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "model_provider", _required_text(self.model_provider, "model_provider"))
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        object.__setattr__(self, "model_version", _required_text(self.model_version, "model_version"))
        object.__setattr__(self, "model_config_sha256", _sha256(self.model_config_sha256, "model_config_sha256"))

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workload": self.workload.to_dict(),
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_config_sha256": self.model_config_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParsingRunIdentity":
        p = _mapping(payload, "ParsingRunIdentity payload")
        return cls(
            p["run_id"], ParsingWorkloadRef.from_dict(p["workload"]), p["model_provider"],
            p["model_id"], p["model_version"], p["model_config_sha256"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "ParsingRunIdentity":
        return cls.from_dict(_json_payload(payload, "ParsingRunIdentity JSON"))

    def model_context_metadata(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workload": self.workload.to_dict(),
            "model": {
                "provider": self.model_provider,
                "id": self.model_id,
                "version": self.model_version,
                "config_sha256": self.model_config_sha256,
            },
        }


@dataclass(frozen=True)
class ComparabilityReport:
    comparable: bool
    mismatched_dimensions: tuple[str, ...]

    def __post_init__(self) -> None:
        mismatches = tuple(sorted(_text_tuple(self.mismatched_dimensions, "mismatched_dimensions")))
        if not isinstance(self.comparable, bool):
            raise ValueError("comparable must be boolean")
        if self.comparable != (not mismatches):
            raise ValueError("comparable must match whether mismatch dimensions are empty")
        object.__setattr__(self, "mismatched_dimensions", mismatches)

    def to_dict(self) -> dict[str, object]:
        return {"comparable": self.comparable, "mismatched_dimensions": list(self.mismatched_dimensions)}


_WORKLOAD_FIELDS = (
    "corpus", "tablet", "column", "snapshot_id", "snapshot_provenance",
    "repository_revision", "capability_name", "capability_contract_version",
    "capability_provenance_sha256", "tool_policy_sha256", "evidence_policy_sha256",
    "permission_policy_sha256",
)
_MODEL_FIELDS = ("model_provider", "model_id", "model_version", "model_config_sha256")


def compare_run_identities(
    left: ParsingRunIdentity,
    right: ParsingRunIdentity,
    *,
    ignore_model_identity: bool = False,
) -> ComparabilityReport:
    if not isinstance(left, ParsingRunIdentity) or not isinstance(right, ParsingRunIdentity):
        raise ValueError("comparability requires ParsingRunIdentity values")
    mismatches = [field for field in _WORKLOAD_FIELDS if getattr(left.workload, field) != getattr(right.workload, field)]
    if not ignore_model_identity:
        mismatches.extend(field for field in _MODEL_FIELDS if getattr(left, field) != getattr(right, field))
    return ComparabilityReport(not mismatches, tuple(mismatches))


@dataclass(frozen=True)
class EvaluationTarget:
    target_id: str
    reviewed_ref: str
    reviewed_provenance: str
    scorer_id: str
    scorer_provenance: str
    feedback_protocol_sha256: str

    def __post_init__(self) -> None:
        for field in ("target_id", "reviewed_ref", "reviewed_provenance", "scorer_id", "scorer_provenance"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(
            self,
            "feedback_protocol_sha256",
            _sha256(self.feedback_protocol_sha256, "feedback_protocol_sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "reviewed_ref": self.reviewed_ref,
            "reviewed_provenance": self.reviewed_provenance,
            "scorer_id": self.scorer_id,
            "scorer_provenance": self.scorer_provenance,
            "feedback_protocol_sha256": self.feedback_protocol_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationTarget":
        p = _mapping(payload, "EvaluationTarget payload")
        return cls(
            p["target_id"], p["reviewed_ref"], p["reviewed_provenance"], p["scorer_id"],
            p["scorer_provenance"], p["feedback_protocol_sha256"],
        )


@dataclass(frozen=True)
class EvaluationMeasurement:
    name: str
    kind: MeasurementKind
    value: int | float | bool | str
    scope: MeasurementScope
    source: str
    provenance_refs: tuple[str, ...] = ()
    token_id: str | None = None
    decision_id: str | None = None
    deterministic: bool = True

    def __post_init__(self) -> None:
        kind = _enum(self.kind, MeasurementKind, "kind")
        scope = _enum(self.scope, MeasurementScope, "scope")
        token_id = _optional_text(self.token_id, "token_id")
        decision_id = _optional_text(self.decision_id, "decision_id")
        if kind is MeasurementKind.NUMERIC:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
                raise ValueError("numeric measurement requires a finite int or float, not bool")
        elif kind is MeasurementKind.BOOLEAN:
            if not isinstance(self.value, bool):
                raise ValueError("boolean measurement requires bool")
        else:
            _required_text(self.value, "measurement value")
        if scope is MeasurementScope.TOKEN:
            if token_id is None or decision_id is None:
                raise ValueError("token-scoped measurement requires token_id and decision_id")
        elif token_id is not None or decision_id is not None:
            raise ValueError("non-token measurement cannot carry token/decision identity")
        if not isinstance(self.deterministic, bool):
            raise ValueError("deterministic must be boolean")
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "provenance_refs", _text_tuple(self.provenance_refs, "provenance_refs"))
        object.__setattr__(self, "token_id", token_id)
        object.__setattr__(self, "decision_id", decision_id)

    @property
    def uniqueness_key(self) -> tuple[str, str | None, str, str]:
        return (self.scope.value, self.token_id, self.name, self.source)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "value": self.value,
            "scope": self.scope.value,
            "source": self.source,
            "provenance_refs": list(self.provenance_refs),
            "token_id": self.token_id,
            "decision_id": self.decision_id,
            "deterministic": self.deterministic,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationMeasurement":
        p = _mapping(payload, "EvaluationMeasurement payload")
        return cls(
            p["name"], MeasurementKind(p["kind"]), p["value"], MeasurementScope(p["scope"]),
            p["source"], tuple(p.get("provenance_refs", ())), p.get("token_id"),
            p.get("decision_id"), p.get("deterministic", True),
        )


@dataclass(frozen=True)
class ExpertFeedback:
    feedback_id: str
    run_id: str
    decision_revision: int
    scope: FeedbackScope
    disposition: FeedbackDisposition
    reviewer_ref: str
    rationale: str
    evidence_refs: tuple[str, ...]
    token_id: str | None = None
    decision_id: str | None = None
    corrected_analyses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        scope = _enum(self.scope, FeedbackScope, "scope")
        disposition = _enum(self.disposition, FeedbackDisposition, "disposition")
        revision = _nonnegative_int(self.decision_revision, "decision_revision")
        token_id = _optional_text(self.token_id, "token_id")
        decision_id = _optional_text(self.decision_id, "decision_id")
        corrected = _text_tuple(self.corrected_analyses, "corrected_analyses")
        if scope is FeedbackScope.TOKEN and (token_id is None or decision_id is None):
            raise ValueError("token feedback requires token_id and decision_id")
        if scope is FeedbackScope.COLUMN and (token_id is not None or decision_id is not None):
            raise ValueError("column feedback cannot carry token/decision identity")
        if disposition is FeedbackDisposition.CORRECT and not corrected:
            raise ValueError("correction feedback requires corrected analyses")
        if disposition is not FeedbackDisposition.CORRECT and corrected:
            raise ValueError("only correction feedback may contain corrected analyses")
        object.__setattr__(self, "feedback_id", _required_text(self.feedback_id, "feedback_id"))
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "decision_revision", revision)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "reviewer_ref", _required_text(self.reviewer_ref, "reviewer_ref"))
        object.__setattr__(self, "rationale", _required_text(self.rationale, "rationale"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs", required=True))
        object.__setattr__(self, "token_id", token_id)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "corrected_analyses", corrected)

    def to_dict(self) -> dict[str, object]:
        return {
            "feedback_id": self.feedback_id,
            "run_id": self.run_id,
            "decision_revision": self.decision_revision,
            "scope": self.scope.value,
            "disposition": self.disposition.value,
            "reviewer_ref": self.reviewer_ref,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "token_id": self.token_id,
            "decision_id": self.decision_id,
            "corrected_analyses": list(self.corrected_analyses),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExpertFeedback":
        p = _mapping(payload, "ExpertFeedback payload")
        return cls(
            p["feedback_id"], p["run_id"], p["decision_revision"], FeedbackScope(p["scope"]),
            FeedbackDisposition(p["disposition"]), p["reviewer_ref"], p["rationale"],
            tuple(p["evidence_refs"]), p.get("token_id"), p.get("decision_id"),
            tuple(p.get("corrected_analyses", ())),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "ExpertFeedback":
        return cls.from_dict(_json_payload(payload, "ExpertFeedback JSON"))


@dataclass(frozen=True)
class EfficiencyMetrics:
    model_calls: int | None = None
    tool_calls: int | None = None
    retries: int | None = None
    latency_ms: float | int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | int | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        for field in ("model_calls", "tool_calls", "retries", "input_tokens", "output_tokens"):
            object.__setattr__(self, field, _nonnegative_int_or_none(getattr(self, field), field))
        object.__setattr__(self, "latency_ms", _nonnegative_number_or_none(self.latency_ms, "latency_ms"))
        object.__setattr__(self, "cost", _nonnegative_number_or_none(self.cost, "cost"))
        currency = _optional_text(self.currency, "currency")
        if self.cost is not None and currency is None:
            raise ValueError("currency is required when cost is recorded")
        if self.cost is None and currency is not None:
            raise ValueError("currency cannot be recorded without cost")
        object.__setattr__(self, "currency", currency)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls, "tool_calls": self.tool_calls, "retries": self.retries,
            "latency_ms": self.latency_ms, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "cost": self.cost, "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EfficiencyMetrics":
        p = _mapping(payload, "EfficiencyMetrics payload")
        return cls(
            p.get("model_calls"), p.get("tool_calls"), p.get("retries"), p.get("latency_ms"),
            p.get("input_tokens"), p.get("output_tokens"), p.get("cost"), p.get("currency"),
        )


def measure_morphology_summary(
    summary: MetricSummary,
    *,
    provenance_refs: tuple[str, ...] = (),
    namespace: str = "morphology",
) -> tuple[EvaluationMeasurement, ...]:
    if not isinstance(summary, MetricSummary):
        raise ValueError("summary must be MetricSummary")
    prefix = _required_text(namespace, "namespace")
    provenance = _text_tuple(provenance_refs, "provenance_refs")
    return tuple(
        EvaluationMeasurement(
            f"{prefix}.{name}", MeasurementKind.NUMERIC, value, MeasurementScope.RUN,
            "reviewed-morphology-scorer", provenance, deterministic=True,
        )
        for name, value in summary.to_dict().items()
    )


def _required_finding_is_resolved(state: ColumnRunState, finding) -> bool:
    if not finding.requires_revisit:
        return True
    resolved = set(state.resolved_revisit_request_ids)
    covered = {
        item.token_id
        for item in state.revisit_queue
        if item.finding_id == finding.finding_id and item.request_id in resolved
    }
    return set(finding.token_ids).issubset(covered)


def measure_column_behavior(state: ColumnRunState) -> tuple[EvaluationMeasurement, ...]:
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    latest = {token_id: state.latest_decision(token_id) for token_id in state.snapshot.token_ids}
    values: tuple[tuple[str, MeasurementKind, int | bool], ...] = (
        ("behavior.expected_token_count", MeasurementKind.NUMERIC, len(state.snapshot.tokens)),
        ("behavior.visited_initial_tokens", MeasurementKind.NUMERIC, state.cursor.next_index),
        ("behavior.revisit_decision_count", MeasurementKind.NUMERIC, sum(1 for d in state.decisions if d.revisit_of is not None)),
        ("behavior.unresolved_revisit_count", MeasurementKind.NUMERIC, len(state.unresolved_revisits)),
        ("behavior.reconciliation_finding_count", MeasurementKind.NUMERIC, len(state.reconciliation_findings)),
        (
            "behavior.unresolved_required_finding_count", MeasurementKind.NUMERIC,
            sum(1 for f in state.reconciliation_findings if f.requires_revisit and not _required_finding_is_resolved(state, f)),
        ),
        (
            "behavior.latest_ambiguous_token_count", MeasurementKind.NUMERIC,
            sum(1 for d in latest.values() if d is not None and len(d.analyses) > 1),
        ),
        ("behavior.column_completed", MeasurementKind.BOOLEAN, state.completion is not None),
    )
    provenance = (f"column-state:{state.task.task_id}:revision-{state.decision_revision}",)
    return tuple(
        EvaluationMeasurement(name, kind, value, MeasurementScope.COLUMN, "column-state", provenance, deterministic=True)
        for name, kind, value in values
    )


def validate_feedback_against_state(feedback: ExpertFeedback, state: ColumnRunState) -> None:
    if not isinstance(feedback, ExpertFeedback):
        raise ValueError("feedback must be ExpertFeedback")
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    if feedback.run_id != state.task.task_id:
        raise ValueError("feedback run_id does not match column run")
    if feedback.decision_revision != state.decision_revision:
        raise ValueError("feedback decision_revision does not match column run revision")
    if feedback.scope is FeedbackScope.TOKEN:
        if feedback.token_id not in set(state.snapshot.token_ids):
            raise ValueError("feedback token_id does not exist in column snapshot")
        decision = next((item for item in state.decisions if item.decision_id == feedback.decision_id), None)
        if decision is None:
            raise ValueError("feedback decision_id does not exist in column run")
        if decision.token_id != feedback.token_id:
            raise ValueError("feedback token_id does not match referenced decision")


@dataclass(frozen=True)
class ParsingEvaluationRecord:
    schema_version: int
    identity: ParsingRunIdentity
    target: EvaluationTarget
    decision_revision: int
    deterministic_measurements: tuple[EvaluationMeasurement, ...]
    supplementary_measurements: tuple[EvaluationMeasurement, ...]
    expert_feedback: tuple[ExpertFeedback, ...]
    efficiency: EfficiencyMetrics
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version <= 0:
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(self.identity, ParsingRunIdentity):
            raise ValueError("identity must be ParsingRunIdentity")
        if not isinstance(self.target, EvaluationTarget):
            raise ValueError("target must be EvaluationTarget")
        if not isinstance(self.efficiency, EfficiencyMetrics):
            raise ValueError("efficiency must be EfficiencyMetrics")
        revision = _nonnegative_int(self.decision_revision, "decision_revision")
        deterministic = _objects(self.deterministic_measurements, EvaluationMeasurement, "deterministic_measurements")
        supplementary = _objects(self.supplementary_measurements, EvaluationMeasurement, "supplementary_measurements")
        feedback = _objects(self.expert_feedback, ExpertFeedback, "expert_feedback")
        if any(not item.deterministic for item in deterministic):
            raise ValueError("deterministic_measurements cannot contain supplementary measurements")
        if any(item.deterministic for item in supplementary):
            raise ValueError("supplementary_measurements must be marked non-deterministic")
        if any(item.run_id != self.identity.run_id for item in feedback):
            raise ValueError("expert feedback run_id must match evaluation identity")
        if any(item.decision_revision != revision for item in feedback):
            raise ValueError("expert feedback decision_revision must match evaluation record")
        all_measurements = deterministic + supplementary
        keys = tuple(item.uniqueness_key for item in all_measurements)
        if len(keys) != len(set(keys)):
            raise ValueError("evaluation measurement keys must be unique")
        feedback_ids = tuple(item.feedback_id for item in feedback)
        if len(feedback_ids) != len(set(feedback_ids)):
            raise ValueError("expert feedback ids must be unique")
        object.__setattr__(self, "decision_revision", revision)
        object.__setattr__(self, "deterministic_measurements", deterministic)
        object.__setattr__(self, "supplementary_measurements", supplementary)
        object.__setattr__(self, "expert_feedback", feedback)
        object.__setattr__(self, "artifact_refs", _text_tuple(self.artifact_refs, "artifact_refs"))

    def model_context_metadata(self) -> dict[str, object]:
        return self.identity.model_context_metadata()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "target": self.target.to_dict(),
            "decision_revision": self.decision_revision,
            "deterministic_measurements": [item.to_dict() for item in self.deterministic_measurements],
            "supplementary_measurements": [item.to_dict() for item in self.supplementary_measurements],
            "expert_feedback": [item.to_dict() for item in self.expert_feedback],
            "efficiency": self.efficiency.to_dict(),
            "artifact_refs": list(self.artifact_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParsingEvaluationRecord":
        p = _mapping(payload, "ParsingEvaluationRecord payload")
        return cls(
            p["schema_version"],
            ParsingRunIdentity.from_dict(p["identity"]),
            EvaluationTarget.from_dict(p["target"]),
            p["decision_revision"],
            tuple(EvaluationMeasurement.from_dict(item) for item in p.get("deterministic_measurements", ())),
            tuple(EvaluationMeasurement.from_dict(item) for item in p.get("supplementary_measurements", ())),
            tuple(ExpertFeedback.from_dict(item) for item in p.get("expert_feedback", ())),
            EfficiencyMetrics.from_dict(p.get("efficiency", {})),
            tuple(p.get("artifact_refs", ())),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "ParsingEvaluationRecord":
        return cls.from_dict(_json_payload(payload, "ParsingEvaluationRecord JSON"))


_TARGET_FIELDS = (
    "target_id",
    "reviewed_ref",
    "reviewed_provenance",
    "scorer_id",
    "scorer_provenance",
    "feedback_protocol_sha256",
)


def compare_evaluation_records(
    left: ParsingEvaluationRecord,
    right: ParsingEvaluationRecord,
    *,
    ignore_model_identity: bool = False,
) -> ComparabilityReport:
    if not isinstance(left, ParsingEvaluationRecord) or not isinstance(right, ParsingEvaluationRecord):
        raise ValueError("record comparability requires ParsingEvaluationRecord values")
    identity = compare_run_identities(left.identity, right.identity, ignore_model_identity=ignore_model_identity)
    mismatches = list(identity.mismatched_dimensions)
    if left.schema_version != right.schema_version:
        mismatches.append("schema_version")
    if left.decision_revision != right.decision_revision:
        mismatches.append("decision_revision")
    for field in _TARGET_FIELDS:
        if getattr(left.target, field) != getattr(right.target, field):
            mismatches.append(f"target.{field}")
    return ComparabilityReport(not mismatches, tuple(mismatches))
