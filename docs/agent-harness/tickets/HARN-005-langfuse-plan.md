# HARN-005 — Langfuse sidecar implementation plan

## Status

Planning phase after `HARN-005-langfuse-research.md`. Production code and dependency integration must follow TDD RED.

## Scope

Implement optional observability for two explicitly distinct domains:

1. scholarly complete-column parsing (`run_type=parsing`);
2. autonomous software development (`run_type=development`).

Langfuse is a removable transport sidecar. Existing domain contracts, HARN-004 orchestration, HARN-015 evaluation semantics and development-controller correctness remain authoritative.

## Proposed modules

### `agent/harness/telemetry.py`

Framework/provider-neutral telemetry contracts and pure projections. This module must not import Langfuse/OpenTelemetry.

Planned public contracts:

- `TelemetryRunType {PARSING, DEVELOPMENT}`
- `TelemetryOutcome(enabled, delivered, diagnostic)`
- `TraceProjection(run_type, run_id, trace_name, metadata)`
- `ObservationProjection(run_type, run_id, operation_id, name, observation_type, metadata)`
- `ScoreProjection(run_type, run_id, name, value, data_type, metadata)`
- `DevelopmentTraceContext(repository, issue_ref, base_branch, head_branch, head_sha, executed_sha, model_provider?, model_id?, model_version?)`
- `build_parsing_trace_projection(ColumnRunState, ParsingEvaluationRecord | None)`
- `build_development_trace_projection(RunState, DevelopmentTraceContext)`
- `project_parsing_scores(ParsingEvaluationRecord)`

Projection metadata is JSON-scalar/string-list only and data-minimized. No raw token/source/prompt/expert-correction body is emitted.

### `agent/harness/langfuse_sidecar.py`

Concrete optional transport. **No top-level `import langfuse`.**

Planned behavior:

- explicit `enabled` flag, default false;
- `from_environment(...)` reads only documented Langfuse environment config and returns a disabled sink if opt-in is absent;
- lazy `importlib.import_module("langfuse")` only after explicit enable + credential validation;
- exact SDK version lives in optional project extra, not default dependencies;
- injected `client_factory` supported for deterministic unit tests;
- deterministic Langfuse trace ID derived internally from `run_type + run_id`; never persisted into domain state;
- emit curated observations with `start_observation(..., trace_context={"trace_id": ...})`, then `.end()`;
- forward scores with `create_score(trace_id=..., name=..., value=..., data_type=..., metadata=...)`;
- `flush()` is best-effort;
- all import/auth/network/backend/SDK exceptions are converted to `TelemetryOutcome(delivered=False, diagnostic=...)` and never escape into domain work.

Programmer errors constructing a projection remain ordinary exceptions before the transport boundary.

### HARN-004 parsing wrapper

Provide `wrap_column_review_adapters(adapters, sidecar)` without changing `ColumnRunState` or graph topology.

The wrapper must:

- preserve the exact underlying adapter signature/result/exception;
- emit one data-minimized observation per existing operation (`initialize_skill_context`, evidence, adjudication, reconciliation, gate, evaluation);
- derive cursor/revision/token/request/gate IDs from arguments/state;
- never alter operation IDs;
- never catch an underlying scholarly adapter exception;
- never re-run an underlying adapter if telemetry fails;
- use HARN-015 `ParsingEvaluationRecord` for final score forwarding without recomputation.

Telemetry failures are swallowed only on the telemetry side; domain failures remain visible.

### Development emitter

Provide a transport-neutral helper that projects a HARN-002 `RunState` plus external Git/ref context and emits a `cuc.development.run` trace/observation.

No Langfuse ID enters `RunState`; no new development state schema is required.

## Trace names / query boundary

- parsing root/trace name: `cuc.parsing.column-review`
- development root/trace name: `cuc.development.run`

Every projection includes `run_type` so these cannot be conflated in analytics.

Observation names for parsing:

- `cuc.parsing.initialize-skill-context`
- `cuc.parsing.collect-evidence`
- `cuc.parsing.adjudicate`
- `cuc.parsing.reconcile`
- `cuc.parsing.completion-gate`
- `cuc.parsing.evaluate`

Development observations initially use the current `RunPhase` as metadata rather than inventing a second controller state machine.

## Score forwarding

HARN-015 remains authoritative. `project_parsing_scores()` converts already-computed scalar measurements to Langfuse score payloads one-to-one.

Rules:

- score names retain the authoritative metric key with a stable `cuc.` prefix only if needed for namespace; no semantic renaming;
- numeric values are forwarded exactly as Python numeric values (no rounding);
- booleans remain boolean scores;
- categorical/text values remain categorical/text only when explicitly present in HARN-015 scalar metrics;
- measurement provenance/evidence refs are metadata references, not recomputed evidence;
- expert feedback is associated through IDs/references and status fields, not sent as a score unless HARN-015 already defines a scalar metric for it.

