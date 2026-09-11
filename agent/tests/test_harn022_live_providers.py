from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    CompletionGateResult,
    EvidenceRecord,
)
from harness.langgraph_column_review import ReconciliationPlan, build_column_task_from_capability
from harness.model_benchmark import (
    BenchmarkBackendSpec,
    BenchmarkCase,
    SharedBenchmarkAdapters,
)
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    measure_column_behavior,
)
from harness.skill_capabilities import SkillCapabilityRegistry
from harness.live_providers import (
    AnthropicMessagesClient,
    ExecutionKind,
    LiveProviderBinding,
    OpenAIResponsesClient,
    ProviderBudgetPolicy,
    ProviderCredentialError,
    ProviderExecutionPolicy,
    ProviderJSONRequest,
    ProviderModelIdentityError,
    ProviderPermanentError,
    ProviderResponse,
    ProviderTransientError,
    ProviderUsage,
    run_live_benchmark,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64
SHA1 = "1" * 64
SHA2 = "2" * 64
SHA3 = "3" * 64
GOLD_REF = "reviewed:HARN022-GOLD-DO-NOT-LEAK"


def _base_state() -> ColumnRunState:
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    task = build_column_task_from_capability(
        manifest,
        provenance,
        task_id="harn022-template",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="harn022-fixture-revision",
    )
    return ColumnRunState.initial(
        task,
        ColumnSnapshot(
            "harn022-snapshot",
            "fixture:harn022",
            "harn022-source-provenance",
            (
                ColumnToken("t1", 1, "1", "a"),
                ColumnToken("t2", 2, "1", "b"),
            ),
        ),
    )


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        1,
        "harn022-case",
        _base_state(),
        SHA1,
        SHA2,
        SHA3,
        EvaluationTarget(
            "harn022-target",
            GOLD_REF,
            "gold-provenance",
            "score_reviewed_morphology.py",
            "scorer-provenance",
            SHA0,
        ),
    )


