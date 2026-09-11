# HARN-022 live provider execution plan

## Scope

HARN-016 already owns comparable complete-column scheduling and evaluation. HARN-022 adds a live provider boundary around its backend factory without changing column traversal, completion gates, held-out evaluation, or comparison semantics.

## Research snapshot — 2026-09-11

Official provider documentation checked immediately before implementation:

- OpenAI Responses API: https://developers.openai.com/api/reference/cli/resources/responses/methods/create
- OpenAI Responses input-token count: https://developers.openai.com/api/reference/typescript/resources/responses/subresources/input_tokens
- OpenAI model snapshots: https://developers.openai.com/api/docs/models/gpt-5.4
- Anthropic Messages API: https://platform.claude.com/docs/en/api/http/messages/create
- Anthropic token count API: https://platform.claude.com/docs/en/api/http/messages/count_tokens
- Anthropic model ID/version semantics: https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions
- Anthropic rate limits: https://platform.claude.com/docs/en/api/rate-limits

Findings used by the design:

1. Both providers expose server-side input-token counting and response usage. HARN-022 can therefore reject an over-budget request before generation and record actual input/output use after generation.
2. OpenAI exposes snapshot model IDs for models that have snapshots; rolling aliases must not be treated as exact versions when a more exact provider identifier exists.
3. Anthropic documents Claude 4.6+ dateless model IDs as pinned model snapshots; earlier short aliases can still point at dated snapshots.
4. Provider APIs expose the model identifier used in the response. HARN-022 must compare it with the configured exact model version and fail closed on drift.
5. 429/rate-limit responses and transient 5xx/network failures need bounded retries. A timeout/retry is conservatively budgeted as another potentially billable generation attempt.

## Design

### Provider boundary

Add `harness.live_providers` with a narrow provider-neutral client protocol:

- `count_input_tokens(request, timeout_seconds) -> int`
- `generate_json(request, timeout_seconds) -> ProviderResponse`

The provider client owns credentials. Scholarly adapters never receive API keys. The built-in OpenAI and Anthropic HTTP clients read only the configured environment-variable name and never serialize the secret.

### Exact model identity

Each live binding supplies:

- HARN-016 `BenchmarkBackendSpec` with provider/model/config identity;
- the requested provider model ID;
- the exact expected response model/version;
- an explicit provenance note for how that exact version was selected.

Generic values such as `latest`, `default`, `auto`, or `unknown` are rejected. Every generation response must report the configured expected model version.

### Budget policy

Budgeting is shared across the full benchmark and tracked separately per trial. Before each generation attempt:

1. count input tokens using the provider token-count endpoint;
2. reject if reserving those tokens would exceed trial or benchmark input limits;
3. reserve one generation request and the maximum permitted output tokens before the call;
4. on success, replace the reserved output allowance with actual output usage;
5. on transient failure, keep the conservative reservation and retry only while the retry ceiling and remaining budget allow it.

This gives hard request/input/output ceilings without embedding provider prices that change independently of the repository. Cost remains a separately reportable metric when an operator supplies a pricing layer later.

### Prompt/evaluation boundary

Provider prompts receive only HARN-004 state, current evidence, skill context, revisit context, and HARN-016 model-safe identity metadata. `EvaluationTarget`, reviewed gold references, scorer identity, and expert feedback never enter provider requests.

Provider JSON is converted into existing `TokenDecision`, `CorpusReconciliationFinding`, and `RevisitRequest` domain objects and is then revalidated by HARN-016/HARN-004 transitions.

### Artifacts

Live execution emits safe structured call artifacts containing provider, exact model, request ID when available, usage, latency, retry count, execution kind (`live-provider` or `test-double`), and request/response digests. Prompt text, response text, credentials, and held-out evaluation targets are not retained in benchmark artifacts.

The HARN-022 wrapper marks the benchmark `complete=false` unless every configured arm and repetition completed. Consumers must not compare an incomplete run as a completed benchmark.

## TDD gate

RED tests cover:

- missing credential before network access;
- ambiguous/exact model identity and response-model drift;
- pre-generation input-budget rejection;
- output/request budget exhaustion;
- retry ceiling and later-arm isolation;
- partial benchmark status;
- held-out target absence from provider-visible payloads;
- secret/error-message redaction;
- `live-provider` vs `test-double` provenance;
- OpenAI Responses and Anthropic Messages request/response adapters with injected HTTP transport.

No live paid inference runs in ordinary PR CI. Live calls require credentials plus an explicit `allow_paid_live_execution=True` execution policy.