"""HARN-032 RED gate: opt-in capture of the exact provider request and response.

HARN-005 keeps prompt/response text out of telemetry by default. A self-hosted,
loopback-only Langfuse may receive it on explicit opt-in, so the reviewer can see
exactly what the model was asked and what it answered.
"""

from __future__ import annotations

import importlib
import json

import pytest

from harness.column_state import CompletionGateResult, EvidenceRecord, TokenDecision
from harness.langgraph_column_review import ColumnReviewAdapters, ReconciliationPlan
from harness.live_providers import ProviderCallArtifact
from tests.test_harn005_integration import _column_state, _evaluation
from tests.test_harn029_typesafe_jev import KEY_ENV, Transport, _adjudicate_payload, _request
from tests.test_langfuse_sidecar import FakeClient


def _live():
    return importlib.import_module("harness.live_providers")


def _jev():
    return importlib.import_module("harness.typesafe_jev")


def _sidecar_api():
    return importlib.import_module("harness.langfuse_sidecar")


# --- capture at the HTTP boundary -----------------------------------------------------------


def test_capture_records_exact_request_body_and_response_per_call(monkeypatch) -> None:
    live, jev = _live(), _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    capture = live.ProviderIOCapture()
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})
    client = jev.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport, capture=capture)
    client.generate_json(_request(_adjudicate_payload()), 5.0)

    assert len(capture.records) == 1
    record = capture.records[0]
    assert record.operation == "adjudicate"
    assert record.url.endswith("/v1/systemone")
    assert record.request == transport.calls[0][2]          # exactly what went over the wire
    assert record.response["answers"]["reading"]["choice"]   # exactly what came back
    assert "Authorization" not in json.dumps(record.to_dict())
    assert "sk-test" not in json.dumps(record.to_dict())


def test_capture_is_off_by_default_and_never_created_implicitly(monkeypatch) -> None:
    jev = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})
    client = jev.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport)
    client.generate_json(_request(_adjudicate_payload()), 5.0)
    assert client.capture is None


def test_capture_records_failed_calls_with_the_error_type_only(monkeypatch) -> None:
    live, jev = _live(), _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    capture = live.ProviderIOCapture()

    def transport(url, headers, body, timeout):
        raise live.ProviderTransientError("provider HTTP 429 /Users/secret/path")

    client = jev.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport, capture=capture)
    with pytest.raises(live.ProviderTransientError):
        client.generate_json(_request(_adjudicate_payload()), 5.0)
    record = capture.records[0]
    assert record.response is None
    assert record.error_type == "ProviderTransientError"
    assert "/Users/secret" not in json.dumps(record.to_dict())


# --- sidecar attaches input/output only on opt-in against a loopback backend -----------------


def _wrapped(client, sidecar, calls, io):
    api = _sidecar_api()

    def initialize(state, operation_id):
        return ("worklist",)

    def evidence(state, token, skill_context, operation_id):
        return (EvidenceRecord("ev1", "fixture", "fixture:t1", "prov", "secret summary"),)

    def adjudicate(state, token, evidence_records, skill_context, operation_id, revisit_request=None):
        calls.append(ProviderCallArtifact("adjudicate", 0, "jev-1.13.0", None, 1000, 25, 1.0, "a" * 64, "b" * 64, None))
        io.append(_live().ProviderIORecord("adjudicate", "https://api.typesafe.ai/v1/systemone",
                                           {"model": "jev-latest", "state": {"token": {"surface": "ġr"}}, "questions": {}},
                                           {"model": "jev-1.13.0", "answers": {"reading": {"choice": "reading-1"}}}, None))
        return TokenDecision("d1", token.token_id, ("x",), ("ev1",), "s")

    def reconcile(state, skill_context, operation_id):
        return ReconciliationPlan((), ())

    def gate(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(gate_id, True, state.decision_revision, ("g",), "ok")

    def evaluate(state, skill_context, operation_id):
        return _evaluation(state)

    adapters = ColumnReviewAdapters(initialize, evidence, adjudicate, reconcile, gate, evaluate)
    return api.wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: tuple(calls), provider_io=lambda: tuple(io))


@pytest.mark.parametrize(
    "base_url,capture_flag,expect_io",
    [
        ("http://localhost:3000", "1", True),
        ("http://127.0.0.1:3000", "true", True),
        ("http://localhost:3000", None, False),          # opt-in missing
        ("https://cloud.langfuse.com", "1", False),       # never to a non-loopback backend
        (None, "1", False),                               # SDK default host is the cloud
    ],
)
def test_input_output_reach_the_generation_only_on_opt_in_to_a_loopback_backend(
    base_url, capture_flag, expect_io
) -> None:
    api = _sidecar_api()
    env = {"CUC_LANGFUSE_ENABLED": "1", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}
    if base_url:
        env["LANGFUSE_BASE_URL"] = base_url
    if capture_flag:
        env["CUC_LANGFUSE_CAPTURE_IO"] = capture_flag
    client = FakeClient()
    sidecar = api.LangfuseSidecar.from_environment(env=env, client_factory=lambda **_: client)
    assert sidecar.capture_io is expect_io
    calls, io = [], []
    wrapped = _wrapped(client, sidecar, calls, io)
    state = _column_state()
    wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    obs = client.observation_calls[-1]
    flat = json.dumps(obs, ensure_ascii=False, default=str)
    if expect_io:
        assert obs["input"][0]["state"]["token"]["surface"] == "ġr"
        assert obs["output"][0]["answers"]["reading"]["choice"] == "reading-1"
    else:
        assert "input" not in obs and "output" not in obs
        assert "ġr" not in flat and "reading-1" not in flat


def test_capture_never_forwards_the_decision_or_evidence_text_itself() -> None:
    """Only the provider wire exchange is captured; harness objects stay minimised."""

    api = _sidecar_api()
    env = {"CUC_LANGFUSE_ENABLED": "1", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
           "LANGFUSE_BASE_URL": "http://localhost:3000", "CUC_LANGFUSE_CAPTURE_IO": "1"}
    client = FakeClient()
    sidecar = api.LangfuseSidecar.from_environment(env=env, client_factory=lambda **_: client)
    calls, io = [], []
    wrapped = _wrapped(client, sidecar, calls, io)
    state = _column_state()
    wrapped.adjudicate(state, state.snapshot.tokens[0], (), ("worklist",), "run-1:initial:t1:adjudicate", None)
    flat = json.dumps(client.observation_calls[-1], ensure_ascii=False, default=str)
    assert "secret summary" not in flat and "secret decision" not in flat
