"""Optional best-effort Langfuse transport for provider-neutral CUC telemetry.

Importing this module does not import Langfuse. The optional SDK is loaded lazily only
when CUC telemetry is explicitly enabled and credentials are present.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import replace
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


def _is_loopback(base_url: str | None) -> bool:
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    from urllib.parse import urlsplit

    host = (urlsplit(base_url.strip()).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


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
    model: str | None = None,
    usage: dict[str, int] | None = None,
    io: tuple[Any, Any] = (None, None),
    **metadata: Any,
) -> TelemetryOutcome:
    """Record only safe failure identity; never let telemetry mask the domain error.

    Billed provider usage (HARN-031) is attached when known: the call was paid for
    even though the operation then failed.
    """

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
        projection = _with_provider_usage(projection, model, usage)
        projection = _with_provider_io(projection, io)
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
        capture_io: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self._public_key = public_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._client_factory = client_factory or _default_client_factory
        self._client_instance: Any | None = None
        # HARN-032: prompt/response capture is allowed only towards a loopback backend;
        # the SDK's default host is the cloud, so an unset base URL never qualifies.
        self.capture_io = bool(capture_io) and _is_loopback(base_url)

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
            capture_io=_flag(source.get("CUC_LANGFUSE_CAPTURE_IO"), default=False),
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
            kwargs: dict[str, Any] = {
                "name": projection.name,
                "as_type": projection.observation_type,
                "trace_context": {"trace_id": trace_id},
                "metadata": _transport_metadata(projection),
            }
            if projection.model is not None:
                kwargs["model"] = projection.model
            if projection.usage is not None:
                kwargs["usage_details"] = dict(projection.usage)
            if self.capture_io:
                if projection.input is not None:
                    kwargs["input"] = projection.input
                if projection.output is not None:
                    kwargs["output"] = projection.output
            observation = client.start_observation(**kwargs)
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


class _ProviderUsageWindow:
    """Attribute the HARN-022 call artifacts made during one operation (HARN-031).

    Everything here is telemetry bookkeeping: it never raises, never coerces a
    malformed count, and a call that cannot be read exactly is left unattributed.
    Usage is attributed whenever a provider call succeeded, even if the operation
    then failed validation, because that call was paid for.
    """

    def __init__(self, provider_calls: Callable[[], Any] | None) -> None:
        self._source = provider_calls
        self._start = self._count()

    def _snapshot(self) -> tuple[Any, ...]:
        if self._source is None:
            return ()
        try:
            return tuple(self._source())
        except Exception:  # telemetry must never surface a provider bookkeeping error
            return ()

    def _count(self) -> int:
        return len(self._snapshot())

    def close(self) -> tuple[dict[str, Any], str | None, dict[str, int] | None]:
        """Return (metadata, model, usage); never raises."""

        if self._source is None:
            return {}, None, None
        try:
            new_calls = self._snapshot()[self._start :]
            successful = [c for c in new_calls if getattr(c, "error_type", None) is None]
            metadata = {
                "provider_calls": len(new_calls),
                "provider_retries": max(0, len(new_calls) - 1) if new_calls else 0,
            }
            if not successful:
                return metadata, None, None
            model = getattr(successful[-1], "model", None)
            if not isinstance(model, str) or not model.strip():
                return metadata, None, None
            usage = {"input": 0, "output": 0}
            for call in successful:
                for key, attr in (("input", "input_tokens"), ("output", "output_tokens")):
                    value = getattr(call, attr, 0)
                    if value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        return metadata, None, None
                    usage[key] += value
            return metadata, model.strip(), usage
        except Exception:  # telemetry must never surface a provider bookkeeping error
            return {}, None, None


def _with_provider_usage(projection: ObservationProjection, model: str | None, usage: dict[str, int] | None):
    """Attach model/usage without letting a projection error escape."""

    try:
        return replace(projection, model=model, usage=usage)
    except Exception:
        return projection


class _ProviderIOWindow:
    """The wire exchanges (HARN-032 ``ProviderIORecord``) made during one operation."""

    def __init__(self, provider_io: Callable[[], Any] | None) -> None:
        self._source = provider_io
        self._start = len(self._snapshot())

    def _snapshot(self) -> tuple[Any, ...]:
        if self._source is None:
            return ()
        try:
            return tuple(self._source())
        except Exception:  # telemetry must never surface a bookkeeping error
            return ()

    def close(self) -> tuple[Any, Any]:
        """Return (input, output): lists of request bodies and response bodies, or None."""

        if self._source is None:
            return None, None
        try:
            records = self._snapshot()[self._start :]
            if not records:
                return None, None
            inputs = [dict(getattr(r, "request", {}) or {}) for r in records]
            outputs = [
                (dict(getattr(r, "response")) if getattr(r, "response", None) is not None
                 else {"error_type": getattr(r, "error_type", None)})
                for r in records
            ]
            return inputs, outputs
        except Exception:
            return None, None


def _with_provider_io(projection: ObservationProjection, io: tuple[Any, Any]):
    try:
        return replace(projection, input=io[0], output=io[1])
    except Exception:
        return projection


def wrap_column_review_adapters(
    adapters: ColumnReviewAdapters,
    sidecar: Any,
    *,
    provider_calls: Callable[[], Any] | None = None,
    provider_io: Callable[[], Any] | None = None,
) -> ColumnReviewAdapters:
    """Wrap HARN-004 effects without changing their inputs, results, or exceptions.

    ``provider_calls`` optionally returns the HARN-022 ``ProviderCallArtifact``s made
    so far (for example ``lambda: runtime.calls``). When given, adjudicate and
    reconcile observations become ``generation``s carrying the exact model and the
    token usage of the calls made during that operation, so Langfuse can price them.
    """

    if not isinstance(adapters, ColumnReviewAdapters):
        raise ValueError("adapters must be ColumnReviewAdapters")
    if provider_calls is not None and not callable(provider_calls):
        raise ValueError("provider_calls must be callable")
    if provider_io is not None and not callable(provider_io):
        raise ValueError("provider_io must be callable")
    provider_type = "generation" if provider_calls is not None else None

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
        window = _ProviderUsageWindow(provider_calls)
        io_window = _ProviderIOWindow(provider_io)
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
            usage_metadata, model, usage = window.close()
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.adjudicate",
                provider_type or "agent",
                exc,
                model=model,
                usage=usage,
                io=io_window.close(),
                **metadata,
                **usage_metadata,
            )
            raise
        usage_metadata, model, usage = window.close()
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.adjudicate",
            provider_type or "agent",
            outcome="success",
            **metadata,
            **usage_metadata,
        )
        projection = _with_provider_usage(projection, model, usage)
        projection = _with_provider_io(projection, io_window.close())
        _emit_safely(sidecar, "emit_observation", projection)
        return result

    def reconcile(state, skill_context, operation_id):
        window = _ProviderUsageWindow(provider_calls)
        io_window = _ProviderIOWindow(provider_io)
        try:
            result = adapters.reconcile(state, skill_context, operation_id)
        except Exception as exc:
            usage_metadata, model, usage = window.close()
            _emit_failed_operation(
                sidecar,
                state,
                operation_id,
                "cuc.parsing.reconcile",
                provider_type or "span",
                exc,
                model=model,
                usage=usage,
                io=io_window.close(),
                **usage_metadata,
            )
            raise
        usage_metadata, model, usage = window.close()
        projection = _observation(
            state,
            operation_id,
            "cuc.parsing.reconcile",
            provider_type or "span",
            outcome="success",
            finding_count=len(result.findings),
            revisit_request_count=len(result.revisit_requests),
            **usage_metadata,
        )
        projection = _with_provider_usage(projection, model, usage)
        projection = _with_provider_io(projection, io_window.close())
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
