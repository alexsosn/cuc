# HARN-015 — Parsing evaluation and expert-feedback protocol

## Goal

Define a framework-neutral protocol for evaluating the **real complete-column parsing workflow** so output quality, agent behavior, efficiency, expert feedback, and later model comparisons remain comparable across exact corpus/model/skill/tool revisions.

The protocol must not create a second morphology scorer. `agent/reviewed_evaluation/` and `agent/scripts/score_reviewed_morphology.py` remain authoritative for reviewed-vs-automatic morphology agreement.

## Existing boundaries

### Deterministic morphology evaluation

HARN-003 established a fast seven-token regression fixture under `agent/tests/fixtures/harn_003_reviewed_morphology/` and pins the complete current `MetricSummary` baseline. It deliberately contains ambiguity, overgeneration, unresolved DULAT evidence, and an ordered formula-context case; it is **not** a whole-column benchmark.

The existing `reviewed_evaluation` package already provides:
- per-token `PerIdAgreement` including exact-set match, precision/recall/F1/Jaccard, missing/extra analyses, coverage, and option-count error;
- aggregate `MetricSummary` including exact-set accuracy, macro/micro precision/recall/F1, Jaccard, gold coverage, and mean extra/missing/option-count error;
- unambiguous/ambiguous splits;
- `FileComparison` and `AggregateComparison` serialization.

HARN-015 therefore **adapts/copies authoritative results** into a run-level protocol. It never recomputes those metrics.

### Parsing runtime state

HARN-018 now supplies `ColumnRunState` with:
- exact `ColumnTask`/`ColumnSnapshot` identity;
- complete-column initial traversal;
- append-only token decisions and alternatives;
- evidence provenance;
- explicit revisit request→decision provenance;
- reconciliation findings;
- decision revision and revision-bound completion gates;
- deterministic checkpoint serialization.

Evaluation records should refer to this state by stable IDs/revisions rather than duplicating the full state payload.

### Development-controller evals

HARN-002's generic `EvalResult` belongs to the GitHub issue development controller and is keyed by change/head SHA. It is not the scholarly parsing-evaluation schema and should not be overloaded for token/column expert feedback or model benchmarking.

## Protocol separation

Evaluation needs three separate surfaces.

### A. Output quality

Authoritative deterministic metrics from reviewed morphology:
- exact-set agreement;
- macro/micro precision, recall, F1 and Jaccard;
- gold coverage;
- extra/missing option counts and option-count error;
- ambiguous vs unambiguous splits;
- token-level missing/extra analyses.

Additional deterministic column gates may be recorded as pass/fail measurements when an existing tool produced them:
- review-status completion;
- lint/reconstruction validity;
- column/corpus consistency checks when deterministic tooling exists.

No model/LLM judge may override a deterministic score or gate.

### B. Agent behavior

Run-level behavior is distinct from output correctness. Seed measurements:
- expected token count vs visited initial tokens;
- explicit revisit count;
- unresolved revisit count;
- reconciliation finding count / unresolved required finding count;
- preserved-alternative count;
- completed vs incomplete column state;
- unsupported/harmful-edit counts only when grounded in deterministic comparison or expert feedback.

The protocol records values and provenance; HARN-004/HARN-016 runtime code will produce them.

### C. Efficiency

Keep optional efficiency measurements separate from quality:
- model calls;
- tool calls;
- retries;
- wall latency in milliseconds;
- input/output tokens where provider reporting is available;
- monetary cost with explicit currency/source where available.

Missing efficiency data is `None`/absent, never silently zero.

## Comparable run identity

Define a `ParsingRunIdentity` containing enough immutable metadata to decide whether two runs are meaningfully comparable:

- `run_id`;
- corpus/tablet/column;
- `snapshot_id` + snapshot/source provenance;
- repository revision;
- capability canonical name, contract version and provenance SHA-256;
- model provider, model id and explicit model/version/revision string;
- model configuration fingerprint or stable config reference;
- tool/evidence policy fingerprint;
- permission-policy fingerprint.

The later HARN-004 adapter must build capability fields from the exact HARN-008 capability and HARN-018 task, not model-authored strings.

A comparison helper should report **which dimensions differ**, not merely a boolean. Model-backend comparisons may intentionally differ in model identity but must match the workload/capability/tool/evidence/permission dimensions.

## Leakage boundary

Gold/reviewed morphology and expert feedback are evaluator data. They must not be part of model workload/context metadata merely because they exist in the same evaluation record.

Represent this structurally:

- `ParsingWorkloadRef` — safe input-side identity: corpus/tablet/column, snapshot/source provenance, repository revision, capability/tool/evidence/permission policy; **no reviewed gold, expected analyses, expert corrections, or evaluation scores**.
- `EvaluationTarget` — evaluator-only refs to reviewed/gold data and scorer baseline/provenance.
- `ParsingEvaluationRecord` — joins workload/run identity, deterministic scores, behavior/efficiency measurements, and feedback **after execution**.

Expose a `model_context_metadata()`/equivalent that serializes only the workload/run configuration safe for the parser. Tests should fail if gold/feedback fields can leak through this surface.

## Score representation

Use a generic immutable `EvaluationMeasurement`:
- stable `name`;
- kind: `numeric`, `boolean`, `categorical`, or `text`;
- value;
- scope: `token`, `column`, or `run`;
- optional token/decision id for token scope;
- authoritative source (`reviewed-morphology-scorer`, `column-state`, `expert-feedback`, etc.);
- source/provenance refs.

