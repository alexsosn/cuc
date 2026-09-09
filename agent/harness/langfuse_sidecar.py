"""Optional best-effort Langfuse transport for provider-neutral CUC telemetry.

Importing this module does not import Langfuse. The optional SDK is loaded lazily only
when CUC telemetry is explicitly enabled and credentials are present.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

from .contracts import RunState
from .langgraph_column_review import ColumnReviewAdapters
from .parsing_evaluation import ParsingEvaluationRecord
from .telemetry import (
    DevelopmentTraceContext,
    ObservationProjection,
    ScoreProjection,
    TelemetryOutcome,
    TelemetryRunType,
    TraceProjection,
    build_development_trace_projection,
    build_parsing_trace_projection,
    project_parsing_scores,
)


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES or not normalized:
            return False
    return default


def _safe_failure(enabled: bool, exc: BaseException) -> TelemetryOutcome:
    # Never include exception text: SDK/network messages may echo keys, hosts, payloads,
    # or other values that do not belong in local diagnostics.
    return TelemetryOutcome(
        enabled=enabled,
        delivered=False,
        diagnostic=f"{type(exc).__name__}: telemetry backend failure",
    )


def _default_client_factory(**kwargs: Any):
    module = importlib.import_module("langfuse")
    span_filter = importlib.import_module("langfuse.span_filter")
    client_type = getattr(module, "Langfuse")
    is_langfuse_span = getattr(span_filter, "is_langfuse_span")
    # Langfuse v4 otherwise exports Langfuse + GenAI/known LLM spans by default.
    # CUC intentionally exports only the curated observations created by this sidecar.
    safe_kwargs = dict(kwargs)
    safe_kwargs["should_export_span"] = is_langfuse_span
    return client_type(**safe_kwargs)


def _sidecar_enabled(sidecar: Any) -> bool:
    return bool(getattr(sidecar, "enabled", True))


def _emit_safely(sidecar: Any, method: str, projection: Any) -> TelemetryOutcome:
    """Contain every optional telemetry failure outside the domain execution path."""

    try:
        outcome = getattr(sidecar, method)(projection)
        if isinstance(outcome, TelemetryOutcome):
            return outcome
        return TelemetryOutcome(_sidecar_enabled(sidecar), True)
    except Exception as exc:  # optional sidecar/projection transport boundary
        return _safe_failure(_sidecar_enabled(sidecar), exc)


def _observation(
    state: Any,
    operation_id: str,
    name: str,
    observation_type: str,
    **metadata: Any,
) -> ObservationProjection:
    # Keep enough stable identity on every child observation for incomplete/failed
    # runs to remain queryable without exporting scholarly payloads.
    base = {
        "corpus": state.task.corpus,
        "tablet": state.task.tablet,
        "column": state.task.column,
        "repository_revision": state.task.repository_revision,
        "capability_name": state.task.capability.canonical_name,
        "capability_contract_version": state.task.capability.contract_version,
        "capability_provenance_sha256": state.task.capability.provenance_sha256,
        "decision_revision": state.decision_revision,
        "next_token_index": state.cursor.next_index,
    }
    base.update(metadata)
    return ObservationProjection(
        TelemetryRunType.PARSING,
        state.task.task_id,
        operation_id,
        name,
        observation_type,
        base,
    )


def _emit_failed_operation(
    sidecar: Any,
    state: Any,
    operation_id: str,
    name: str,
    observation_type: str,
    exc: BaseException,
    **metadata: Any,
) -> TelemetryOutcome:
    """Record only safe failure identity; never let telemetry mask the domain error."""

    try:
        projection = _observation(
            state,
            operation_id,
            name,
            observation_type,
            outcome="error",
            error_type=type(exc).__name__,
            **metadata,
        )
    except Exception as telemetry_exc:
        return _safe_failure(_sidecar_enabled(sidecar), telemetry_exc)
    return _emit_safely(sidecar, "emit_observation", projection)


def _run_type_value(run_type: Any) -> str:
    return str(getattr(run_type, "value", run_type))


def _transport_metadata(
    projection: TraceProjection | ObservationProjection | ScoreProjection,
) -> dict[str, Any]:
    """Add authoritative query identity after caller metadata is copied.

    The structural projection fields are the source of truth.  Writing them last
    prevents arbitrary metadata from spoofing parsing/development run identity or an
    observation's deterministic operation identity.
    """

    metadata = dict(projection.metadata)
    metadata["run_type"] = _run_type_value(projection.run_type)
    metadata["run_id"] = projection.run_id
    if isinstance(projection, ObservationProjection):
        metadata["operation_id"] = projection.operation_id
    return metadata


def _score_id(projection: ScoreProjection) -> str:
    """Derive the Langfuse idempotency key from HARN-015 measurement identity.

    EvaluationMeasurement uniqueness is scope + token + metric name + source.  Add
    run type/run id so independent parsing/development runs cannot collide.  Value,
    provenance and decision revision deliberately stay out of the key: replaying or
    updating the same logical measurement should update one Langfuse score.
    """

    metadata = projection.metadata
    run_type = _run_type_value(projection.run_type)
    identity = "\x1f".join(
        (
            run_type,
            projection.run_id,
            projection.name,
            str(metadata.get("source", "")),
            str(metadata.get("scope", "")),
            str(metadata.get("token_id", "")),
        )
    )
    return str(uuid5(NAMESPACE_URL, f"cuc-langfuse-score:{identity}"))


class LangfuseSidecar:
    """Lazy, removable Langfuse exporter whose failures never affect domain work."""

    def __init__(
        self,
        *,
        enabled: bool,
        public_key: str | None,
        secret_key: str | None,
        base_url: str | None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self._public_key = public_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._client_factory = client_factory or _default_client_factory
        self._client_instance: Any | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> "LangfuseSidecar":
        source: Mapping[str, str] = os.environ if env is None else env
        enabled = _flag(source.get("CUC_LANGFUSE_ENABLED"), default=False)
        # Respect an explicit SDK-level opt-out; CUC never overrides it to true.
        if source.get("LANGFUSE_TRACING_ENABLED") is not None and not _flag(
            source.get("LANGFUSE_TRACING_ENABLED"), default=True
        ):
            enabled = False
        return cls(
            enabled=enabled,
            public_key=source.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=source.get("LANGFUSE_SECRET_KEY"),
            base_url=source.get("LANGFUSE_BASE_URL"),
            client_factory=client_factory,
        )

    def _credentials_ready(self) -> bool:
        return bool(self._public_key and self._secret_key)

    def _client(self):
        if self._client_instance is not None:
            return self._client_instance
        kwargs: dict[str, Any] = {
            "public_key": self._public_key,
            "secret_key": self._secret_key,
        }
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client_instance = self._client_factory(**kwargs)
        return self._client_instance

    def _preflight(self) -> TelemetryOutcome | None:
        if not self.enabled:
            return TelemetryOutcome(False, False, "telemetry disabled")
        if not self._credentials_ready():
            return TelemetryOutcome(True, False, "Langfuse credentials are missing")
        return None

    @staticmethod
    def _seed(run_type: Any, run_id: str) -> str:
        return f"{_run_type_value(run_type)}:{run_id}"

    def _trace_id(self, client: Any, run_type: Any, run_id: str) -> str:
        return client.create_trace_id(seed=self._seed(run_type, run_id))

    def emit_trace(self, projection: TraceProjection) -> TelemetryOutcome:
        if not isinstance(projection, TraceProjection):
            raise ValueError("projection must be TraceProjection")
        preflight = self._preflight()
        if preflight is not None:
            return preflight
        try:
            client = self._client()
            trace_id = self._trace_id(client, projection.run_type, projection.run_id)
            observation = client.start_observation(
                name=projection.trace_name,
                as_type="span",
                trace_context={"trace_id": trace_id},
                metadata=_transport_metadata(projection),
            )
            observation.end()
            return TelemetryOutcome(True, True)
        except Exception as exc:  # external optional transport boundary
            return _safe_failure(True, exc)

    def emit_observation(self, projection: ObservationProjection) -> TelemetryOutcome:
        if not isinstance(projection, ObservationProjection):
            raise ValueError("projection must be ObservationProjection")
        preflight = self._preflight()
        if preflight is not None:
            return preflight
        try:
            client = self._client()
            trace_id = self._trace_id(client, projection.run_type, projection.run_id)
            observation = client.start_observation(
                name=projection.name,
                as_type=projection.observation_type,
                trace_context={"trace_id": trace_id},
                metadata=_transport_metadata(projection),
            )
            observation.end()
            return TelemetryOutcome(True, True)
        except Exception as exc:  # external optional transport boundary
            return _safe_failure(True, exc)

    def emit_score(self, projection: ScoreProjection) -> TelemetryOutcome:
        if not isinstance(projection, ScoreProjection):
            raise ValueError("projection must be ScoreProjection")
        preflight = self._preflight()
        if preflight is not None:
            return preflight
        try:
            client = self._client()
            trace_id = self._trace_id(client, projection.run_type, projection.run_id)
            transport_value = (
                float(projection.value)
                if projection.data_type == "BOOLEAN" and isinstance(projection.value, bool)
                else projection.value
            )
            client.create_score(
                trace_id=trace_id,
                score_id=_score_id(projection),
                name=projection.name,
                value=transport_value,
                data_type=projection.data_type,
                metadata=_transport_metadata(projection),
            )
            return TelemetryOutcome(True, True)
        except Exception as exc:  # external optional transport boundary
            return _safe_failure(True, exc)

    def flush(self) -> TelemetryOutcome:
        preflight = self._preflight()
        if preflight is not None:
            return preflight
        try:
            self._client().flush()
            return TelemetryOutcome(True, True)
        except Exception as exc:  # external optional transport boundary
            return _safe_failure(True, exc)


def wrap_column_review_adapters(
    adapters: ColumnReviewAdapters,
    sidecar: Any,
) -> ColumnReviewAdapters:
    """Wrap HARN-004 effects without changing their inputs, results, or exceptions."""

    if not isinstance(adapters, ColumnReviewAdapters):
        raise ValueError("adapters must be ColumnReviewAdapters")

    def initialize_skill_context(state, operation_id):
        try:
            result = adapters.initialize_skill_context(state, operation_id)
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.initialize-skill-context",
                "span",
                exc,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.initialize-skill-context",
            "span",
            outcome="success",
        )
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def collect_evidence(state, token, skill_context, operation_id):
        metadata = {
            "token_id": token.token_id,
            "priority_hint": token.token_id in state.task.evidence_priority_token_ids,
        }
        try:
            result = adapters.collect_evidence(state, token, skill_context, operation_id)
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.collect-evidence",
                "tool",
                exc,
                **metadata,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.collect-evidence",
            "tool",
            outcome="success",
            evidence_count=len(result),
            **metadata,
        )
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def adjudicate(
        state,
        token,
        evidence,
        skill_context,
        operation_id,
        revisit_request=None,
    ):
        metadata = {
            "token_id": token.token_id,
            "revisit_request_id": (
                None if revisit_request is None else revisit_request.request_id
            ),
        }
        try:
            result = adapters.adjudicate(
                state,
                token,
                evidence,
                skill_context,
                operation_id,
                revisit_request,
            )
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.adjudicate",
                "agent",
                exc,
                **metadata,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.adjudicate",
            "agent",
            outcome="success",
            **metadata,
        )
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def reconcile(state, skill_context, operation_id):
        try:
            result = adapters.reconcile(state, skill_context, operation_id)
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.reconcile",
                "span",
                exc,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.reconcile",
            "span",
            outcome="success",
            finding_count=len(result.findings),
            revisit_request_count=len(result.revisit_requests),
        )
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def verify_completion(state, gate_id, skill_context, operation_id):
        try:
            result = adapters.verify_completion(state, gate_id, skill_context, operation_id)
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.completion-gate",
                "evaluator",
                exc,
                gate_id=gate_id,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.completion-gate",
            "evaluator",
            outcome="success",
            gate_id=gate_id,
            passed=bool(result.passed),
        )
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def evaluate(state, skill_context, operation_id):
        try:
            result = adapters.evaluate(state, skill_context, operation_id)
        except Exception as exc:
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.evaluate",
                "evaluator",
                exc,
            )
            raise
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.evaluate",
            "evaluator",
            outcome="success",
            evaluation_artifact_refs=(
                result.artifact_refs
                if isinstance(result, ParsingEvaluationRecord)
                else ()
            ),
        )
        _emit_safely(sidecar, "emit_observation", projection)
        if isinstance(result, ParsingEvaluationRecord):
            root_trace = build_parsing_trace_projection(state, result)
            _emit_safely(sidecar, "emit_trace", root_trace)
            for score in project_parsing_scores(result):
                _emit_safely(sidecar, "emit_score", score)
        return result

    return ColumnReviewAdapters(
        initialize_skill_context,
        collect_evidence,
        adjudicate,
        reconcile,
        verify_completion,
        evaluate,
    )


def emit_development_run(
    state: RunState,
    context: DevelopmentTraceContext,
    sidecar: Any,
) -> TelemetryOutcome:
    """Emit one data-minimized development trace; transport failure is non-fatal."""

    projection = build_development_trace_projection(state, context)
    return _emit_safely(sidecar, "emit_trace", projection)
