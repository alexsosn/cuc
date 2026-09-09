# HARN-005 — Langfuse sidecar research

## Status

Research phase for issue #6. No production telemetry code or dependency change is introduced by this document.

## Question

How should CUC add Langfuse observability/evaluation without making parsing or the development controller depend on Langfuse, leaking scholarly/evaluation data into model context, or duplicating authoritative CUC scoring?

## Repository seams inspected

### Parsing

The completed HARN-004 runtime already supplies the right instrumentation boundary:

- `ColumnReviewAdapters.initialize_skill_context`
- `collect_evidence`
- `adjudicate`
- `reconcile`
- `verify_completion`
- `evaluate`

Every adapter receives the durable `ColumnRunState` and deterministic operation identity. `ColumnRunState` itself already carries complete-column identity, cursor, evidence records, token decisions, reconciliation findings, completion gates and append-only receipts.

HARN-015 already owns evaluation semantics through `ParsingEvaluationRecord`, including:

- exact `ParsingRunIdentity` and `ParsingWorkloadRef`;
- model/provider/version/config identity;
- repository, snapshot, capability and policy provenance;
- deterministic and supplementary measurements;
- expert feedback;
- efficiency measurements and artifact references.

The authoritative reviewed-morphology scorer remains `agent/scripts/score_reviewed_morphology.py`; Langfuse must ingest HARN-015 measurements rather than recompute them.

### Development controller

HARN-002 `RunState` already supplies a separate development-run projection:

- `run_id`, task identity and current `RunPhase`;
- research and plan artifacts;
- test intents;
- change sets and deterministic operation IDs;
- test/eval results with head/executed SHA evidence;
- logically independent `ReviewResult` and findings;
- verified head, blocked/awaiting-human state and final disposition.

This must remain a different trace type from scholarly parsing. Development adversarial review is not expert scholarly feedback.

## Current Langfuse findings (checked 2026-09-09)

Primary sources:

- SDK overview: https://langfuse.com/docs/observability/sdk/overview
- Python API reference: https://python.reference.langfuse.com/langfuse
- v3→v4 migration: https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4
- scores via SDK/API: https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk
- Scores API: https://langfuse.com/docs/api-and-data-platform/features/scores-api
- enable/disable tracing: https://langfuse.com/faq/all/enable-disable-tracing
- masking: https://langfuse.com/docs/observability/features/masking
- package release: https://pypi.org/project/langfuse/

Findings:

