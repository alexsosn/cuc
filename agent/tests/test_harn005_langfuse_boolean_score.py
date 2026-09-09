from __future__ import annotations

from harness.langfuse_sidecar import LangfuseSidecar
from harness.telemetry import ScoreProjection, TelemetryRunType


class _Client:
    def __init__(self) -> None:
        self.score_calls = []

    def create_trace_id(self, *, seed=None):
        return "0123456789abcdef0123456789abcdef"

    def create_score(self, **kwargs):
        self.score_calls.append(kwargs)


def test_boolean_projection_stays_boolean_but_transport_uses_langfuse_numeric_encoding():
    client = _Client()
    sidecar = LangfuseSidecar.from_environment(
        env={
            "CUC_LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "public-test-key",
            "LANGFUSE_SECRET_KEY": "secret-test-key",
        },
        client_factory=lambda **kwargs: client,
    )
    projection = ScoreProjection(
        TelemetryRunType.PARSING,
        "run-boolean",
        "behavior.column_completed",
        False,
        "BOOLEAN",
        {"source": "column-state"},
    )

    outcome = sidecar.emit_score(projection)

    assert outcome.delivered is True
    assert projection.value is False
    assert projection.data_type == "BOOLEAN"
    assert len(client.score_calls) == 1
    encoded = client.score_calls[0]["value"]
    assert type(encoded) is float
    assert encoded == 0.0
