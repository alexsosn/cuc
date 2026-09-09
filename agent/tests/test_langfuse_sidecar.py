from __future__ import annotations

import importlib

import pytest


def _sidecar_api():
    try:
        return importlib.import_module("harness.langfuse_sidecar")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-005 Langfuse sidecar is not implemented yet: {exc}")


def _telemetry_api():
    try:
        return importlib.import_module("harness.telemetry")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-005 provider-neutral telemetry is not implemented yet: {exc}")


def _trace():
    telemetry = _telemetry_api()
    return telemetry.TraceProjection(
        telemetry.TelemetryRunType.PARSING,
        "run-1",
        "cuc.parsing.column-review",
        {"run_type": "parsing", "corpus": "CUC"},
    )


def _observation():
    telemetry = _telemetry_api()
    return telemetry.ObservationProjection(
        telemetry.TelemetryRunType.PARSING,
        "run-1",
        "run-1:initial:t1:evidence",
        "cuc.parsing.collect-evidence",
        "tool",
        {"token_id": "t1", "decision_revision": 0},
    )


def _score():
    telemetry = _telemetry_api()
    return telemetry.ScoreProjection(
        telemetry.TelemetryRunType.PARSING,
        "run-1",
        "morphology.exact_set_accuracy",
        0.3333333333333333,
        "NUMERIC",
        {"source": "reviewed-morphology-scorer"},
    )


class FakeObservation:
    def __init__(self, *, fail_end: bool = False) -> None:
        self.fail_end = fail_end
        self.end_calls = 0

    def end(self):
        self.end_calls += 1
        if self.fail_end:
            raise RuntimeError("END-BACKEND-FAILURE")


class FakeClient:
    def __init__(
        self,
        *,
        fail_trace_id: bool = False,
        fail_start: bool = False,
        fail_end: bool = False,
        fail_score: bool = False,
        fail_flush: bool = False,
    ) -> None:
        self.fail_trace_id = fail_trace_id
        self.fail_start = fail_start
        self.fail_end = fail_end
        self.fail_score = fail_score
        self.fail_flush = fail_flush
        self.trace_id_calls = []
        self.observation_calls = []
        self.score_calls = []
        self.flush_calls = 0

    def create_trace_id(self, *, seed=None):
        self.trace_id_calls.append(seed)
        if self.fail_trace_id:
            raise RuntimeError("TRACE-ID-BACKEND-FAILURE")
        return "0123456789abcdef0123456789abcdef"

    def start_observation(self, **kwargs):
        self.observation_calls.append(kwargs)
        if self.fail_start:
            raise RuntimeError("START-BACKEND-FAILURE")
        return FakeObservation(fail_end=self.fail_end)

    def create_score(self, **kwargs):
        self.score_calls.append(kwargs)
        if self.fail_score:
            raise RuntimeError("SCORE-BACKEND-FAILURE")

    def flush(self):
        self.flush_calls += 1
        if self.fail_flush:
            raise RuntimeError("FLUSH-BACKEND-FAILURE")


def test_module_import_is_lazy_and_disabled_configuration_never_builds_client():
    sidecar_api = _sidecar_api()
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        raise AssertionError("disabled sidecar must not construct Langfuse client")

    sidecar = sidecar_api.LangfuseSidecar.from_environment(
        env={},
        client_factory=factory,
    )
    outcome = sidecar.emit_trace(_trace())

    assert outcome.enabled is False
    assert outcome.delivered is False
    assert factory_calls == []


def test_enabled_but_missing_credentials_is_non_delivered_not_exception():
    sidecar_api = _sidecar_api()
    sidecar = sidecar_api.LangfuseSidecar.from_environment(
        env={"CUC_LANGFUSE_ENABLED": "true"},
        client_factory=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("missing credentials must block client construction")
        ),
    )

    outcome = sidecar.emit_trace(_trace())

    assert outcome.enabled is True
    assert outcome.delivered is False
    assert "credential" in (outcome.diagnostic or "").lower()


@pytest.mark.parametrize(
    "failure_kwargs, operation",
    [
        ({"fail_trace_id": True}, "trace"),
        ({"fail_start": True}, "observation"),
        ({"fail_end": True}, "observation"),
        ({"fail_score": True}, "score"),
        ({"fail_flush": True}, "flush"),
    ],
)
def test_backend_failures_are_best_effort_and_never_escape(failure_kwargs, operation):
    sidecar_api = _sidecar_api()
    client = FakeClient(**failure_kwargs)
    sidecar = sidecar_api.LangfuseSidecar.from_environment(
        env={
            "CUC_LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "public-test-key",
            "LANGFUSE_SECRET_KEY": "secret-test-key",
        },
        client_factory=lambda **kwargs: client,
    )

    if operation == "trace":
        outcome = sidecar.emit_trace(_trace())
    elif operation == "observation":
        outcome = sidecar.emit_observation(_observation())
    elif operation == "score":
        outcome = sidecar.emit_score(_score())
    else:
        outcome = sidecar.flush()

    assert outcome.enabled is True
    assert outcome.delivered is False
    diagnostic = (outcome.diagnostic or "").lower()
    assert "secret-test-key" not in diagnostic
    assert "public-test-key" not in diagnostic


def test_successful_calls_use_deterministic_run_correlation_and_curated_payloads():
    sidecar_api = _sidecar_api()
    client = FakeClient()
    sidecar = sidecar_api.LangfuseSidecar.from_environment(
        env={
            "CUC_LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "public-test-key",
            "LANGFUSE_SECRET_KEY": "secret-test-key",
        },
        client_factory=lambda **kwargs: client,
    )

    assert sidecar.emit_trace(_trace()).delivered is True
    assert sidecar.emit_observation(_observation()).delivered is True
    assert sidecar.emit_score(_score()).delivered is True
    assert sidecar.flush().delivered is True

    assert client.trace_id_calls
    assert all(seed == "parsing:run-1" for seed in client.trace_id_calls)
    assert client.observation_calls[-1]["name"] == "cuc.parsing.collect-evidence"
    assert client.observation_calls[-1]["trace_context"]["trace_id"] == (
        "0123456789abcdef0123456789abcdef"
    )
    assert client.observation_calls[-1]["metadata"]["token_id"] == "t1"
    assert client.score_calls[-1]["name"] == "morphology.exact_set_accuracy"
    assert client.score_calls[-1]["value"] == 0.3333333333333333
