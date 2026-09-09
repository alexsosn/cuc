from __future__ import annotations

from harness.langfuse_sidecar import LangfuseSidecar
from harness.telemetry import ScoreProjection, TelemetryRunType


class _Client:
    def __init__(self) -> None:
        self.score_calls: list[dict[str, object]] = []

    def create_trace_id(self, *, seed=None):
        return "0123456789abcdef0123456789abcdef"

    def create_score(self, **kwargs):
        self.score_calls.append(kwargs)


def _sidecar(client: _Client) -> LangfuseSidecar:
    return LangfuseSidecar.from_environment(
        env={
            "CUC_LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "public-test-key",
            "LANGFUSE_SECRET_KEY": "secret-test-key",
        },
        client_factory=lambda **kwargs: client,
    )


def _score(*, run_id: str = "run-1", token_id: str = "t1") -> ScoreProjection:
    return ScoreProjection(
        TelemetryRunType.PARSING,
        run_id,
        "morphology.exact_set_accuracy",
        0.75,
        "NUMERIC",
        {
            "source": "reviewed-morphology-scorer",
            "scope": "token",
            "token_id": token_id,
            "decision_id": f"decision-{token_id}",
            "deterministic": True,
        },
    )


def test_replayed_logical_score_uses_stable_langfuse_idempotency_key() -> None:
    client = _Client()
    sidecar = _sidecar(client)
    projection = _score()

    assert sidecar.emit_score(projection).delivered is True
    assert sidecar.emit_score(projection).delivered is True

    first = client.score_calls[0]["score_id"]
    second = client.score_calls[1]["score_id"]
    assert isinstance(first, str) and first
    assert second == first


def test_score_idempotency_key_separates_runs_and_token_measurements() -> None:
    client = _Client()
    sidecar = _sidecar(client)

    for projection in (_score(), _score(run_id="run-2"), _score(token_id="t2")):
        assert sidecar.emit_score(projection).delivered is True

    score_ids = [call["score_id"] for call in client.score_calls]
    assert len(score_ids) == len(set(score_ids))