The deterministic reviewed-morphology scorer is never imported by telemetry code.

## Credential / activation contract

CUC-specific opt-in: `CUC_LANGFUSE_ENABLED=true`.

When opt-in is true, require:

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

`LANGFUSE_BASE_URL` remains optional and is passed through only when explicitly configured. We do not commit defaults containing project credentials.

Missing keys yield a disabled/not-delivered `TelemetryOutcome`; they do not raise into parsing/tests.

`LANGFUSE_TRACING_ENABLED=false` from the SDK is respected; CUC never overrides it to true.

## Optional dependency

After RED is demonstrated, add:

```toml
[project.optional-dependencies]
observability = ["langfuse==4.15.1"]
```

Use HARN-021 trusted lock generation. Never edit `agent/uv.lock` manually.

A locked smoke test should verify the real optional SDK import/API surface. Default deterministic tests must remain runnable without the extra installed.

## TDD gates

### A. Provider-neutral projections

1. parsing projection carries exact run/workload/capability/model/policy/revision identity from HARN-018/HARN-015;
2. development projection carries run/task/phase/change/test/eval/review/head context and has `run_type=development`;
3. parsing/development trace names are distinct;
4. raw token surfaces, analyses, source summaries, prompt bodies and expert-feedback rationale are absent from serialized projection metadata;
5. domain state JSON does not gain telemetry fields.

### B. Score forwarding

1. authoritative HARN-015 scalar metrics are forwarded exactly, including non-rounded floating values;
2. telemetry does not import or call the morphology scorer;
3. expert feedback is referenced by IDs, not converted to hidden prompt/model context;
4. score projection rejects unsupported non-scalar payloads rather than stringifying arbitrary objects.

### C. Disabled / missing dependency / credentials

1. importing `harness.telemetry` works with no Langfuse installed;
2. importing `harness.langfuse_sidecar` works with no Langfuse installed;
3. disabled sink does not attempt import/client creation;
4. enabled + missing credentials returns non-delivered outcome without exception;
5. enabled + missing optional package returns non-delivered outcome without exception.

### D. Backend failure isolation

Fake SDK client failures at:

- trace ID creation;
- observation start;
- observation end;
- score creation;
- flush;

must never change or fail the wrapped domain operation. Diagnostics may record exception class/message but not credentials.

### E. Parsing wrapper semantics

1. all six HARN-004 adapter operations preserve exact return values and underlying exceptions;
2. operation IDs and call ordering are unchanged;
3. evidence/worklist priority still does not change token scope;
4. failure after a checkpoint does not cause the telemetry wrapper to invoke domain effects twice;
5. final evaluation is forwarded once and scores come from that exact record;
6. no `auto_parsing/**`/`reviewed/**` filesystem write exists in telemetry modules.

### F. Development separation

1. development trace projection cannot accept a `ParsingEvaluationRecord` in place of `RunState`;
2. independent `ReviewResult` fields stay under development-review metadata;
3. HARN-015 `ExpertFeedback` fields stay under scholarly-feedback reference metadata;
4. query keys make the two trace types unambiguous.

### G. Real SDK optional smoke

With the locked `observability` extra installed:

- import `langfuse` and assert the tested major/version family is v4;
- construct concrete backend with an injected fake transport/client where possible so no network/project key is required;
- verify methods used by CUC exist (`create_trace_id`, `start_observation`, `create_score`, `flush`).

## RED sequence

1. Commit provider-neutral contract/projection tests first; expect missing `harness.telemetry`.
2. Commit sidecar/wrapper failure-isolation tests; expect missing `harness.langfuse_sidecar`.
3. Obtain exact-head full-suite RED proving failures are HARN-005-specific and pre-existing suite remains collected.
4. Implement pure contracts first, then sidecar, then wrapper.
5. Obtain GREEN without optional SDK installed.
6. Add optional dependency metadata; let trusted lock workflow update lock.
7. Add/execute locked optional-SDK smoke test without credentials/network telemetry.
8. Full-suite GREEN on exact final head.
9. Fresh logically independent adversarial review.
10. Any review blocker becomes test-first RED -> fix -> GREEN -> new exact-head review.

## Independent review rubric

Reject if:

- Langfuse import is required for domain module import or domain-state restore;
- telemetry absence/failure can change parsing or development correctness;
- raw scholarly data, held-out gold, source excerpts, prompt bodies or secrets are exported by default;
- telemetry changes HARN-004 operation ordering/cursor/scope/checkpoint semantics;
- metrics are recomputed, rounded or renamed ambiguously;
- expert scholarly feedback and PR adversarial review are conflated;
- automatic OpenTelemetry capture is enabled beyond deliberate CUC observations without an explicit policy;
- a Langfuse trace ID appears in serialized domain state;
- optional dependency becomes a default hard dependency;
- `uv.lock` is hand-edited;
- any upstream write exists.
