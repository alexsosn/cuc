"""Regression tests originating from the logically independent HARN-022 PR review."""

from __future__ import annotations

import pytest

from harness.live_providers import (
    ExecutionKind,
    LiveProviderBinding,
    OpenAIResponsesClient,
    ProviderResponse,
    ProviderUsage,
)
from harness.model_benchmark import BenchmarkBackendSpec


SHA0 = "0" * 64


class DeclaredTestDouble:
    provider = "openai"
    execution_kind = ExecutionKind.TEST_DOUBLE

    def count_input_tokens(self, request, timeout_seconds):
        return 1

    def generate_json(self, request, timeout_seconds):
        return ProviderResponse(
            model="gpt-5.4-2026-03-05",
            payload={"findings": []},
            usage=ProviderUsage(1, 1),
            request_id="fixture",
        )


def _spec(*, requested_model: str = "gpt-5.4") -> BenchmarkBackendSpec:
    return BenchmarkBackendSpec(
        backend_id="openai-review",
        model_provider="openai",
        model_id=requested_model,
        model_version="gpt-5.4-2026-03-05",
        model_config_sha256=SHA0,
    )


def test_real_network_client_cannot_be_mislabelled_as_test_double() -> None:
    client = OpenAIResponsesClient(api_key_env="NEVER_READ_IN_CONSTRUCTOR")
    with pytest.raises(ValueError, match="execution|kind|live|test-double"):
        LiveProviderBinding(
            spec=_spec(),
            requested_model="gpt-5.4",
            exact_model_version="gpt-5.4-2026-03-05",
            exact_version_provenance="OpenAI model catalog snapshot",
            client=client,
            execution_kind=ExecutionKind.TEST_DOUBLE,
        )


def test_test_double_client_cannot_be_mislabelled_as_live_provider() -> None:
    client = DeclaredTestDouble()
    with pytest.raises(ValueError, match="execution|kind|live|test-double"):
        LiveProviderBinding(
            spec=_spec(),
            requested_model="gpt-5.4",
            exact_model_version="gpt-5.4-2026-03-05",
            exact_version_provenance="fixture provenance",
            client=client,
            execution_kind=ExecutionKind.LIVE_PROVIDER,
        )


def test_requested_provider_model_must_be_bound_into_harn016_identity() -> None:
    client = DeclaredTestDouble()
    with pytest.raises(ValueError, match="requested_model|model_id|identity"):
        LiveProviderBinding(
            spec=_spec(requested_model="gpt-5.4"),
            requested_model="gpt-5.4-latest",
            exact_model_version="gpt-5.4-2026-03-05",
            exact_version_provenance="fixture provenance",
            client=client,
            execution_kind=ExecutionKind.TEST_DOUBLE,
        )


def test_binding_accepts_identity_when_requested_model_and_execution_kind_are_bound() -> None:
    client = DeclaredTestDouble()
    binding = LiveProviderBinding(
        spec=_spec(requested_model="gpt-5.4"),
        requested_model="gpt-5.4",
        exact_model_version="gpt-5.4-2026-03-05",
        exact_version_provenance="fixture provenance",
        client=client,
        execution_kind=ExecutionKind.TEST_DOUBLE,
    )
    assert binding.spec.model_id == binding.requested_model
    assert binding.execution_kind is client.execution_kind
