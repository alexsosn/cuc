# HARN-016 — Model-backend benchmark research

## Goal

Build a reproducible benchmark runner for the real HARN-004 complete-column review workflow, using HARN-015 as the authoritative evaluation/feedback protocol. The benchmark must compare model backends without changing the column, skill/evidence/tool/permission policy, evaluator target, or completion contract unless the caller explicitly requests a broader system-variant comparison.

This ticket is a **runner/protocol implementation**, not a provider-SDK integration ticket and not a benchmark-corpus-selection ticket.

## Existing repository contracts

### HARN-004 is the execution engine

`agent/harness/langgraph_column_review.py` already guarantees the core scholarly runtime invariants:

- canonical `review-automatic-parsing` capability/provenance admission;
- one complete `ColumnSnapshot` as context;
- every token in textual order;
- worklist/priority hints do not narrow processing scope;
- checkpointed skill-context initialization before token review;
- append-only decisions and explicit revisits;
- reconciliation closure before completion gates;
- exact capability-derived gate order;
- evaluation only after `ColumnCompleted`;
- forwarded `ParsingEvaluationRecord` must bind to the exact completed state.

HARN-016 should invoke that graph rather than reimplement traversal or completion.

### HARN-015 already defines benchmark identity and evaluator separation

`ParsingRunIdentity` already contains:

- complete safe workload identity via `ParsingWorkloadRef`;
- model provider/id/version;
- model-config SHA-256.

`ParsingWorkloadRef` already captures corpus/tablet/column, snapshot/provenance, repository revision, capability version/provenance, tool policy, evidence policy, and permission policy.

`compare_evaluation_records(..., ignore_model_identity=True)` already checks model-backend fairness across workload and evaluator-target dimensions while allowing model identity to differ. HARN-016 should use this helper rather than creating a weaker comparability definition.

HARN-015 also structurally separates:

- model-safe workload metadata;
- evaluator-only `EvaluationTarget` (reviewed/gold references and scorer provenance);
- post-execution deterministic measurements and expert feedback.

The benchmark must preserve this separation all the way through execution.

### HARN-005 is observability only

The newly merged Langfuse sidecar can ingest parsing/development traces and HARN-015 measurements, but HARN-016 correctness must not depend on Langfuse. Benchmark records should remain deterministic vendor-neutral data that can optionally be exported later.

## Current provider reproducibility reality (checked 2026-09-10)

A universal provider-level determinism contract is not viable:

- OpenAI documents `seed` as a **best-effort** deterministic sampling control and exposes a backend/system fingerprint to detect backend changes; determinism is explicitly not guaranteed.
  - https://developers.openai.com/api/reference/java/resources/completions/methods/create
- Gemini's current generation API exposes an optional decoding `seed`, but model defaults for sampling parameters vary by model.
  - https://ai.google.dev/api/generate-content
- Anthropic currently deprecates/removes some classic sampling parameters (`temperature`, `top_p`, `top_k`) for newer models, so requiring one cross-provider sampling schema would itself bias or exclude backends.
  - https://docs.anthropic.com/en/docs/about-claude/model-deprecations

Consequences:

1. `model_provider`, `model_id`, explicit `model_version`, and `model_config_sha256` remain mandatory, as HARN-015 already requires.
2. Provider-native reproducibility metadata (seed, backend fingerprint, deployment/region, etc.) must be optional opaque **post-run metadata/artifact refs**, never mandatory fields that all providers must fake.
3. Repeated trials must be supported. A single seeded run is not evidence of deterministic equivalence across providers.
4. Trial order should be deterministic and interleaved across backends (round-robin by trial) rather than executing all trials for provider A before provider B, reducing time/order drift in hosted backends.

## Critical leakage boundary

A naive API such as:

```text
backend_factory(case, evaluation_target) -> ColumnReviewAdapters
```

is unacceptable: it gives model-side code direct access to evaluator-only reviewed/gold references before execution.

The runner should instead compose two layers.

### Shared benchmark adapters

Deterministic/shared operations that are held constant for a benchmark case:

- `initialize_skill_context`
- `collect_evidence`
- `verify_completion`
- `evaluate`

The `evaluate` adapter may access the HARN-015 `EvaluationTarget`, but it is owned by the benchmark/evaluator layer and is never passed to model code.

### Backend decision adapters

Backend-specific operations receive only safe runtime/model context:

- `adjudicate`
- `reconcile`

The backend factory receives `ParsingRunIdentity.model_context_metadata()` or an equivalent safe structure. It does not receive `EvaluationTarget`, deterministic scores, reviewed expected analyses, or expert feedback.

This matches the current HARN-004 seam: evidence collection and completion/evaluation are already explicit adapters outside scholarly adjudication.

## Benchmark case and trial identity

A benchmark case should freeze the input-side dimensions once:

- a pristine initial `ColumnRunState` (cursor 0; no evidence/decisions/revisits/gates/completion);
- complete snapshot and canonical capability/gates;
- tool/evidence/permission policy digests;
- evaluator-target identity/provenance, held only by the evaluator side;
- shared adapter factory/provenance;
- deterministic benchmark protocol version.

Each backend trial gets:

- a unique `run_id` derived from case/backend/trial identity;
- the same task fields except `task_id/run_id`;
- the exact same immutable `ColumnSnapshot`;
- a fresh graph/checkpointer and fresh backend adapters (no cross-trial state bleed);
- provider/model/version/config fingerprint from the selected backend;
- deterministic trial index and schedule position.

