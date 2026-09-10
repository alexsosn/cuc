# HARN-016 — Model-backend benchmark implementation plan

## Objective

Implement a provider-neutral benchmark protocol around the already-complete HARN-004 column-review graph. The benchmark compares model decision backends on identical complete-column workloads while preserving HARN-015 evaluator separation and exact comparability semantics.

The benchmark core must not contain provider SDK clients, Langfuse requirements, morphology scoring logic, or a second parsing state machine.

## Runtime module

Add `agent/harness/model_benchmark.py`.

It may import:

- `harness.column_state` for immutable column state/task cloning;
- `harness.langgraph_column_review` to compile and execute the real graph;
- `harness.parsing_evaluation` for run/workload identity, evaluation records, and authoritative model-only comparability.

It must not import OpenAI, Anthropic, Gemini, Langfuse, LangChain, or provider-specific configuration types.

## Data/runtime separation

### Serializable benchmark data

`BenchmarkCase`

- protocol version;
- case id;
- pristine `ColumnRunState` with complete snapshot;
- tool/evidence/permission policy SHA-256 values;
- evaluator-only `EvaluationTarget`.

Admission requires a truly pristine run state: cursor at zero, no evidence, decisions, revisits, findings, gates, completion, event receipts, or closed reconciliation. A partial/resumed state is not a fair benchmark input.

`BenchmarkBackendSpec`

- backend id;
- model provider;
- model id;
- explicit model version;
- model config SHA-256.

No callable or provider client belongs in the serializable descriptor.

`ScheduledTrial`

- backend id;
- zero-based trial index;
- schedule position.

`BenchmarkTrialResult`

- case/backend/trial/schedule identity;
- exact `ParsingRunIdentity`;
- terminal status;
- final column state when available;
- HARN-015 evaluation only when the graph actually completed;
- safe exception class only for backend failures;
- optional provider artifact refs.

### Runtime-only bindings

`BackendDecisionAdapters`

- `adjudicate`;
- `reconcile`.

`BenchmarkBackendBinding`

- serializable `BenchmarkBackendSpec`;
- factory receiving only `ParsingRunIdentity.model_context_metadata()` and returning fresh decision adapters.

`SharedBenchmarkAdapters`

- skill/worklist initialization;
- evidence collection;
- completion verification;
- evaluator callback.

The shared evaluator callback receives the exact `ParsingRunIdentity` and evaluator-only `EvaluationTarget` through the benchmark-owned closure. Neither object is passed wholesale to backend code; backend code receives only the safe model-context projection.

## Trial construction

For every scheduled backend/trial:

1. derive deterministic run id from case/backend/trial;
2. clone the pristine task with only `task_id/run_id` changed;
3. reuse the exact immutable `ColumnSnapshot`;
4. construct `ParsingWorkloadRef` from the cloned task plus case policy digests;
5. construct `ParsingRunIdentity` from workload + backend spec;
6. call the backend factory with only `identity.model_context_metadata()`;
7. compose HARN-004 adapters:
   - shared initialization/evidence/gates/evaluation;
   - backend adjudication/reconciliation;
8. compile a fresh HARN-004 graph/checkpointer;
9. invoke with a fresh thread id;
10. validate/store result.

No graph/checkpointer/backend adapter instance is reused across trials.

## Trial ordering

For backend list `[A, B, C]` and `trials_per_backend=3`, schedule exactly:

`A0, B0, C0, A1, B1, C1, A2, B2, C2`.

Reject duplicate backend ids and non-positive/bool trial counts.

The deterministic round-robin reduces systematic hosted-backend timing drift while remaining replayable without an additional RNG protocol.

## Backend-failure isolation

Backend-specific adapter calls are wrapped by a narrow internal exception boundary. Provider/model exceptions become a trial result with:

- terminal status `backend-error`;
- safe exception class name;
- no raw exception message;
- no evaluation.

The matrix continues with later scheduled trials.

Do not silently convert shared evaluator, state-invariant, or benchmark-programming failures into provider failures. Those remain correctness errors and should fail the benchmark invocation.

`gate-failed` is a normal HARN-004 terminal result, not a backend exception.

## Completion acceptance

A `completed` trial is valid only when:

- HARN-004 final state has `initial_pass_complete`;
- reconciliation is closed and required revisits are resolved;
- `completion` exists with the exact task-required gates;
- evaluation is a `ParsingEvaluationRecord`;
- evaluation decision revision equals final state revision;
- evaluation identity equals the trial's exact run identity.

The benchmark does not compute morphology metrics itself.

## Leakage boundary

Backend factory/model code must never receive:

- `EvaluationTarget`;
- reviewed/gold ref or provenance;
- scorer id/provenance;
- feedback protocol digest;
- deterministic evaluation measurements;
- expert feedback/corrected analyses.

The only backend-factory input is HARN-015 safe model-context metadata.

## Comparisons

### Model-only

For completed trials call:

`compare_evaluation_records(left.evaluation, right.evaluation, ignore_model_identity=True)`

Return the HARN-015 mismatch dimensions without weakening or recomputing them.

### System-variant

Use a separate explicit comparison mode. Require equality for:

- corpus/tablet/column;
- snapshot id/provenance;
- repository revision;
- permission policy;
- complete evaluator target/protocol.

Allow, but report, differences in:

- model provider/id/version/config;
- capability name/version/provenance;
- tool policy;
- evidence policy.

A model-only mismatch must never be silently relabeled as model-only comparable because system-variant mode exists.

## Serialization

Data contracts use deterministic `to_dict()` / `to_json()` projections. Serialization must contain no callable/provider client and preserve `None` efficiency values from HARN-015 rather than inventing zero.

Evaluator-only fields may exist in the benchmark case/result artifact; they must not be included in backend safe metadata.

## TDD RED gate

Before implementation, add tests covering:

1. backend spec/case validation and deterministic serialization;
2. partial/resumed case rejection;
3. duplicate backends and invalid trial counts rejected;
4. deterministic round-robin schedule;
5. fresh run ids, state, graph and backend factory per trial;
6. backend factory sees safe model metadata only;
7. real HARN-004 graph processes every token in textual order;
8. shared evaluator receives target and exact run identity without target leaking to backend;
9. one backend exception records `backend-error` without raw message and later trials still run;
10. completed result binds exact final state/evaluation identity;
11. model-only comparison delegates to HARN-015 and detects non-model drift;
12. system-variant mode reports allowed changed dimensions and rejects frozen-dimension/evaluator drift;
13. HARN-015 efficiency/feedback data passes through unchanged;
14. benchmark core has no provider/Langfuse imports;
15. full existing suite remains green.

## Independent adversarial review

Reject the PR if review can demonstrate any of the following:

- flagged/worklist rows become the benchmark processing scope;
- a partial state can enter as a benchmark case;
- different trials share graph checkpoints or mutable backend state unintentionally;
- evaluator/gold/feedback data reaches backend factory/model context;
- model-only comparison ignores tool/evidence/capability/evaluator drift;
- system-variant comparison hides rather than reports changed dimensions;
- backend exceptions expose raw provider messages/secrets;
- one backend exception aborts unrelated later trials;
- missing cost/token/latency values are coerced to zero;
- benchmark code reimplements HARN-015 scoring/comparability;
- provider SDK or Langfuse becomes a correctness dependency;
- generated `auto_parsing/**` or upstream `DT-UCPH/cuc` is modified.

Every blocking review finding enters the same test-first RED → minimal fix → full GREEN cycle before finalization.