Rules:
- numeric values must be finite;
- boolean is not accepted as numeric;
- names are unique per `(scope, token_id, name, source)` where a set requires uniqueness;
- qualitative text is non-aggregate evidence, not a numeric substitute;
- deterministic measurements can be identified separately from supplementary model-judge measurements.

For the reviewed scorer adapter, copy the complete `MetricSummary.to_dict()` values without formula duplication. Namespace them (for example `morphology.exact_set_accuracy`) to avoid collisions.

## Expert feedback

Define `ExpertFeedback` as immutable evaluation evidence attached to exact run/state provenance:

- stable `feedback_id`;
- `run_id`;
- `decision_revision`;
- scope: column or token;
- token id and decision id when token-scoped;
- disposition such as `accept`, `correct`, `reject`, `needs-review`;
- optional corrected analyses, preserving multiple alternatives;
- reviewer reference (stable identity/pseudonymous ref, not free-form display name as provenance);
- rationale/comment;
- evidence/source refs supporting the feedback.

Validation:
- token-scoped feedback requires token + decision identity;
- column-scoped feedback must not pretend to target a token decision;
- a correction requires at least one corrected analysis;
- non-correction feedback cannot smuggle corrected analyses;
- decision revision must be non-negative;
- feedback does not mutate `ColumnRunState`; it is an evaluation artifact that may later produce a systematic-development finding.

A later runtime adapter should additionally verify referenced token/decision actually exists in the exact `ColumnRunState`; HARN-015 can provide a pure validation helper for this.

## Evaluation record

`ParsingEvaluationRecord` should contain:
- protocol/schema version;
- `ParsingRunIdentity`;
- `EvaluationTarget`;
- deterministic measurements;
- supplementary measurements (if any);
- expert feedback;
- optional efficiency measurements;
- references to raw scorer/trace artifacts.

Deterministic serialization must make records suitable for:
- checked-in regression fixtures;
- CI artifacts;
- HARN-016 model comparison;
- optional Langfuse ingestion.

## Langfuse mapping (optional sidecar)

Current Langfuse supports datasets/experiment runs with arbitrary external application logic plus scores attached to traces/observations/sessions/dataset runs. Scores support numeric, categorical, boolean and text data. Deterministic code-generated metrics can therefore be forwarded rather than recomputed in Langfuse.

Design implication:
- `EvaluationMeasurement.numeric` -> numeric score;
- boolean gates -> boolean score;
- expert dispositions -> categorical score;
- qualitative expert rationale -> feedback artifact / text annotation or score comment rather than an aggregate numeric score;
- complete protocol metadata -> trace/experiment metadata.

Langfuse availability is never required to construct or validate a protocol record.

Official references checked during research:
- https://langfuse.com/docs/evaluation/scores/overview
- https://langfuse.com/docs/evaluation/experiments/data-model
- https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk

## TDD plan

Write protocol tests before implementation. RED tests should require:

1. `ParsingRunIdentity` deterministic round-trip and required model/capability/workload provenance;
2. workload-comparability report catches snapshot, repository, capability, tool/evidence and permission-policy drift while allowing model identity to be intentionally ignored for backend comparison;
3. model-context serialization contains no gold/reviewed target, expected analyses, expert correction, or evaluation score fields;
4. `EvaluationMeasurement` validates type/scope/finite numeric semantics and token identity;
5. reviewed scorer adapter copies **all** authoritative `MetricSummary` fields exactly and does not contain scorer formulas;
6. HARN-003 fixture expected summary maps losslessly to namespaced deterministic measurements;
7. expert feedback requires exact run/revision and correct token/decision targeting semantics;
8. correction feedback preserves multiple alternative corrected analyses through round-trip;
9. pure helper rejects expert feedback that targets an unknown token/decision/revision of a `ColumnRunState`;
10. efficiency fields distinguish missing from zero;
11. evaluation record round-trip is deterministic and vendor-neutral;
12. source module contains no Langfuse/LangGraph/LangChain/provider SDK dependency;
13. full existing test suite remains green.

## Implementation plan

1. Add tests in `agent/tests/test_harness_parsing_evaluation.py` using HARN-003 fixture baseline and small HARN-018 state fixtures.
2. Observe exact-head RED with the rest of the suite green.
3. Implement a standard-library-only `agent/harness/parsing_evaluation.py`.
4. Reuse `reviewed_evaluation.models.MetricSummary`; adaptation copies `to_dict()` only.
5. Export contracts from `agent/harness/__init__.py` if public harness consumers need them.
6. Run full suite on exact head.
7. Perform clean-context independent adversarial review against #21, HARN-003 fixture/scorer, HARN-018 state, and HARN-016 comparability requirements.
8. Convert every blocking review finding into RED regression tests before revision.

## Independent adversarial review rubric

Reject if any of these are possible:
- reviewed morphology formulas are reimplemented rather than copied from authoritative scorer output;
- model context can expose gold, expected output or expert correction data;
- two runs with different corpus/snapshot/capability/tool/evidence/permission policies can be labelled comparable without an explicit reported mismatch;
- missing efficiency data is treated as zero;
- expert feedback cannot be tied to exact run/revision/token/decision;
- correction feedback collapses legitimate alternatives;
- model-judge output can override deterministic measurements;
- schema depends on Langfuse/LangGraph/LangChain/provider-specific types;
- HARN-003 fast fixture is misrepresented as the whole-column benchmark;
- feedback/evaluation mutates reviewed/generated corpus data.

## Non-goals

- running model backends (HARN-016);
- LangGraph orchestration (HARN-004);
- Langfuse transport/SDK integration (HARN-005);
- creating a new morphology scorer;
- selecting the eventual held-out whole-column benchmark corpus;
- automatically turning feedback into GitHub issues (HARN-017).
