"""Bounded live-provider execution for the HARN-016 complete-column benchmark.

The provider boundary is intentionally narrow. HARN-016 remains responsible for
benchmark scheduling/comparability and HARN-004 remains responsible for scholarly
state transitions. This module supplies model decisions, enforces live-call policy,
and emits redacted execution artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
import os
from time import monotonic
from typing import Any, Callable, Mapping, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from .column_state import (
    ColumnRunState,
    CorpusReconciliationFinding,
    EvidenceRecord,
    ReconciliationScope,
    RevisitRequest,
    TokenDecision,
)
from .langgraph_column_review import ReconciliationPlan
from .model_benchmark import (
    BackendDecisionAdapters,
    BenchmarkBackendBinding,
    BenchmarkBackendSpec,
    BenchmarkCase,
    BenchmarkRunResult,
    SharedBenchmarkAdapters,
    run_benchmark,
)


_AMBIGUOUS_VERSION_MARKERS = frozenset(
    {"latest", "default", "auto", "unknown", "unspecified", "rolling"}
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return float(value)


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _safe_json_value(value: object, field_name: str = "value") -> Any:
    """Convert known checkpoint/domain values to JSON without falling back to repr()."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} mapping keys must be strings")
            result[key] = _safe_json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, (tuple, list)):
        return [_safe_json_value(item, field_name) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _safe_json_value(to_dict(), field_name)
    raise ValueError(f"{field_name} contains unsupported provider-visible value")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _safe_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class ProviderError(RuntimeError):
    """Base provider-boundary error. Messages are never persisted by this module."""


class ProviderCredentialError(ProviderError):
    pass


class ProviderTransientError(ProviderError):
    pass


class ProviderPermanentError(ProviderError):
    pass


class ProviderModelIdentityError(ProviderPermanentError):
    pass


class ProviderBudgetExceeded(ProviderPermanentError):
    pass


class ExecutionKind(str, Enum):
    LIVE_PROVIDER = "live-provider"
    TEST_DOUBLE = "test-double"


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_tokens", _nonnegative_int(self.input_tokens, "input_tokens")
        )
        object.__setattr__(
            self, "output_tokens", _nonnegative_int(self.output_tokens, "output_tokens")
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class ProviderResponse:
    model: str
    payload: Mapping[str, Any]
    usage: ProviderUsage
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _required_text(self.model, "model"))
        object.__setattr__(self, "payload", dict(_mapping(self.payload, "payload")))
        if not isinstance(self.usage, ProviderUsage):
            raise ValueError("usage must be ProviderUsage")
        if self.request_id is not None:
            object.__setattr__(
                self, "request_id", _required_text(self.request_id, "request_id")
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "payload": dict(self.payload),
            "usage": self.usage.to_dict(),
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class ProviderJSONRequest:
    operation: str
    requested_model: str
    system_prompt: str
    payload: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    max_output_tokens: int

    def __post_init__(self) -> None:
        operation = _required_text(self.operation, "operation")
        if operation not in {"adjudicate", "reconcile"}:
            raise ValueError("operation must be adjudicate or reconcile")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(
            self, "requested_model", _required_text(self.requested_model, "requested_model")
        )
        object.__setattr__(
            self, "system_prompt", _required_text(self.system_prompt, "system_prompt")
        )
        object.__setattr__(self, "payload", dict(_mapping(self.payload, "payload")))
        object.__setattr__(
            self, "output_schema", dict(_mapping(self.output_schema, "output_schema"))
        )
        object.__setattr__(
            self,
            "max_output_tokens",
            _positive_int(self.max_output_tokens, "max_output_tokens"),
        )

    def safe_digest_payload(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "requested_model": self.requested_model,
            "system_prompt": self.system_prompt,
            "payload": dict(self.payload),
            "output_schema": dict(self.output_schema),
            "max_output_tokens": self.max_output_tokens,
        }


class ProviderJSONClient(Protocol):
    provider: str

    def count_input_tokens(
        self, request: ProviderJSONRequest, timeout_seconds: float
    ) -> int: ...

    def generate_json(
        self, request: ProviderJSONRequest, timeout_seconds: float
    ) -> ProviderResponse: ...


HttpJSON = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]


