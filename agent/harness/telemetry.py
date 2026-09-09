"""Provider-neutral, data-minimized telemetry projections for CUC harness runs.

This module deliberately contains no Langfuse/OpenTelemetry transport code.  It turns
existing authoritative domain contracts into small queryable projections that a
removable sidecar may export.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .column_state import ColumnRunState
from .contracts import RunPhase, RunState
from .parsing_evaluation import ParsingEvaluationRecord


class TelemetryRunType(str, Enum):
    PARSING = "parsing"
    DEVELOPMENT = "development"


_JSON_SCALAR = str | int | float | bool | None
_MetadataValue = _JSON_SCALAR | tuple[str, ...]


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _metadata(value: Mapping[str, Any]) -> dict[str, _MetadataValue]:
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    result: dict[str, _MetadataValue] = {}
    for raw_key, raw_value in value.items():
        key = _required_text(raw_key, "metadata key")
        if raw_value is None or isinstance(raw_value, (str, int, float, bool)):
            result[key] = raw_value
            continue
        if isinstance(raw_value, (tuple, list)) and all(
            isinstance(item, str) for item in raw_value
        ):
            result[key] = tuple(raw_value)
            continue
        raise ValueError(f"metadata value for {key!r} must be JSON-scalar or strings")
    return result


@dataclass(frozen=True)
class TelemetryOutcome:
    enabled: bool
    delivered: bool
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.delivered, bool):
            raise ValueError("enabled and delivered must be booleans")
        if self.delivered and not self.enabled:
            raise ValueError("disabled telemetry cannot report delivered=True")
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _required_text(self.diagnostic, "diagnostic"))


@dataclass(frozen=True)
class TraceProjection:
    run_type: TelemetryRunType
    run_id: str
    trace_name: str
    metadata: Mapping[str, _MetadataValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_type", TelemetryRunType(self.run_type))
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "trace_name", _required_text(self.trace_name, "trace_name"))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "run_type": self.run_type.value,
            "run_id": self.run_id,
            "trace_name": self.trace_name,
            "metadata": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.metadata.items()
            },
        }


@dataclass(frozen=True)
class ObservationProjection:
    run_type: TelemetryRunType
    run_id: str
    operation_id: str
    name: str
    observation_type: str
    metadata: Mapping[str, _MetadataValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_type", TelemetryRunType(self.run_type))
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(
            self, "observation_type", _required_text(self.observation_type, "observation_type")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class ScoreProjection:
    run_type: TelemetryRunType
    run_id: str
    name: str
    value: str | int | float | bool
    data_type: str
    metadata: Mapping[str, _MetadataValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_type", TelemetryRunType(self.run_type))
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        if not isinstance(self.value, (str, int, float, bool)):
            raise ValueError("score value must be a scalar")
        object.__setattr__(self, "data_type", _required_text(self.data_type, "data_type"))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DevelopmentTraceContext:
    repository: str
    issue_ref: str
    base_branch: str
    head_branch: str
    head_sha: str
    executed_sha: str | None
    model_provider: str | None = None
    model_id: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        for field in ("repository", "issue_ref", "base_branch", "head_branch", "head_sha"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        for field in ("executed_sha", "model_provider", "model_id", "model_version"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _required_text(value, field))


def _validate_parsing_binding(
    state: ColumnRunState,
    record: ParsingEvaluationRecord | None,
) -> None:
    if not isinstance(state, ColumnRunState):
        raise ValueError("state must be ColumnRunState")
    if record is None:
        return
    if not isinstance(record, ParsingEvaluationRecord):
        raise ValueError("evaluation must be ParsingEvaluationRecord or None")
    identity = record.identity
    workload = identity.workload
    expected = {
        "run_id": state.task.task_id,
        "corpus": state.task.corpus,
        "tablet": state.task.tablet,
        "column": state.task.column,
        "snapshot_id": state.snapshot.snapshot_id,
        "snapshot_provenance": state.snapshot.source_provenance,
        "repository_revision": state.task.repository_revision,
        "capability_name": state.task.capability.canonical_name,
        "capability_contract_version": state.task.capability.contract_version,
        "capability_provenance_sha256": state.task.capability.provenance_sha256,
    }
    actual = {
        "run_id": identity.run_id,
        "corpus": workload.corpus,
        "tablet": workload.tablet,
        "column": workload.column,
        "snapshot_id": workload.snapshot_id,
        "snapshot_provenance": workload.snapshot_provenance,
        "repository_revision": workload.repository_revision,
        "capability_name": workload.capability_name,
        "capability_contract_version": workload.capability_contract_version,
        "capability_provenance_sha256": workload.capability_provenance_sha256,
    }
    mismatched = tuple(key for key in expected if actual[key] != expected[key])
    if mismatched:
        raise ValueError("evaluation does not match column state: " + ", ".join(mismatched))
    if record.decision_revision != state.decision_revision:
        raise ValueError("evaluation decision revision does not match column state")


def build_parsing_trace_projection(
    state: ColumnRunState,
    evaluation: ParsingEvaluationRecord | None = None,
) -> TraceProjection:
    """Project only identifiers/provenance/counts; never scholarly text or gold payloads."""

    _validate_parsing_binding(state, evaluation)
    metadata: dict[str, _MetadataValue] = {
        "run_type": TelemetryRunType.PARSING.value,
        "corpus": state.task.corpus,
        "tablet": state.task.tablet,
        "column": state.task.column,
        "snapshot_id": state.snapshot.snapshot_id,
        "snapshot_provenance": state.snapshot.source_provenance,
        "repository_revision": state.task.repository_revision,
        "capability_name": state.task.capability.canonical_name,
        "capability_contract_version": state.task.capability.contract_version,
        "capability_provenance_sha256": state.task.capability.provenance_sha256,
        "decision_revision": state.decision_revision,
        "next_token_index": state.cursor.next_index,
        "token_count": len(state.snapshot.tokens),
        "revisit_queue_count": len(state.revisit_queue),
        "unresolved_revisit_count": len(state.unresolved_revisits),
        "reconciliation_finding_count": len(state.reconciliation_findings),
        "reconciliation_closed": state.reconciliation_closed,
        "column_completed": state.completion is not None,
    }
    if evaluation is not None:
        identity = evaluation.identity
        workload = identity.workload
        metadata.update(
            {
                "model_provider": identity.model_provider,
                "model_id": identity.model_id,
                "model_version": identity.model_version,
                "model_config_sha256": identity.model_config_sha256,
                "tool_policy_sha256": workload.tool_policy_sha256,
                "evidence_policy_sha256": workload.evidence_policy_sha256,
                "permission_policy_sha256": workload.permission_policy_sha256,
                "evaluation_artifact_refs": evaluation.artifact_refs,
                "expert_feedback_ids": tuple(item.feedback_id for item in evaluation.expert_feedback),
            }
        )
    return TraceProjection(
        TelemetryRunType.PARSING,
        state.task.task_id,
        "cuc.parsing.column-review",
        metadata,
    )


def build_development_trace_projection(
    state: RunState,
    context: DevelopmentTraceContext,
) -> TraceProjection:
    """Project the existing HARN-002 state without inventing a second status model."""

    if not isinstance(state, RunState):
        raise ValueError("state must be RunState")
    if not isinstance(context, DevelopmentTraceContext):
        raise ValueError("context must be DevelopmentTraceContext")

    latest_change = state.latest_change
    metadata: dict[str, _MetadataValue] = {
        "run_type": TelemetryRunType.DEVELOPMENT.value,
        "task_id": state.task.task_id,
        "phase": state.phase.value,
        "resume_phase": None if state.resume_phase is None else state.resume_phase.value,
        "repository": context.repository,
        "issue_ref": context.issue_ref,
        "base_branch": context.base_branch,
        "head_branch": context.head_branch,
        "head_sha": context.head_sha,
        "executed_sha": context.executed_sha,
        "change_count": len(state.changes),
        "test_result_count": len(state.test_results),
        "eval_result_count": len(state.eval_results),
        "verified_head_sha": state.verified_head_sha,
        "blocked": state.phase is RunPhase.BLOCKED,
        "awaiting_human_action": state.phase is RunPhase.AWAITING_HUMAN,
        "run_complete": state.phase is RunPhase.COMPLETE,
        "has_pause_reason": state.pause_reason is not None,
        "latest_change_id": None if latest_change is None else latest_change.change_id,
        "latest_operation_ids": () if latest_change is None else latest_change.operation_ids,
        "test_gate_outcomes": tuple(
            f"{item.intent_id}:{item.outcome.value}" for item in state.test_results
        ),
        "eval_gate_outcomes": tuple(
            f"{item.eval_id}:{item.outcome.value}" for item in state.eval_results
        ),
    }
    if context.model_provider is not None:
        metadata["model_provider"] = context.model_provider
    if context.model_id is not None:
        metadata["model_id"] = context.model_id
    if context.model_version is not None:
        metadata["model_version"] = context.model_version
    if state.review is not None:
        review = state.review
        metadata.update(
            {
                "development_review_id": review.review_id,
                "development_reviewer_id": review.reviewer_id,
                "development_review_context_id": review.review_context_id,
                "development_reviewed_head_sha": review.inspected_sha,
                "development_review_disposition": review.disposition.value,
                "development_review_finding_count": len(review.findings),
                "development_review_blocking_count": sum(
                    1 for finding in review.findings if finding.blocking
                ),
                "development_review_finding_severities": tuple(
                    finding.severity.value for finding in review.findings
                ),
            }
        )

    return TraceProjection(
        TelemetryRunType.DEVELOPMENT,
        state.run_id,
        "cuc.development.run",
        metadata,
    )


def project_parsing_scores(record: ParsingEvaluationRecord) -> tuple[ScoreProjection, ...]:
    """Forward HARN-015 scalar measurements exactly; do not compute/round/rename them."""

    if not isinstance(record, ParsingEvaluationRecord):
        raise ValueError("record must be ParsingEvaluationRecord")
    projected: list[ScoreProjection] = []
    for measurement in (*record.deterministic_measurements, *record.supplementary_measurements):
        if not isinstance(measurement.value, (str, int, float, bool)):
            raise ValueError(f"measurement {measurement.name!r} is not scalar")
        data_type = measurement.kind.name
        metadata: dict[str, _MetadataValue] = {
            "source": measurement.source,
            "scope": measurement.scope.value,
            "deterministic": measurement.deterministic,
            "provenance_refs": measurement.provenance_refs,
        }
        if measurement.token_id is not None:
            metadata["token_id"] = measurement.token_id
        if measurement.decision_id is not None:
            metadata["decision_id"] = measurement.decision_id
        projected.append(
            ScoreProjection(
                TelemetryRunType.PARSING,
                record.identity.run_id,
                measurement.name,
                measurement.value,
                data_type,
                metadata,
            )
        )
    return tuple(projected)
