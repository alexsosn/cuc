# HARN-016 — model-backend benchmark protocol

## Status

Research and implementation plan. This ticket adds a reproducible, provider-neutral benchmark layer over the already-completed HARN-004 parsing runtime and HARN-015 evaluation contracts. It does **not** add vendor SDKs, credentials, or a second morphology scorer.

## Research findings

### Existing contracts already encode the fixed workload

`ParsingWorkloadRef` already carries every dimension that must remain fixed across model arms:

- corpus/tablet/column and complete snapshot identity/provenance;
- repository revision;
- capability name/version/provenance;
- tool policy digest;
- evidence policy digest;
- permission policy digest.

`ParsingRunIdentity` separately carries provider, model id, exact model version, and model-config digest. Therefore the benchmark must compare workload identity while intentionally allowing model identity to differ; it must not invent another workload fingerprint schema.

`compare_evaluation_records(..., ignore_model_identity=True)` already checks workload dimensions, evaluation target, schema version, and decision revision. HARN-016 should reuse it as the pairwise gate rather than duplicate comparability logic.

### Evaluation and efficiency are already separated

`ParsingEvaluationRecord` keeps deterministic/supplementary measurements, expert feedback, and `EfficiencyMetrics` separate. `EfficiencyMetrics` already supports model/tool calls, retries, latency, token counts, and cost/currency. A benchmark report can therefore preserve the required quality-first presentation without creating a weighted score that mixes quality and cost.

The reviewed-morphology scorer remains behind HARN-015 `build_parsing_evaluation`; HARN-016 consumes finished `ParsingEvaluationRecord` values and never calls or reimplements scorer internals.

### Leakage protection already has an authoritative boundary

HARN-015 rejects held-out target values/markers from model workload context and reconciliation context. The benchmark must strengthen—not bypass—that separation by exposing a model/backend request that contains only run identity plus explicitly supplied model context. `EvaluationTarget`, reviewed/gold paths, scorer inputs, and post-run evaluation records are evaluator-side values and must never be passed to a backend callable.

### HARN-004 is the production execution seam

The real parser runtime accepts narrow adapters for skill-context initialization, evidence collection, adjudication, reconciliation, completion verification, and evaluation. HARN-016 should not teach the benchmark runner how to parse tokens. A live provider integration can construct HARN-004 adapters and return the resulting HARN-015 evaluation record; the benchmark layer only enforces fairness and aggregates records.

### Live vendor execution is a separate operational concern

The repository currently has no safe multi-provider credential/cost policy for running paid OpenAI/Anthropic inference in CI. Coupling secrets and vendor SDKs into the benchmark contract would make the core protocol non-replayable and provider-specific. HARN-016 will therefore define/test the provider-neutral runner with deterministic backend adapters. A live benchmark executor may be added separately once credential, budget, retry, and version-pinning policy is explicit; no synthetic test result may be represented as a real vendor quality result.

## Design

Add `agent/harness/model_benchmark.py` as a framework-neutral layer over `ParsingEvaluationRecord`.

### `BenchmarkBackend`

Immutable identity plus one callable:

- `provider`
- `model_id`
- `model_version`
- `model_config_sha256`
- `run(request) -> ParsingEvaluationRecord`

All identity fields are required. Distinct benchmark arms must have distinct complete model identities.

### `BenchmarkRequest`

Contains only values legitimately visible to the model execution side:

- `ParsingRunIdentity` for the arm;
- opaque, caller-supplied `workload_context` after HARN-015 leakage checks;
- repetition index.

It deliberately has no `EvaluationTarget`, reviewed path, gold rows, scorer configuration, expert feedback, or post-run scores.

### `BenchmarkProtocol`

Binds:

- one reference `ParsingWorkloadRef`;
- one evaluator-side `EvaluationTarget`;
- repetitions per arm;
- an immutable workload-context mapping.

