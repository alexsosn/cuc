# HARN-006 — Clean-context independent development reviewer: implementation plan

## Objective

Add a framework-neutral review boundary that can be restarted independently from implementation context and produces the existing HARN-002 `ReviewResult`. The HARN-002 state machine remains the only authority for approve/request-changes/escalate routing.

## Module

Add `agent/harness/development_reviewer.py`.

Allowed imports:

- standard library;
- `harness.contracts` for `RunState`, `ReviewResult`, `ReviewFinding` and existing gate types;
- `harness.state_machine` only for the narrow helper that applies `ReviewRecorded`.

Do not import LangGraph, Langfuse, provider SDKs, parsing evaluation/gold contracts, or corpus parsing code.

## Serializable clean context

### `ReviewTaskProjection`

Copy only:

- task id;
- title;
- objective;
- acceptance criteria.

### `ReviewChangeProjection`

Copy only:

- current change id;
- changed paths.

Exclude `ChangeSet.summary` and operation history: both may encode implementation narrative and are unnecessary when the final diff is present.

### `ReviewTestEvidence`

For each current declared test intent, expose only objective execution evidence:

- intent id and kind;
- command and working directory;
- head SHA and executed SHA;
- outcome, exit code, passed/failed counts;
- evidence refs.

Exclude free-form test-result summary from the reviewer packet.

### `ReviewEvalEvidence`

For each current evaluation result expose:

- eval id;
- head SHA and executed SHA;
- outcome;
- scalar metrics;
- evidence refs.

Exclude free-form eval summary.

### `DevelopmentReviewContext`

Fields:

- schema version;
- deterministic `review_context_id`;
- development run id;
- task projection;
- change projection;
- base SHA, head SHA and executed SHA;
- exact final diff text;
- final diff SHA-256;
- test evidence;
- eval evidence;
- policy/invariant refs;
- explicit rubric items.

`to_dict()` / `to_json()` must be deterministic. `from_dict()` / `from_json()` may be added only if they preserve the same derived-ID invariant; callers may never choose the context ID.

## Context construction

`build_development_review_context(state, *, base_sha, head_sha, final_diff, policy_refs, rubric)`:

1. requires `RunState.phase == REVIEW`;
2. requires `state.verified_head_sha == head_sha`;
3. requires one current change;
4. reconstructs latest current test results by declared intent and requires all successful;
5. includes current evaluation results and requires all successful;
6. requires all included executed evidence to bind to the same `head_sha` and the same `executed_sha`;
7. rejects missing test evidence, empty diff, empty policy refs or empty rubric;
8. copies only allowlisted structured fields; no generic metadata/context kwargs;
9. derives `diff_sha256` and then `review_context_id = review-context-<sha256(canonical payload without id)>`.

The builder must not depend on `state.research.summary`, `state.plan.summary`, `ChangeSet.summary`, result summaries, prior review, scholarly expert feedback, or conversational state.

## Review execution

### `IndependentReviewer`

Runtime binding contains:

- explicit `reviewer_id`;
- optional `implementer_id` for structural separation enforcement;
- `review(context)` callable.

Require reviewer id != implementer id when an implementer id is supplied.

### `run_independent_development_review(context, reviewer)`

- call reviewer with only `DevelopmentReviewContext`;
- require returned value is existing HARN-002 `ReviewResult`;
- require exact reviewer id;
- require exact context id;
- require exact inspected head;
- require `review_id` distinct from development run id and context id;
- return validated result unchanged.

No merge/finalization/GitHub side effect occurs here.

### `apply_development_review(state, result)`

Exactly delegate to:

`apply_event(state, ReviewRecorded(result))`

No custom disposition routing.

## TDD RED tests

Write tests before production module for:

1. review-ready context has deterministic identity and contains task, final diff, exact revisions, current objective test/eval evidence, policies and rubric;
2. context shape has no generic metadata field and excludes research/plan/change/result summaries and prior review narrative;
3. changing diff/head/policy/rubric changes context id;
4. review context cannot be built outside REVIEW phase or for stale head;
5. mixed/stale head/executed verification evidence fails closed;
6. missing/non-success current test evidence fails closed;
7. reviewer callable receives exactly the clean context and can run without `RunState` or implementation conversation;
8. malformed/wrong reviewer/context/head review result fails closed;
9. reviewer identity must differ from supplied implementer identity;
10. deterministic seeded unsafe diff is visible to a fixture reviewer, producing a structured blocking `ReviewFinding`;
11. `REQUEST_CHANGES` result routes through existing HARN-002 state machine back to IMPLEMENT and persists findings;
12. `APPROVE` routes through existing state machine to COMPLETE;
13. scholarly parsing/expert-feedback types/modules are not imported by reviewer core;
14. LangGraph/Langfuse/provider SDKs are not correctness dependencies;
15. full suite remains green.

## Review-driven adversarial checklist

Reject if any of these can occur:

- arbitrary nested metadata enters reviewer context;
- implementer research/plan/self-justification prose is present by default;
- result summaries or prior reviewer narrative are passed as persuasion;
- scholarly expert feedback is used as development-review approval;
- a stale or mixed executed/head revision is reviewable;
- a returned review for another context/head/reviewer is accepted;
- request-changes routing is reimplemented rather than delegated to HARN-002;
- review cannot be restarted from context alone;
- reviewer can mark tests/evals passed or mutate RunState directly;
- a provider/framework/telemetry dependency is required for correctness;
- raw final diff is omitted so the reviewer sees only a digest;
- upstream or corpus-generated data is touched.

Every blocking finding enters tests first as RED, followed by the smallest production fix and full-suite GREEN.

## Non-goals

- GitHub write authorization / human approval (HARN-009);
- whole-loop budgets/retries/termination (HARN-010);
- Deep Agents selection (HARN-007);
- provider/model clean-session launcher;
- scholarly morphology review/evaluation;
- changing HARN-002 disposition names (`approve`, `request-changes`, `escalate`).
