"""Optional best-effort Langfuse transport for provider-neutral CUC telemetry.

Importing this module does not import Langfuse.  The optional SDK is loaded lazily only
when CUC telemetry is explicitly enabled and credentials are present.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable, Mapping

from .telemetry import (
    ObservationProjection,
    ScoreProjection,
    TelemetryOutcome,
    TraceProjection,
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
    client_type = getattr(module, "Langfuse")
    return client_type(**kwargs)


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
        value = getattr(run_type, "value", run_type)
        return f"{value}:{run_id}"

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
                metadata=dict(projection.metadata),
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
                metadata=dict(projection.metadata),
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
            client.create_score(
                trace_id=trace_id,
                name=projection.name,
                value=projection.value,
                data_type=projection.data_type,
                metadata=dict(projection.metadata),
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