def _default_http_json(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        # Never retain provider error bodies: they can echo request details.
        if exc.code in {408, 409, 429} or exc.code >= 500:
            raise ProviderTransientError(f"provider HTTP {exc.code}") from None
        raise ProviderPermanentError(f"provider HTTP {exc.code}") from None
    except (urllib_error.URLError, TimeoutError, OSError):
        raise ProviderTransientError("provider network failure") from None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderPermanentError("provider returned invalid JSON") from None
    if not isinstance(decoded, Mapping):
        raise ProviderPermanentError("provider returned non-object JSON")
    return decoded


class _EnvironmentCredentialClient:
    provider: str

    def __init__(
        self,
        *,
        api_key_env: str,
        http_json: HttpJSON = _default_http_json,
        base_url: str,
    ) -> None:
        self._api_key_env = _required_text(api_key_env, "api_key_env")
        if not callable(http_json):
            raise ValueError("http_json must be callable")
        self._http_json = http_json
        self._base_url = _required_text(base_url, "base_url").rstrip("/")

    def _api_key(self) -> str:
        value = os.environ.get(self._api_key_env)
        if value is None or not value.strip():
            raise ProviderCredentialError(
                f"provider credential environment variable {self._api_key_env} is missing"
            )
        return value

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(api_key_env={self._api_key_env!r}, "
            f"base_url={self._base_url!r})"
        )


class OpenAIResponsesClient(_EnvironmentCredentialClient):
    provider = "openai"

    def __init__(
        self,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        http_json: HttpJSON = _default_http_json,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        super().__init__(
            api_key_env=api_key_env,
            http_json=http_json,
            base_url=base_url,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _body(request: ProviderJSONRequest, *, include_output_limit: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.requested_model,
            "instructions": request.system_prompt,
            "input": _canonical_json(request.payload),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"cuc_harn022_{request.operation}",
                    "strict": True,
                    "schema": dict(request.output_schema),
                }
            },
        }
        if include_output_limit:
            body["max_output_tokens"] = request.max_output_tokens
        return body

    def count_input_tokens(self, request: ProviderJSONRequest, timeout_seconds: float) -> int:
        result = self._http_json(
            f"{self._base_url}/responses/input_tokens",
            self._headers(),
            self._body(request, include_output_limit=False),
            timeout_seconds,
        )
        return _nonnegative_int(result.get("input_tokens"), "input_tokens")

    @staticmethod
    def _output_text(result: Mapping[str, Any]) -> str:
        direct = result.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        output = result.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, Mapping):
                        continue
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
        raise ProviderPermanentError("OpenAI response has no output text")

    def generate_json(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        result = self._http_json(
            f"{self._base_url}/responses",
            self._headers(),
            self._body(request, include_output_limit=True),
            timeout_seconds,
        )
        try:
            payload = json.loads(self._output_text(result))
        except json.JSONDecodeError:
            raise ProviderPermanentError("OpenAI structured output is invalid JSON") from None
        usage = _mapping(result.get("usage"), "usage")
        return ProviderResponse(
            model=_required_text(result.get("model"), "model"),
            payload=_mapping(payload, "structured output"),
            usage=ProviderUsage(
                _nonnegative_int(usage.get("input_tokens"), "usage.input_tokens"),
                _nonnegative_int(usage.get("output_tokens"), "usage.output_tokens"),
            ),
            request_id=(
                _required_text(result.get("id"), "id") if result.get("id") is not None else None
            ),
        )


class AnthropicMessagesClient(_EnvironmentCredentialClient):
    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        http_json: HttpJSON = _default_http_json,
        base_url: str = "https://api.anthropic.com/v1",
        anthropic_version: str = "2023-06-01",
    ) -> None:
        super().__init__(
            api_key_env=api_key_env,
            http_json=http_json,
            base_url=base_url,
        )
        self._anthropic_version = _required_text(anthropic_version, "anthropic_version")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key(),
            "anthropic-version": self._anthropic_version,
            "content-type": "application/json",
        }

    @staticmethod
    def _body(request: ProviderJSONRequest, *, include_output_limit: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.requested_model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": _canonical_json(request.payload)}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": dict(request.output_schema),
                }
            },
        }
        if include_output_limit:
            body["max_tokens"] = request.max_output_tokens
        return body

    def count_input_tokens(self, request: ProviderJSONRequest, timeout_seconds: float) -> int:
        result = self._http_json(
            f"{self._base_url}/messages/count_tokens",
            self._headers(),
            self._body(request, include_output_limit=False),
            timeout_seconds,
        )
        return _nonnegative_int(result.get("input_tokens"), "input_tokens")

    @staticmethod
    def _output_text(result: Mapping[str, Any]) -> str:
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        raise ProviderPermanentError("Anthropic response has no text content")

    def generate_json(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        result = self._http_json(
            f"{self._base_url}/messages",
            self._headers(),
            self._body(request, include_output_limit=True),
            timeout_seconds,
        )
        try:
            payload = json.loads(self._output_text(result))
        except json.JSONDecodeError:
            raise ProviderPermanentError("Anthropic structured output is invalid JSON") from None
        usage = _mapping(result.get("usage"), "usage")
        input_tokens = sum(
            _nonnegative_int(usage.get(field_name, 0), f"usage.{field_name}")
            for field_name in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        return ProviderResponse(
            model=_required_text(result.get("model"), "model"),
            payload=_mapping(payload, "structured output"),
            usage=ProviderUsage(
                input_tokens,
                _nonnegative_int(usage.get("output_tokens"), "usage.output_tokens"),
            ),
            request_id=(
                _required_text(result.get("id"), "id") if result.get("id") is not None else None
            ),
        )


@dataclass(frozen=True)
class ProviderBudgetPolicy:
    max_generation_requests_per_trial: int
    max_generation_requests_per_benchmark: int
    max_input_tokens_per_trial: int
    max_input_tokens_per_benchmark: int
    max_output_tokens_per_trial: int
    max_output_tokens_per_benchmark: int
    max_output_tokens_per_request: int
    max_retries_per_request: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        for field_name in (
            "max_generation_requests_per_trial",
            "max_generation_requests_per_benchmark",
            "max_input_tokens_per_trial",
            "max_input_tokens_per_benchmark",
            "max_output_tokens_per_trial",
            "max_output_tokens_per_benchmark",
            "max_output_tokens_per_request",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "max_retries_per_request",
            _nonnegative_int(self.max_retries_per_request, "max_retries_per_request"),
        )
        object.__setattr__(
            self,
            "timeout_seconds",
            _positive_number(self.timeout_seconds, "timeout_seconds"),
        )


@dataclass(frozen=True)
class ProviderExecutionPolicy:
    budget: ProviderBudgetPolicy
    allow_paid_live_execution: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.budget, ProviderBudgetPolicy):
            raise ValueError("budget must be ProviderBudgetPolicy")
        if not isinstance(self.allow_paid_live_execution, bool):
            raise ValueError("allow_paid_live_execution must be boolean")


@dataclass(frozen=True)
class LiveProviderBinding:
    spec: BenchmarkBackendSpec
    requested_model: str
    exact_model_version: str
    exact_version_provenance: str
    client: ProviderJSONClient
    execution_kind: ExecutionKind = ExecutionKind.LIVE_PROVIDER

    def __post_init__(self) -> None:
        if not isinstance(self.spec, BenchmarkBackendSpec):
            raise ValueError("spec must be BenchmarkBackendSpec")
        requested = _required_text(self.requested_model, "requested_model")
        exact = _required_text(self.exact_model_version, "exact_model_version")
        provenance = _required_text(
            self.exact_version_provenance, "exact_version_provenance"
        )
        if exact.lower() in _AMBIGUOUS_VERSION_MARKERS:
            raise ValueError("exact model version is ambiguous")
        if self.spec.model_version != exact:
            raise ValueError("BenchmarkBackendSpec model_version must equal exact_model_version")
        provider = getattr(self.client, "provider", None)
        if provider != self.spec.model_provider:
            raise ValueError("provider client does not match BenchmarkBackendSpec provider")
        if not callable(getattr(self.client, "count_input_tokens", None)) or not callable(
            getattr(self.client, "generate_json", None)
        ):
            raise ValueError("client must implement ProviderJSONClient")
        try:
            kind = (
                self.execution_kind
                if isinstance(self.execution_kind, ExecutionKind)
                else ExecutionKind(self.execution_kind)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid execution_kind") from exc
        object.__setattr__(self, "requested_model", requested)
        object.__setattr__(self, "exact_model_version", exact)
        object.__setattr__(self, "exact_version_provenance", provenance)
        object.__setattr__(self, "execution_kind", kind)


@dataclass(frozen=True)
class ProviderCallArtifact:
    operation: str
    attempt: int
    model: str
    request_id: str | None
    input_tokens: int
    output_tokens: int | None
    latency_ms: float
    request_sha256: str
    response_sha256: str | None
    error_type: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "attempt": self.attempt,
            "model": self.model,
            "request_id": self.request_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class ProviderTrialArtifact:
    backend_id: str
    run_id: str
    execution_kind: str
    terminal_status: str
    request_count: int
    input_tokens: int
    output_tokens: int
    retries: int
    budget_exhausted: bool
    calls: tuple[ProviderCallArtifact, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_id": self.backend_id,
            "run_id": self.run_id,
            "execution_kind": self.execution_kind,
            "terminal_status": self.terminal_status,
            "request_count": self.request_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retries": self.retries,
            "budget_exhausted": self.budget_exhausted,
            "calls": [item.to_dict() for item in self.calls],
        }


@dataclass(frozen=True)
class LiveBenchmarkRunResult:
    benchmark: BenchmarkRunResult
    provider_trials: tuple[ProviderTrialArtifact, ...]
    complete: bool

    def to_dict(self) -> dict[str, object]:
        # Deliberately omit full HARN-015 evaluations/targets from the provider artifact.
        return {
            "case_id": self.benchmark.case_id,
            "complete": self.complete,
            "benchmark_trials": [
                {
                    "backend_id": item.backend_id,
                    "trial_index": item.trial_index,
                    "schedule_position": item.schedule_position,
                    "run_id": item.identity.run_id,
                    "terminal_status": item.terminal_status,
                    "error_type": item.error_type,
                }
                for item in self.benchmark.results
            ],
            "provider_trials": [item.to_dict() for item in self.provider_trials],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass
class _TrialBudget:
    requests: int = 0
    input_tokens: int = 0
    output_tokens_reserved: int = 0


@dataclass(frozen=True)
class _Reservation:
    run_id: str
    input_tokens: int
    output_tokens: int


class _BudgetLedger:
    def __init__(self, policy: ProviderBudgetPolicy) -> None:
        self.policy = policy
        self.total = _TrialBudget()
        self.by_run: dict[str, _TrialBudget] = {}

    def _trial(self, run_id: str) -> _TrialBudget:
        return self.by_run.setdefault(run_id, _TrialBudget())

    def reserve(self, run_id: str, input_tokens: int) -> _Reservation:
        input_tokens = _nonnegative_int(input_tokens, "input_tokens")
        trial = self._trial(run_id)
        p = self.policy
        if trial.requests + 1 > p.max_generation_requests_per_trial:
            raise ProviderBudgetExceeded("trial generation request budget exhausted")
        if self.total.requests + 1 > p.max_generation_requests_per_benchmark:
            raise ProviderBudgetExceeded("benchmark generation request budget exhausted")
        if trial.input_tokens + input_tokens > p.max_input_tokens_per_trial:
            raise ProviderBudgetExceeded("trial input-token budget exhausted")
        if self.total.input_tokens + input_tokens > p.max_input_tokens_per_benchmark:
            raise ProviderBudgetExceeded("benchmark input-token budget exhausted")

        remaining_trial_output = p.max_output_tokens_per_trial - trial.output_tokens_reserved
        remaining_total_output = (
            p.max_output_tokens_per_benchmark - self.total.output_tokens_reserved
        )
        output_limit = min(
            p.max_output_tokens_per_request,
            remaining_trial_output,
            remaining_total_output,
        )
        if output_limit <= 0:
            raise ProviderBudgetExceeded("output-token budget exhausted")

        trial.requests += 1
        self.total.requests += 1
        trial.input_tokens += input_tokens
        self.total.input_tokens += input_tokens
        trial.output_tokens_reserved += output_limit
        self.total.output_tokens_reserved += output_limit
        return _Reservation(run_id, input_tokens, output_limit)

    def settle(self, reservation: _Reservation, response: ProviderResponse) -> None:
        if response.usage.input_tokens != reservation.input_tokens:
            raise ProviderPermanentError(
                "provider usage input_tokens differs from token-count preflight"
            )
        if response.usage.output_tokens > reservation.output_tokens:
            raise ProviderPermanentError("provider exceeded reserved output-token ceiling")
        refund = reservation.output_tokens - response.usage.output_tokens
        trial = self._trial(reservation.run_id)
        trial.output_tokens_reserved -= refund
        self.total.output_tokens_reserved -= refund

    def trial(self, run_id: str) -> _TrialBudget:
        return self._trial(run_id)


_ADJUDICATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analyses": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "summary": {"type": "string"},
    },
    "required": ["analyses", "evidence_ids", "summary"],
    "additionalProperties": False,
}

_RECONCILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["column", "tablet", "corpus"]},
                    "token_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                    "summary": {"type": "string"},
                    "requires_revisit": {"type": "boolean"},
                },
                "required": [
                    "scope",
                    "token_ids",
                    "evidence_ids",
                    "summary",
                    "requires_revisit",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You are the model decision component inside the authenticated CUC complete-column "
    "review harness. Use only the supplied column, evidence, prior decisions, and skill "
    "context. Return JSON matching the supplied schema. Do not invent evidence IDs, token "
    "IDs, evaluator targets, reviewed gold data, or external facts not present in the request."
)