1. The current Python SDK major is **v4**, rewritten in March 2026 and OpenTelemetry-native. Do not build against legacy v2/v3 APIs.
2. PyPI currently publishes **langfuse 4.15.1** (2026-08-28). If HARN-005 adds a dependency, pin that exact tested version and let the existing trusted uv lock transport generate `uv.lock`.
3. Python SDK initialization automatically configures OpenTelemetry. By default it exports Langfuse plus GenAI/LLM spans; `should_export_span` is the current filtering mechanism. This is useful but also a leakage/cost risk if CUC accidentally initializes global telemetry when the sidecar is meant to be disabled.
4. Tracing can be disabled with `LANGFUSE_TRACING_ENABLED=false`. For CUC, disabled must be the explicit default of our adapter unless the caller opts in; correctness cannot rely on Langfuse's own implicit credential behavior.
5. Credentials are environment/config only (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`). No key, host, project or generated telemetry ID belongs in domain state or committed fixtures.
6. `start_as_current_observation(...)` supports explicit span/tool/generation observations. For the first slice we should emit deliberately curated CUC observations rather than rely on automatic capture of arbitrary third-party OpenTelemetry spans.
7. `create_score(...)` can attach numeric/categorical/boolean/text scores to a trace or observation. This fits forwarding HARN-015 measurements exactly; Langfuse code evaluators are unnecessary and would duplicate CUC evaluation logic.
8. Short-lived processes may need `flush()`, but flush/export failure must remain telemetry-only failure, never a parsing/test gate failure.
9. Current SDKs support masking/export filtering. CUC should avoid sending raw scholarly token text, source excerpts, expert-feedback rationale, prompts, secrets or arbitrary tool inputs in the initial adapter at all; metadata-by-reference is safer than relying on post-hoc masking.
10. Langfuse trace/observation IDs are observability identifiers. They may be deterministically derived internally for correlation but must never be required to serialize/restore `ColumnRunState` or development `RunState`.

## Architecture decision

Implement a framework-neutral CUC telemetry facade with a **Null sink** and an optional **Langfuse sink**.

```text
CUC domain state / evaluation contracts
        |
        +--> telemetry projection builders (pure, no Langfuse imports)
        |       - parsing run metadata
        |       - development run metadata
        |       - HARN-015 score projection
        |
        +--> best-effort telemetry facade
                - disabled/null => no-op
                - enabled + unavailable/misconfigured/backend failure => record local diagnostic, never fail domain work
                - Langfuse backend imported lazily
```

For HARN-004 integration, prefer wrapping `ColumnReviewAdapters` rather than adding telemetry IDs or SDK types to graph/domain state. The wrapper can observe the existing deterministic operations and cursor while passing through the original scholarly adapter result unchanged.

### Parsing trace type

Root trace/observation name: `cuc.parsing.column-review`.

Required queryable metadata, all derived from existing contracts:

- `run_type=parsing`;
- task/run ID;
- corpus, tablet, column;
- snapshot ID/provenance and repository revision;
- capability name/version/provenance;
- model provider/id/version/config digest when available from HARN-015 identity;
- tool/evidence/permission policy digests;
- current cursor/revision and operation ID;
- reconciliation/completion summaries by IDs/counts, not raw source text;
- evaluation artifact refs and deterministic measurement names/values.

Child observations correspond to initialized skill context, evidence collection, adjudication, reconciliation, named completion gate and evaluation. Do not use a flagged-token selector as trace scope; every-token semantics remain controlled by HARN-004.

### Development trace type

Root trace/observation name: `cuc.development.run` with `run_type=development`.

Projection is derived from HARN-002 `RunState`:

- run/task ID and current phase;
- research/plan/test-design/implement/verify/review iteration identity;
- change IDs and operation IDs;
- test/eval gate outcomes plus head/executed SHA;
- independent review ID/context/inspected SHA/disposition and finding severities;
- verified head and final/blocked disposition.

Do not encode scholarly `ExpertFeedback` as development `ReviewResult`, or vice versa.

## Data minimization / held-out safety

The initial sidecar exports identifiers, provenance digests, counts, statuses and already-authoritative metric values. It must not export by default:

- raw token surfaces or reconstructed text;
- DULAT/Tropper/EUPT/source excerpts;
- prompt bodies;
- expert-feedback rationale/corrected analyses;
- reviewed gold rows or held-out expected answers;
- environment values whose names indicate credentials/secrets;
- arbitrary tool arguments/results.

Expert feedback is associated by stable feedback IDs/reviewer refs/evidence refs and run/revision identity only. The feedback object is never inserted into model input by telemetry code.

## Dependency decision

Use an **optional project extra**, not a default runtime dependency:

```toml
[project.optional-dependencies]
observability = ["langfuse==4.15.1"]
```

The module containing pure projections and the Null sink must import without Langfuse installed. The concrete Langfuse backend performs a lazy import only when explicitly enabled.

The existing HARN-021 trusted lock transport must generate the lock update. No manual `uv.lock` editing.

## Failure semantics

Telemetry methods are best-effort and return an explicit lightweight result such as `TelemetryOutcome(enabled, delivered, diagnostic)`; they do not raise backend/import/auth/network exceptions into parsing or development execution.

Programmer/domain-contract errors in pure projection builders should still raise: silently accepting malformed `ColumnRunState`, `ParsingEvaluationRecord`, or `RunState` would hide CUC bugs. The exception shield belongs only around the external telemetry boundary.

## TDD risks to attack

1. Disabled telemetry imports or initializes Langfuse anyway.
2. Missing credentials changes parsing/development result.
3. Backend/start-span/score/flush failure escapes into domain execution.
4. A Langfuse trace ID leaks into `ColumnRunState` or `RunState` serialization requirements.
5. Parsing and development trace names/metadata are conflated.
6. HARN-015 deterministic metrics are renamed, rounded, recomputed or otherwise modified.
7. Expert scholarly feedback is serialized as prompt/model input or confused with PR review.
8. Raw token/source/prompt/secret-bearing payloads are exported by default.
9. Adapter wrapping changes scholarly adapter return values, call ordering, operation IDs, checkpoint/resume or every-token scope.
10. Optional dependency becomes a hard import from `harness.__init__` or another deterministic domain module.
11. Automatic OpenTelemetry capture exports unrelated spans because the backend is initialized too broadly.
12. Short-lived flush failure becomes a correctness failure.

## Research conclusion

HARN-005 is implementable without changing domain schemas or parser semantics. The safest first slice is:

1. pure projection contracts;
2. Null/best-effort facade;
3. lazy optional Langfuse v4 backend;
4. parsing adapter wrapper and development-state emitter;
5. exact HARN-015 score forwarding;
6. explicit data-minimization policy and failure isolation;
7. optional dependency lock generated through the trusted existing workflow.

Implementation should not add Langfuse callbacks directly to `column_state.py`, `contracts.py`, `parsing_evaluation.py`, the morphology scorer, parser or linter.
