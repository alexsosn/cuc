"""HARN-031 RED gate: provider model/usage on adjudicate and reconcile observations.

Langfuse prices an observation only when it is a generation that names the model
and carries usage. The HARN-022 runtime already records both per provider call;
the sidecar forwards them without ever forwarding prompt or response text.
"""

from __future__ import annotations

import importlib

import pytest

from harness.column_state import (
    CompletionGateResult,
    EvidenceRecord,
    TokenDecision,
)
from harness.langgraph_column_review import ColumnReviewAdapters, ReconciliationPlan
from harness.live_providers import ProviderCallArtifact
from tests.test_harn005_integration import _column_state, _evaluation
from tests.test_langfuse_sidecar import FakeClient


def _sidecar_api():
    return importlib.import_module("harness.langfuse_sidecar")


def _telemetry_api():
    return importlib.import_module("harness.telemetry")


def _artifact(operation: str, model: str, input_tokens: int, output_tokens: int | None, error: str | None = None):
    return ProviderCallArtifact(
        operation=operation,
        attempt=0,
        model=model,
        request_id=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=12.0,
        request_sha256="a" * 64,
        response_sha256=None if error else "b" * 64,
        error_type=error,
    )


def _adapters(calls: list[ProviderCallArtifact], *, fail_adjudicate: bool = False):
    def initialize(state, operation_id):
        return ("worklist",)

    def evidence(state, token, skill_context, operation_id):
        return (EvidenceRecord("ev1", "fixture", "fixture:t1", "prov", "secret summary"),)

    def adjudicate(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        # A transient failure then a success: two artifacts, one billed generation.
        calls.append(_artifact("adjudicate", "jev-1.13.0", 900, None, "ProviderTransientError"))
        if fail_adjudicate:
            calls.append(_artifact("adjudicate", "jev-1.13.0", 900, None, "ProviderPermanentError"))
            raise RuntimeError("provider failed")
        calls.append(_artifact("adjudicate", "jev-1.13.0", 1000, 25))
        return TokenDecision("d1", token.token_id, ("secret-analysis",), ("ev1",), "secret decision")

    def reconcile(state, skill_context, operation_id):
        calls.append(_artifact("reconcile", "jev-1.13.0", 4000, 300))
        return ReconciliationPlan((), ())

    def gate(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(gate_id, True, state.decision_revision, ("gate:ref",), "ok")

    def evaluate(state, skill_context, operation_id):
        return _evaluation(state)

    return ColumnReviewAdapters(initialize, evidence, adjudicate, reconcile, gate, evaluate)


def _wrapped(calls, client, **kwargs):
    api = _sidecar_api()
    sidecar = api.LangfuseSidecar(enabled=True, public_key="pk", secret_key="sk", base_url=None, client_factory=lambda **_: client)
    return api.wrap_column_review_adapters(_adapters(calls, **kwargs), sidecar, provider_calls=lambda: tuple(calls))


def test_observation_projection_accepts_optional_model_and_usage() -> None:
    telemetry = _telemetry_api()
    projection = telemetry.ObservationProjection(
        telemetry.TelemetryRunType.PARSING, "run-1", "op-1", "cuc.parsing.adjudicate", "generation",
        {"token_id": "t1"}, model="jev-1.13.0", usage={"input": 1000, "output": 25},
    )
    assert projection.model == "jev-1.13.0"
    assert projection.usage == {"input": 1000, "output": 25}
    with pytest.raises(ValueError):
        telemetry.ObservationProjection(
            telemetry.TelemetryRunType.PARSING, "run-1", "op-1", "n", "generation", {}, usage={"input": -1},
        )
    with pytest.raises(ValueError):
        telemetry.ObservationProjection(
            telemetry.TelemetryRunType.PARSING, "run-1", "op-1", "n", "generation", {}, usage={"prompt": 1},
        )
    # Backward compatible: the existing positional form still works with no model/usage.
    plain = telemetry.ObservationProjection(telemetry.TelemetryRunType.PARSING, "run-1", "op-1", "n", "tool", {})
    assert plain.model is None and plain.usage is None


def test_adjudicate_becomes_a_generation_with_model_and_usage_from_the_provider_calls() -> None:
    calls: list[ProviderCallArtifact] = []
    client = FakeClient()
    wrapped = _wrapped(calls, client)
    state = _column_state()
    token = state.snapshot.tokens[0]
    wrapped.adjudicate(state, token, (), ("worklist",), "run-1:initial:t1:adjudicate", None)

    obs = client.observation_calls[-1]
    assert obs["name"] == "cuc.parsing.adjudicate"
    assert obs["as_type"] == "generation"
    assert obs["model"] == "jev-1.13.0"
    assert obs["usage_details"] == {"input": 1000, "output": 25}
    assert obs["metadata"]["provider_calls"] == 2
    assert obs["metadata"]["provider_retries"] == 1
    assert obs["metadata"]["outcome"] == "success"
    # Never any text: the fake decision and evidence strings must not appear.
    flat = str(obs)
    assert "secret" not in flat and "input" in flat


def test_reconcile_sums_usage_and_failed_adjudication_carries_no_usage() -> None:
    calls: list[ProviderCallArtifact] = []
    client = FakeClient()
    wrapped = _wrapped(calls, client)
    state = _column_state()
    wrapped.reconcile(state, ("worklist",), "run-1:column:reconcile")
    obs = client.observation_calls[-1]
    assert obs["as_type"] == "generation" and obs["usage_details"] == {"input": 4000, "output": 300}
    assert obs["model"] == "jev-1.13.0"

    calls.clear()
    client = FakeClient()
    wrapped = _wrapped(calls, client, fail_adjudicate=True)
    token = state.snapshot.tokens[0]
    with pytest.raises(RuntimeError):
        wrapped.adjudicate(state, token, (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    obs = client.observation_calls[-1]
    assert obs["metadata"]["outcome"] == "error"
    assert obs["metadata"]["error_type"] == "RuntimeError"
    assert obs["metadata"]["provider_calls"] == 2
    assert "usage_details" not in obs or obs["usage_details"] is None
    assert obs["as_type"] == "generation"


def test_without_a_provider_calls_source_observations_are_unchanged() -> None:
    api = _sidecar_api()
    calls: list[ProviderCallArtifact] = []
    client = FakeClient()
    sidecar = api.LangfuseSidecar(enabled=True, public_key="pk", secret_key="sk", base_url=None, client_factory=lambda **_: client)
    wrapped = api.wrap_column_review_adapters(_adapters(calls), sidecar)
    state = _column_state()
    wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    obs = client.observation_calls[-1]
    assert obs["as_type"] == "agent"
    assert "model" not in obs and "usage_details" not in obs


def test_only_calls_made_during_the_operation_are_attributed() -> None:
    calls: list[ProviderCallArtifact] = []
    calls.append(_artifact("adjudicate", "jev-1.13.0", 77, 7))  # earlier, someone else's
    client = FakeClient()
    wrapped = _wrapped(calls, client)
    state = _column_state()
    wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    assert client.observation_calls[-1]["usage_details"] == {"input": 1000, "output": 25}


# --- review findings (2026-09-21) ---------------------------------------------------------


def test_malformed_artifacts_never_surface_and_never_mask_the_domain_result() -> None:
    """Telemetry bookkeeping errors stay inside the sidecar boundary."""

    class Weird:
        error_type = None
        model = "jev-1.13.0"
        input_tokens = "abc"
        output_tokens = float("nan")

    api = _sidecar_api()
    client = FakeClient()
    sidecar = api.LangfuseSidecar(enabled=True, public_key="pk", secret_key="sk", base_url=None, client_factory=lambda **_: client)
    calls: list = []

    def adjudicate(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls.append(Weird())
        return TokenDecision("d1", token.token_id, ("x",), ("ev1",), "s")

    base = _adapters([])
    adapters = ColumnReviewAdapters(base.initialize_skill_context, base.collect_evidence, adjudicate, base.reconcile, base.verify_completion, base.evaluate)
    wrapped = api.wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: tuple(calls))
    state = _column_state()
    result = wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    assert result.decision_id == "d1"
    obs = client.observation_calls[-1]
    assert obs["metadata"]["provider_calls"] == 1
    assert obs.get("usage_details") is None  # unattributable, not coerced
    assert obs.get("model") is None

    # Domain failure with a malformed artifact: the domain exception is what escapes.
    def failing(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls.append(Weird())
        raise KeyError("domain")

    adapters = ColumnReviewAdapters(base.initialize_skill_context, base.collect_evidence, failing, base.reconcile, base.verify_completion, base.evaluate)
    wrapped = api.wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: tuple(calls))
    with pytest.raises(KeyError, match="domain"):
        wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)


def test_billed_calls_are_priced_even_when_the_operation_then_fails() -> None:
    """A successful provider call whose result fails validation was still paid for."""

    api = _sidecar_api()
    client = FakeClient()
    sidecar = api.LangfuseSidecar(enabled=True, public_key="pk", secret_key="sk", base_url=None, client_factory=lambda **_: client)
    calls: list[ProviderCallArtifact] = []

    def adjudicate(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls.append(_artifact("adjudicate", "jev-1.13.0", 1000, 25))
        raise ValueError("analyses must be a non-empty list")

    base = _adapters([])
    adapters = ColumnReviewAdapters(base.initialize_skill_context, base.collect_evidence, adjudicate, base.reconcile, base.verify_completion, base.evaluate)
    wrapped = api.wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: tuple(calls))
    state = _column_state()
    with pytest.raises(ValueError):
        wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    obs = client.observation_calls[-1]
    assert obs["metadata"]["outcome"] == "error"
    assert obs["model"] == "jev-1.13.0"
    assert obs["usage_details"] == {"input": 1000, "output": 25}


def test_coercions_are_strict_and_a_model_without_usage_is_not_emitted() -> None:
    api = _sidecar_api()
    client = FakeClient()
    sidecar = api.LangfuseSidecar(enabled=True, public_key="pk", secret_key="sk", base_url=None, client_factory=lambda **_: client)

    class Floaty:
        error_type = None
        model = 123
        input_tokens = 10.7
        output_tokens = True

    base = _adapters([])
    calls_after: list = []
    state = _column_state()

    def adjudicate2(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls_after.append(Floaty())
        return TokenDecision("d1", token.token_id, ("x",), ("ev1",), "s")

    adapters = ColumnReviewAdapters(base.initialize_skill_context, base.collect_evidence, adjudicate2, base.reconcile, base.verify_completion, base.evaluate)
    wrapped = api.wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: tuple(calls_after))
    wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    obs = client.observation_calls[-1]
    assert obs.get("usage_details") is None and obs.get("model") is None
    assert obs["metadata"]["provider_calls"] == 1