class _ProviderDecisionRuntime:
    def __init__(
        self,
        binding: LiveProviderBinding,
        model_context: Mapping[str, Any],
        policy: ProviderExecutionPolicy,
        ledger: _BudgetLedger,
    ) -> None:
        self.binding = binding
        self.model_context = dict(_mapping(model_context, "model_context"))
        self.policy = policy
        self.ledger = ledger
        self.run_id = _required_text(self.model_context.get("run_id"), "model_context.run_id")
        self.calls: list[ProviderCallArtifact] = []
        self.retries = 0
        self.budget_exhausted = False
        self.observed_output_tokens = 0

    def _count_input(self, request: ProviderJSONRequest) -> int:
        attempts = 0
        while True:
            try:
                value = self.binding.client.count_input_tokens(
                    request, self.policy.budget.timeout_seconds
                )
                return _nonnegative_int(value, "counted input tokens")
            except ProviderTransientError:
                if attempts >= self.policy.budget.max_retries_per_request:
                    raise
                attempts += 1
                self.retries += 1
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderPermanentError(type(exc).__name__) from None

    def _invoke(self, request: ProviderJSONRequest) -> Mapping[str, Any]:
        counted_input = self._count_input(request)
        attempt = 0
        while True:
            try:
                reservation = self.ledger.reserve(self.run_id, counted_input)
            except ProviderBudgetExceeded:
                self.budget_exhausted = True
                raise
            effective_request = replace(
                request, max_output_tokens=reservation.output_tokens
            )
            request_digest = _digest(effective_request.safe_digest_payload())
            started = monotonic()
            try:
                response = self.binding.client.generate_json(
                    effective_request,
                    self.policy.budget.timeout_seconds,
                )
                if not isinstance(response, ProviderResponse):
                    raise ProviderPermanentError("provider client returned invalid response type")
                self.ledger.settle(reservation, response)
                if response.model != self.binding.exact_model_version:
                    raise ProviderModelIdentityError(
                        "provider response model does not match configured exact model version"
                    )
                self.observed_output_tokens += response.usage.output_tokens
                self.calls.append(
                    ProviderCallArtifact(
                        operation=request.operation,
                        attempt=attempt,
                        model=response.model,
                        request_id=response.request_id,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        latency_ms=(monotonic() - started) * 1000,
                        request_sha256=request_digest,
                        response_sha256=_digest(response.payload),
                        error_type=None,
                    )
                )
                return response.payload
            except ProviderTransientError as exc:
                self.calls.append(
                    ProviderCallArtifact(
                        operation=request.operation,
                        attempt=attempt,
                        model=self.binding.exact_model_version,
                        request_id=None,
                        input_tokens=counted_input,
                        output_tokens=None,
                        latency_ms=(monotonic() - started) * 1000,
                        request_sha256=request_digest,
                        response_sha256=None,
                        error_type=type(exc).__name__,
                    )
                )
                if attempt >= self.policy.budget.max_retries_per_request:
                    raise
                attempt += 1
                self.retries += 1
                continue
            except ProviderError as exc:
                self.calls.append(
                    ProviderCallArtifact(
                        operation=request.operation,
                        attempt=attempt,
                        model=self.binding.exact_model_version,
                        request_id=None,
                        input_tokens=counted_input,
                        output_tokens=None,
                        latency_ms=(monotonic() - started) * 1000,
                        request_sha256=request_digest,
                        response_sha256=None,
                        error_type=type(exc).__name__,
                    )
                )
                raise
            except Exception as exc:
                error = ProviderPermanentError(type(exc).__name__)
                self.calls.append(
                    ProviderCallArtifact(
                        operation=request.operation,
                        attempt=attempt,
                        model=self.binding.exact_model_version,
                        request_id=None,
                        input_tokens=counted_input,
                        output_tokens=None,
                        latency_ms=(monotonic() - started) * 1000,
                        request_sha256=request_digest,
                        response_sha256=None,
                        error_type=type(error).__name__,
                    )
                )
                raise error from None

    def adjudicate(
        self,
        state: ColumnRunState,
        token: Any,
        evidence: Any,
        skill_context: Any,
        operation_id: str,
        revisit_request: Any = None,
    ) -> TokenDecision:
        evidence_tuple = tuple(evidence)
        payload = {
            "model_context": _safe_json_value(self.model_context, "model_context"),
            "task": state.task.to_dict(),
            "snapshot": state.snapshot.to_dict(),
            "token": token.to_dict(),
            "evidence": [item.to_dict() for item in evidence_tuple],
            "prior_decisions": [item.to_dict() for item in state.decisions],
            "skill_context": _safe_json_value(skill_context, "skill_context"),
            "revisit_request": (
                None if revisit_request is None else revisit_request.to_dict()
            ),
        }
        response = self._invoke(
            ProviderJSONRequest(
                "adjudicate",
                self.binding.requested_model,
                _SYSTEM_PROMPT,
                payload,
                _ADJUDICATE_SCHEMA,
                self.policy.budget.max_output_tokens_per_request,
            )
        )
        analyses_raw = response.get("analyses")
        evidence_raw = response.get("evidence_ids")
        summary = _required_text(response.get("summary"), "summary")
        if not isinstance(analyses_raw, list) or not analyses_raw:
            raise ProviderPermanentError("analyses must be a non-empty list")
        analyses = tuple(_required_text(item, "analysis") for item in analyses_raw)
        if not isinstance(evidence_raw, list) or not evidence_raw:
            raise ProviderPermanentError("evidence_ids must be a non-empty list")
        evidence_ids = tuple(_required_text(item, "evidence_id") for item in evidence_raw)
        available_evidence = {item.evidence_id for item in evidence_tuple}
        if not set(evidence_ids).issubset(available_evidence):
            raise ProviderPermanentError("provider referenced evidence outside supplied evidence")
        prior = state.latest_decision(token.token_id)
        decision_id = "decision-provider-" + sha256(
            f"{operation_id}:{_canonical_json(response)}".encode("utf-8")
        ).hexdigest()[:24]
        return TokenDecision(
            decision_id,
            token.token_id,
            analyses,
            evidence_ids,
            summary,
            revisit_of=(
                None if revisit_request is None or prior is None else prior.decision_id
            ),
            revisit_request_id=(
                None if revisit_request is None else revisit_request.request_id
            ),
        )

    def reconcile(
        self,
        state: ColumnRunState,
        skill_context: Any,
        operation_id: str,
    ) -> ReconciliationPlan:
        payload = {
            "model_context": _safe_json_value(self.model_context, "model_context"),
            "task": state.task.to_dict(),
            "snapshot": state.snapshot.to_dict(),
            "evidence": [item.to_dict() for item in state.evidence],
            "decisions": [item.to_dict() for item in state.decisions],
            "skill_context": _safe_json_value(skill_context, "skill_context"),
        }
        response = self._invoke(
            ProviderJSONRequest(
                "reconcile",
                self.binding.requested_model,
                _SYSTEM_PROMPT,
                payload,
                _RECONCILE_SCHEMA,
                self.policy.budget.max_output_tokens_per_request,
            )
        )
        raw_findings = response.get("findings")
        if not isinstance(raw_findings, list):
            raise ProviderPermanentError("findings must be a list")
        token_ids = set(state.snapshot.token_ids)
        evidence_ids = {item.evidence_id for item in state.evidence}
        findings: list[CorpusReconciliationFinding] = []
        revisits: list[RevisitRequest] = []
        for index, raw in enumerate(raw_findings):
            item = _mapping(raw, "finding")
            raw_tokens = item.get("token_ids")
            raw_evidence = item.get("evidence_ids")
            if not isinstance(raw_tokens, list) or not raw_tokens:
                raise ProviderPermanentError("finding token_ids must be non-empty")
            if not isinstance(raw_evidence, list) or not raw_evidence:
                raise ProviderPermanentError("finding evidence_ids must be non-empty")
            finding_tokens = tuple(_required_text(value, "token_id") for value in raw_tokens)
            finding_evidence = tuple(
                _required_text(value, "evidence_id") for value in raw_evidence
            )
            if not set(finding_tokens).issubset(token_ids):
                raise ProviderPermanentError("finding references token outside column")
            if not set(finding_evidence).issubset(evidence_ids):
                raise ProviderPermanentError("finding references unknown evidence")
            requires_revisit = item.get("requires_revisit")
            if not isinstance(requires_revisit, bool):
                raise ProviderPermanentError("requires_revisit must be boolean")
            summary = _required_text(item.get("summary"), "finding summary")
            try:
                scope = ReconciliationScope(item.get("scope"))
            except (TypeError, ValueError) as exc:
                raise ProviderPermanentError("invalid reconciliation scope") from exc
            finding_id = "finding-provider-" + sha256(
                f"{operation_id}:{index}:{_canonical_json(item)}".encode("utf-8")
            ).hexdigest()[:24]
            finding = CorpusReconciliationFinding(
                finding_id,
                scope,
                finding_tokens,
                finding_evidence,
                summary,
                requires_revisit,
            )
            findings.append(finding)
            if requires_revisit:
                for token_id in finding_tokens:
                    request_id = "revisit-provider-" + sha256(
                        f"{finding_id}:{token_id}".encode("utf-8")
                    ).hexdigest()[:24]
                    revisits.append(
                        RevisitRequest(request_id, token_id, summary, finding_id)
                    )
        return ReconciliationPlan(tuple(findings), tuple(revisits))

    def artifact(self, backend_id: str, terminal_status: str) -> ProviderTrialArtifact:
        budget = self.ledger.trial(self.run_id)
        return ProviderTrialArtifact(
            backend_id=backend_id,
            run_id=self.run_id,
            execution_kind=self.binding.execution_kind.value,
            terminal_status=terminal_status,
            request_count=budget.requests,
            input_tokens=budget.input_tokens,
            output_tokens=self.observed_output_tokens,
            retries=self.retries,
            budget_exhausted=self.budget_exhausted,
            calls=tuple(self.calls),
        )


