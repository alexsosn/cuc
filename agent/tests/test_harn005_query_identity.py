from __future__ import annotations

from harness.langfuse_sidecar import LangfuseSidecar
from harness.telemetry import (
    ObservationProjection,
    TelemetryRunType,
    TraceProjection,
)


class _Observation:
    def end(self) -> None:
        pass


class _Client:
    def __init__(self) -> None:
        self.observation_calls: list[dict[str, object]] = []

    def create_trace_id(self, *, seed=None):
        return "0123456789abcdef0123456789abcdef"

    def start_observation(self, **kwargs):
        self.observation_calls.append(kwargs)
        return _Observation()


def _sidecar(client: _Client) -> LangfuseSidecar:
    return LangfuseSidecar.from_environment(
        env={
            "CUC_LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "public-test-key",
            "LANGFUSE_SECRET_KEY": "secret-test-key",
        },
        client_factory=lambda **kwargs: client,
    )


def test_trace_transport_exports_queryable_run_identity_even_when_projection_metadata_is_empty() -> None:
    client = _Client()
    sidecar = _sidecar(client)
    projection = TraceProjection(
        TelemetryRunType.PARSING,
        "parse-run-42",
        "cuc.parsing.column-review",
        {},
    )

    assert sidecar.emit_trace(projection).delivered is True

    metadata = client.observation_calls[-1]["metadata"]
    assert metadata["run_type"] == "parsing"
    assert metadata["run_id"] == "parse-run-42"


def test_observation_transport_exports_queryable_run_and_operation_identity() -> None:
    client = _Client()
    sidecar = _sidecar(client)
    projection = ObservationProjection(
        TelemetryRunType.PARSING,
        "parse-run-42",
        "parse-run-42:initial:t1:evidence",
        "cuc.parsing.collect-evidence",
        "tool",
        {"token_id": "t1"},
    )

    assert sidecar.emit_observation(projection).delivered is True

    metadata = client.observation_calls[-1]["metadata"]
    assert metadata["run_type"] == "parsing"
    assert metadata["run_id"] == "parse-run-42"
    assert metadata["operation_id"] == "parse-run-42:initial:t1:evidence"
    assert metadata["token_id"] == "t1"