Construction validates HARN-015 leakage rules before any backend is called.

### Execution

`run_benchmark(protocol, backends)`:

1. Require at least two distinct model identities.
2. For each backend and repetition, derive a unique run id while preserving the exact protocol workload and backend identity.
3. Pass only `BenchmarkRequest` to the backend.
4. Require the returned `ParsingEvaluationRecord.identity` to exactly match the issued arm identity/run id.
5. Require its target to equal the evaluator-side target.
6. Require pairwise comparability with the first successful record while ignoring model identity.
7. Do not silently drop failed/incomplete arms: backend exceptions fail the benchmark rather than improving an arm by survivor bias.
8. Return records in deterministic backend-identity/repetition order independent of input iterable order.

### Report

`BenchmarkReport` contains separate sections:

- model identities and fixed workload/target identity;
- per-run quality measurements (deterministic and supplementary, unchanged);
- per-run expert-feedback counts/dispositions;
- per-run efficiency metrics;
- deterministic per-arm aggregates for numeric deterministic measurements and efficiency fields where all values are present;
- stability/repetition count.

No composite winner score is defined. Consumers compare morphology/behavior/expert quality first and inspect efficiency separately.

For aggregation, a measurement is aggregate-eligible only when every repetition of an arm reports the same measurement name/scope/source/kind and numeric value type. Missing or schema-drifting measurements are rejected rather than averaged selectively. Cost is aggregated only when currency is identical across all repetitions for that arm.

## TDD gates

Write tests before implementation.

1. **Identical workload** — two different model identities with the same `ParsingWorkloadRef` and target are accepted; any repository/snapshot/capability/tool/evidence/permission/target drift is rejected.
2. **Exact model identity** — blank provider/model/version/config is impossible; duplicate complete model identities cannot masquerade as two arms; returned record identity must match the issued request.
3. **Complete-column semantics** — benchmark accepts only evaluation records whose HARN-015 behavior says expected token count equals visited initial tokens and column completed; incomplete/skipped runs fail instead of disappearing from the report.
4. **No evaluator leakage** — backend request has no target/gold/reviewed/scorer field and protocol construction rejects held-out target material inside workload context.
5. **Repetitions** — every arm must return exactly the configured repetition count; backend failure aborts the benchmark; deterministic output order does not depend on backend input order.
6. **Quality/efficiency separation** — report preserves quality measurements unchanged and aggregates efficiency independently; no composite score exists.
7. **Aggregation determinism** — equal records in different input/backend order produce byte-identical JSON; missing or inconsistent metric schemas/currencies are rejected rather than cherry-picked.
8. **No scorer duplication** — benchmark module does not import `reviewed_evaluation` or call scorer functions.
9. **No provider coupling** — benchmark module imports no OpenAI/Anthropic/LangChain/LangGraph/Langfuse SDK.

## Independent adversarial review rubric

Reject if any of these are possible:

- one model arm gets a different snapshot, skill, tool/evidence/permission policy, repository revision, or eval target;
- provider/model/version/config identity can be omitted or silently rewritten;
- a duplicate/cached arm is counted as a second model backend;
- held-out target/gold/reviewed/scorer data reaches the backend request/context;
- skipped or failed columns are omitted from averages;
- only flagged/worklist tokens can constitute a benchmark workload;
- morphology scoring is recomputed in the benchmark layer;
- numeric metrics are averaged across inconsistent schemas or cost currencies;
- quality and cost are collapsed into one score;
- result ordering depends on backend input order;
- benchmark code mutates `reviewed/**` or `auto_parsing/**`;
- provider SDKs leak into framework-neutral benchmark contracts.

## Non-goals

- choosing a winning production model;
- executing paid vendor inference from CI;
- storing provider API keys;
- changing HARN-004 parsing semantics;
- changing HARN-015 scorer/evaluation semantics;
- Langfuse transport changes;
- expert-review scheduling or collection.