def _shared() -> SharedBenchmarkAdapters:
    def initialize_skill_context(state, operation_id):
        return {"skill": "review-automatic-parsing", "instructions": "fixture-safe"}

    def collect_evidence(state, token, skill_context, operation_id):
        return (
            EvidenceRecord(
                f"ev-{state.task.task_id}-{token.token_id}",
                "fixture",
                f"fixture:{token.token_id}",
                f"prov:{operation_id}",
                f"evidence for {token.token_id}",
            ),
        )

    def verify_completion(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(
            gate_id,
            True,
            state.decision_revision,
            (f"gate:{gate_id}",),
            "passed",
        )

    def evaluate(state, skill_context, operation_id, identity, target):
        return ParsingEvaluationRecord(
            1,
            identity,
            target,
            state.decision_revision,
            measure_column_behavior(state),
            (),
            (),
            EfficiencyMetrics(),
            (f"evaluation:{identity.run_id}",),
        )

    return SharedBenchmarkAdapters(
        initialize_skill_context,
        collect_evidence,
        verify_completion,
        evaluate,
    )


def _spec(name: str, *, version: str | None = None) -> BenchmarkBackendSpec:
    provider = "openai" if name.startswith("openai") else "anthropic"
    model_id = "gpt-family" if provider == "openai" else "claude-family"
    exact = version or ("gpt-5.4-2026-03-05" if provider == "openai" else "claude-sonnet-4-6")
    return BenchmarkBackendSpec(name, provider, model_id, exact, SHA0 if provider == "openai" else SHA1)


class FakeClient:
    def __init__(
        self,
        provider: str,
        exact_model: str,
        *,
        input_tokens: int = 5,
        scripted: list[object] | None = None,
    ) -> None:
        self.provider = provider
        self.exact_model = exact_model
        self.input_tokens = input_tokens
        self.scripted = list(scripted or [])
        self.count_calls = 0
        self.generate_calls = 0
        self.requests: list[ProviderJSONRequest] = []

    def count_input_tokens(self, request: ProviderJSONRequest, timeout_seconds: float) -> int:
        self.count_calls += 1
        self.requests.append(request)
        return self.input_tokens

    def generate_json(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        self.generate_calls += 1
        self.requests.append(request)
        if self.scripted:
            item = self.scripted.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if request.operation == "adjudicate":
            evidence_id = request.payload["evidence"][0]["evidence_id"]
            payload = {
                "analyses": [f"parse:{request.payload['token']['token_id']}"],
                "evidence_ids": [evidence_id],
                "summary": "fixture decision",
            }
        else:
            payload = {"findings": [], "revisit_requests": []}
        return ProviderResponse(
            model=self.exact_model,
            payload=payload,
            usage=ProviderUsage(input_tokens=self.input_tokens, output_tokens=3),
            request_id=f"req-{self.generate_calls}",
        )


def _binding(
    name: str,
    client: FakeClient,
    *,
    execution_kind: ExecutionKind = ExecutionKind.TEST_DOUBLE,
    requested_model: str | None = None,
    version_provenance: str = "provider-model-catalog:fixture",
) -> LiveProviderBinding:
    spec = _spec(name, version=client.exact_model)
    return LiveProviderBinding(
        spec=spec,
        requested_model=requested_model or spec.model_version,
        exact_model_version=spec.model_version,
        exact_version_provenance=version_provenance,
        client=client,
        execution_kind=execution_kind,
    )


def _budget(**overrides) -> ProviderBudgetPolicy:
    values = dict(
        max_generation_requests_per_trial=16,
        max_generation_requests_per_benchmark=32,
        max_input_tokens_per_trial=2_000,
        max_input_tokens_per_benchmark=4_000,
        max_output_tokens_per_trial=1_000,
        max_output_tokens_per_benchmark=2_000,
        max_output_tokens_per_request=128,
        max_retries_per_request=1,
        timeout_seconds=10.0,
    )
    values.update(overrides)
    return ProviderBudgetPolicy(**values)


def _policy(*, allow_paid: bool = False, budget: ProviderBudgetPolicy | None = None):
    return ProviderExecutionPolicy(
        budget=budget or _budget(),
        allow_paid_live_execution=allow_paid,
    )


def test_missing_credentials_fail_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARN022_OPENAI_KEY", raising=False)
    network_calls: list[object] = []

    def http_json(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("network must not run without credentials")

    client = OpenAIResponsesClient(api_key_env="HARN022_OPENAI_KEY", http_json=http_json)
    request = ProviderJSONRequest(
        operation="adjudicate",
        requested_model="gpt-5.4-2026-03-05",
        system_prompt="system",
        payload={"x": 1},
        output_schema={"type": "object", "properties": {}, "additionalProperties": False},
        max_output_tokens=8,
    )
    with pytest.raises(ProviderCredentialError):
        client.count_input_tokens(request, 1.0)
    assert network_calls == []
    assert "HARN022_OPENAI_KEY" in repr(client)


def test_ambiguous_or_unproven_model_versions_are_rejected() -> None:
    client = FakeClient("openai", "latest")
    with pytest.raises(ValueError, match="exact|version|latest|ambiguous"):
        _binding("openai-a", client, requested_model="gpt-latest")

    exact = FakeClient("anthropic", "claude-sonnet-4-6")
    with pytest.raises(ValueError, match="provenance|version"):
        _binding("anthropic-a", exact, version_provenance="")


def test_response_model_drift_fails_closed_and_marks_benchmark_incomplete() -> None:
    client = FakeClient(
        "openai",
        "gpt-5.4-2026-03-05",
        scripted=[
            ProviderResponse(
                model="gpt-5.4-OTHER",
                payload={"analyses": ["x"], "evidence_ids": ["ev"], "summary": "x"},
                usage=ProviderUsage(5, 2),
                request_id="req-drift",
            )
        ],
    )
    result = run_live_benchmark(
        _case(),
        (_binding("openai-a", client),),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(),
    )
    assert not result.complete
    assert result.benchmark.results[0].terminal_status == "backend-error"
    assert result.benchmark.results[0].error_type == ProviderModelIdentityError.__name__
    assert result.provider_trials[0].terminal_status == "backend-error"


def test_input_budget_exhaustion_stops_before_generation() -> None:
    client = FakeClient("openai", "gpt-5.4-2026-03-05", input_tokens=101)
    result = run_live_benchmark(
        _case(),
        (_binding("openai-a", client),),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(
            budget=_budget(max_input_tokens_per_trial=100, max_input_tokens_per_benchmark=100)
        ),
    )
    assert not result.complete
    assert client.count_calls == 1
    assert client.generate_calls == 0
    assert result.provider_trials[0].budget_exhausted


def test_retry_ceiling_is_bounded_and_later_backend_still_runs() -> None:
    bad = FakeClient(
        "openai",
        "gpt-5.4-2026-03-05",
        scripted=[
            ProviderTransientError("secret first failure"),
            ProviderTransientError("secret second failure"),
            AssertionError("retry ceiling was ignored"),
        ],
    )
    good = FakeClient("anthropic", "claude-sonnet-4-6")
    result = run_live_benchmark(
        _case(),
        (_binding("openai-a", bad), _binding("anthropic-b", good)),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(budget=_budget(max_retries_per_request=1)),
    )
    assert bad.generate_calls == 2
    assert good.generate_calls >= 3
    assert result.benchmark.results[0].terminal_status == "backend-error"
    assert result.benchmark.results[1].terminal_status == "completed"
    assert not result.complete
    assert "secret first failure" not in result.to_json()
    assert "secret second failure" not in result.to_json()


def test_held_out_target_never_enters_provider_visible_requests() -> None:
    client = FakeClient("anthropic", "claude-sonnet-4-6")
    result = run_live_benchmark(
        _case(),
        (_binding("anthropic-a", client),),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(),
    )
    assert result.complete
    encoded = json.dumps(
        [
            {
                "system_prompt": item.system_prompt,
                "payload": item.payload,
                "schema": item.output_schema,
            }
            for item in client.requests
        ],
        sort_keys=True,
    )
    assert GOLD_REF not in encoded
    assert "reviewed_ref" not in encoded
    assert "scorer_id" not in encoded
    assert "evaluation_target" not in encoded


def test_live_execution_requires_explicit_paid_opt_in_and_is_attributed() -> None:
    client = FakeClient("openai", "gpt-5.4-2026-03-05")
    live_binding = _binding(
        "openai-live",
        client,
        execution_kind=ExecutionKind.LIVE_PROVIDER,
    )
    with pytest.raises(ValueError, match="allow|paid|live"):
        run_live_benchmark(
            _case(),
            (live_binding,),
            shared_adapters=_shared(),
            trials_per_backend=1,
            execution_policy=_policy(allow_paid=False),
        )
    assert client.generate_calls == 0

    result = run_live_benchmark(
        _case(),
        (live_binding,),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(allow_paid=True),
    )
    assert result.complete
    assert {item.execution_kind for item in result.provider_trials} == {"live-provider"}
    assert all(item.request_count > 0 for item in result.provider_trials)


def test_test_double_and_live_provider_artifacts_are_distinguishable() -> None:
    fake = FakeClient("anthropic", "claude-sonnet-4-6")
    result = run_live_benchmark(
        _case(),
        (_binding("anthropic-test", fake, execution_kind=ExecutionKind.TEST_DOUBLE),),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(),
    )
    assert result.complete
    assert result.provider_trials[0].execution_kind == "test-double"
    artifact = result.to_json()
    assert "test-double" in artifact
    assert GOLD_REF not in artifact


def test_openai_and_anthropic_clients_use_current_endpoints_and_safe_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARN022_OPENAI_KEY", "OPENAI_SUPER_SECRET")
    monkeypatch.setenv("HARN022_ANTHROPIC_KEY", "ANTHROPIC_SUPER_SECRET")
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def http_json(url, headers, body, timeout_seconds):
        calls.append((url, headers, body, timeout_seconds))
        if url.endswith("/responses/input_tokens"):
            return {"input_tokens": 11}
        if url.endswith("/responses"):
            return {
                "id": "resp_123",
                "model": "gpt-5.4-2026-03-05",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"analyses":["x"],"evidence_ids":["e"],"summary":"s"}',
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 4},
            }
        if url.endswith("/messages/count_tokens"):
            return {"input_tokens": 13}
        if url.endswith("/messages"):
            return {
                "id": "msg_123",
                "model": "claude-sonnet-4-6",
                "content": [
                    {
                        "type": "text",
                        "text": '{"analyses":["x"],"evidence_ids":["e"],"summary":"s"}',
                    }
                ],
                "usage": {
                    "input_tokens": 13,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        raise AssertionError(url)

    request_openai = ProviderJSONRequest(
        "adjudicate",
        "gpt-5.4-2026-03-05",
        "system",
        {"token": "x"},
        {"type": "object", "properties": {}, "additionalProperties": False},
        32,
    )
    openai = OpenAIResponsesClient(api_key_env="HARN022_OPENAI_KEY", http_json=http_json)
    assert openai.count_input_tokens(request_openai, 2.0) == 11
    openai_response = openai.generate_json(request_openai, 2.0)
    assert openai_response.model == "gpt-5.4-2026-03-05"
    assert openai_response.usage == ProviderUsage(11, 4)

    request_anthropic = replace(request_openai, requested_model="claude-sonnet-4-6")
    anthropic = AnthropicMessagesClient(
        api_key_env="HARN022_ANTHROPIC_KEY", http_json=http_json
    )
    assert anthropic.count_input_tokens(request_anthropic, 2.0) == 13
    anthropic_response = anthropic.generate_json(request_anthropic, 2.0)
    assert anthropic_response.model == "claude-sonnet-4-6"
    assert anthropic_response.usage == ProviderUsage(13, 5)

    assert calls[0][0].endswith("/responses/input_tokens")
    assert calls[1][0].endswith("/responses")
    assert calls[2][0].endswith("/messages/count_tokens")
    assert calls[3][0].endswith("/messages")
    serialized = json.dumps(
        {
            "openai": openai_response.to_dict(),
            "anthropic": anthropic_response.to_dict(),
            "openai_repr": repr(openai),
            "anthropic_repr": repr(anthropic),
        },
        sort_keys=True,
    )
    assert "OPENAI_SUPER_SECRET" not in serialized
    assert "ANTHROPIC_SUPER_SECRET" not in serialized


def test_provider_error_details_are_never_serialized() -> None:
    client = FakeClient(
        "anthropic",
        "claude-sonnet-4-6",
        scripted=[ProviderPermanentError("ANTHROPIC_SUPER_SECRET: raw provider body")],
    )
    result = run_live_benchmark(
        _case(),
        (_binding("anthropic-a", client),),
        shared_adapters=_shared(),
        trials_per_backend=1,
        execution_policy=_policy(),
    )
    serialized = result.to_json()
    assert not result.complete
    assert "ANTHROPIC_SUPER_SECRET" not in serialized
    assert "raw provider body" not in serialized
    assert ProviderPermanentError.__name__ in serialized