The runner must reject non-pristine initial state. Benchmarking a partially reviewed/resumed state would make backend comparisons depend on hidden prior decisions.

## Backend specification

Use a provider-neutral immutable backend descriptor, approximately:

```text
BenchmarkBackendSpec
  backend_id
  model_provider
  model_id
  model_version
  model_config_sha256
  decision_adapter_factory
```

The config hash is authoritative for cross-run identity. Human-readable/provider-native config can live in an external artifact referenced by the result; the benchmark core should not normalize provider-specific temperature/seed/thinking schemas.

The factory should receive only the safe run identity/workload metadata and return backend decision adapters. Tests use deterministic fakes; real provider integrations can be plugged in without changing the benchmark protocol.

## Execution result

A benchmark trial must be representable even when the model/backend fails. Do not abort the entire matrix on one provider exception.

Record at minimum:

- case/backend/trial/run identity;
- terminal status (`completed`, `gate-failed`, `backend-error`, etc.);
- final `ColumnRunState` when available;
- `ParsingEvaluationRecord` only when HARN-004 actually completed/evaluated;
- safe exception class (never raw provider exception text in the core record);
- optional provider-native run/ref metadata;
- schedule position.

Completed results must prove:

- full initial traversal (`initial_pass_complete`);
- no unresolved required revisit;
- completion gates satisfied;
- evaluation bound to exact final decision revision.

Incomplete/error results remain benchmark evidence for reliability/unresolved-rate comparisons; they simply lack post-completion morphology measurements.

## Comparison modes

### Model-only fairness

Use HARN-015 `compare_evaluation_records(..., ignore_model_identity=True)` for completed trials. This requires workload, capability, tool/evidence/permission policy and evaluator target to match exactly while allowing model identity/config to differ.

Any mismatch makes the pair non-comparable as a **model-only** benchmark, and the exact mismatch dimensions must be returned.

### System-variant comparison

The acceptance criterion also asks to compare model+skill/tool versions separately. This should not silently relabel an unfair model-only comparison as fair.

Provide an explicit broader mode that:

- still requires the same corpus/tablet/column, snapshot/source provenance, repository/data basis, permission policy and evaluator target;
- allows explicitly reported differences in model identity, capability identity/provenance, tool policy and evidence policy;
- returns those changed dimensions as part of the comparison record.

This supports intentional system-ablation comparisons while keeping them distinct from model-only claims.

## Repeated trials and ordering

Support `trials_per_backend >= 1`.

Deterministic schedule for backends `[A, B, C]` and 3 trials:

```text
A0, B0, C0, A1, B1, C1, A2, B2, C2
```

Do not randomize unless a future protocol version records a seed/order artifact. A deterministic round-robin is replayable and avoids systematic all-A-then-all-B timing bias.

Do not average authoritative morphology formulas inside the benchmark runner. Raw HARN-015 `EvaluationMeasurement`s are the primary result. A later reporting layer may summarize repeated-trial distributions while preserving the raw records.

## Efficiency and cost

HARN-015 `EfficiencyMetrics` already distinguishes missing values from zero. The benchmark should forward it unchanged from each evaluation record.

Provider-native latency/token/cost fields are allowed only when grounded in the backend/evaluator and should map into HARN-015 efficiency fields or explicit artifact refs. The benchmark core must not infer missing cost or tokens as zero.

## Expert feedback

Expert feedback remains post-execution evaluator data attached to the exact `ParsingEvaluationRecord` and decision revision.

The backend factory/model context must never receive:

- feedback disposition;
- corrected analyses;
- reviewer ref/rationale;
- reviewed target refs;
- deterministic metric values.

The benchmark may compare acceptance/correction/reject counts only from completed evaluation records whose feedback is already validated by HARN-015.

## TDD implications

Tests should first require:

1. immutable backend/case/trial contracts and deterministic serialization;
2. pristine complete-column case admission; partial/resumed state rejected;
3. deterministic round-robin trial schedule;
4. fresh run/task ID per backend/trial with identical snapshot/capability/gates/policies;
5. backend factory receives model-safe metadata only (gold/feedback target absent);
6. shared evaluator receives evaluator target without exposing it to backend decision adapters;
7. HARN-004 graph is actually invoked and every token is processed in textual order;
8. one backend failure becomes a recorded `backend-error` and does not prevent later matrix trials;
9. completed trial requires a bound HARN-015 evaluation record;
10. model-only comparison delegates to HARN-015 comparability and rejects any non-model/evaluator drift;
11. explicit system-variant mode allows only its named dimensions and reports all changed dimensions;
12. efficiency values are forwarded without filling missing values with zero;
13. expert feedback/evaluation target never appears in backend safe context;
14. no provider SDK/Langfuse dependency in benchmark core;
15. full existing suite remains green.

## Non-goals

- choosing the definitive held-out whole-column benchmark set;
- committing provider credentials;
- embedding OpenAI/Anthropic/Gemini SDK clients in the benchmark core;
- inventing a universal temperature/seed schema;
- reimplementing HARN-015 morphology metrics;
- using HARN-003's seven-token regression fixture as the model benchmark corpus;
- changing `review-automatic-parsing` semantics;
- converting expert feedback into GitHub development issues (HARN-017).