def run_live_benchmark(
    case: BenchmarkCase,
    bindings: tuple[LiveProviderBinding, ...] | list[LiveProviderBinding],
    *,
    shared_adapters: SharedBenchmarkAdapters,
    trials_per_backend: int = 1,
    execution_policy: ProviderExecutionPolicy,
) -> LiveBenchmarkRunResult:
    if not isinstance(case, BenchmarkCase):
        raise ValueError("case must be BenchmarkCase")
    if not isinstance(shared_adapters, SharedBenchmarkAdapters):
        raise ValueError("shared_adapters must be SharedBenchmarkAdapters")
    if not isinstance(execution_policy, ProviderExecutionPolicy):
        raise ValueError("execution_policy must be ProviderExecutionPolicy")
    live_bindings = tuple(bindings)
    if not live_bindings or any(
        not isinstance(item, LiveProviderBinding) for item in live_bindings
    ):
        raise ValueError("bindings must contain at least one LiveProviderBinding")
    if any(
        item.execution_kind is ExecutionKind.LIVE_PROVIDER for item in live_bindings
    ) and not execution_policy.allow_paid_live_execution:
        raise ValueError(
            "live paid provider execution requires allow_paid_live_execution=True"
        )

    ledger = _BudgetLedger(execution_policy.budget)
    runtimes: dict[str, _ProviderDecisionRuntime] = {}
    harn016_bindings: list[BenchmarkBackendBinding] = []

    for binding in live_bindings:
        def factory(model_context: dict[str, object], binding=binding):
            runtime = _ProviderDecisionRuntime(
                binding,
                model_context,
                execution_policy,
                ledger,
            )
            if runtime.run_id in runtimes:
                raise ValueError("duplicate live-provider run id")
            runtimes[runtime.run_id] = runtime
            return BackendDecisionAdapters(runtime.adjudicate, runtime.reconcile)

        harn016_bindings.append(BenchmarkBackendBinding(binding.spec, factory))

    benchmark = run_benchmark(
        case,
        tuple(harn016_bindings),
        shared_adapters=shared_adapters,
        trials_per_backend=trials_per_backend,
    )

    artifacts: list[ProviderTrialArtifact] = []
    for trial in benchmark.results:
        runtime = runtimes.get(trial.identity.run_id)
        if runtime is None:
            artifacts.append(
                ProviderTrialArtifact(
                    trial.backend_id,
                    trial.identity.run_id,
                    "unknown",
                    trial.terminal_status,
                    0,
                    0,
                    0,
                    0,
                    False,
                    (),
                )
            )
        else:
            artifacts.append(runtime.artifact(trial.backend_id, trial.terminal_status))

    expected = len(live_bindings) * trials_per_backend
    complete = (
        len(benchmark.results) == expected
        and len(artifacts) == expected
        and all(item.terminal_status == "completed" for item in benchmark.results)
    )
    return LiveBenchmarkRunResult(benchmark, tuple(artifacts), complete)
