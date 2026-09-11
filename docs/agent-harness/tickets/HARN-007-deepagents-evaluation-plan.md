# HARN-007 — Deep Agents vs explicit LangGraph: spike plan

## Decision question

Should CUC retain explicit LangGraph only, adopt a hybrid with Deep Agents behind typed worker/reviewer/presentation ports, or adopt Deep Agents as primary controller?

Parsing and development are evaluated separately. API convenience alone is not evidence.

## Prerequisites

Complete and available on current base:

- HARN-004 explicit LangGraph complete-column runtime;
- HARN-008 versioned skill capabilities;
- HARN-018 column state/completion contracts;
- HARN-006 clean serializable development-review context/report contract.

HARN-009/HARN-010 are downstream consumers of this decision, not prerequisites.

## Experimental dependency boundary

Use `deepagents==0.7.13`, the latest published upstream release as of 2026-09-11, only if an executable framework experiment is necessary.

Dependency integration must use the trusted uv-lock path; never hand-edit `agent/uv.lock`. If the final ADR rejects Deep Agents for production roles, remove the dependency before merge and retain only framework-neutral evidence/ADR/tests.

## Research → plan → TDD gates

Research and this plan are committed before experimental code. The next branch change must be tests/specification, not implementation.

### Gate A — framework-neutral comparison contract

Write tests for a small experiment-result/ADR contract before any Deep Agents adapter. It must record per scenario:

- framework/version;
- CUC contract(s) exercised;
- deterministic or model-backed execution mode;
- visible context fields;
- exposed tool/permission surface;
- checkpoint/resume observations;
- bounded termination observation;
- framework steps/tool calls when measured;
- qualitative notes explicitly marked qualitative;
- verdict per candidate role.

The ADR must choose exactly one overall development-controller direction while separately fixing parsing to explicit HARN-004 unless the experiment proves semantic equivalence without a second traversal authority.

### Gate B — parsing semantic preservation

Test first, then model the smallest composition capable of invoking HARN-004 as one bounded capability.

Assertions:

- framework wrapper never chooses token set/order;
- `ColumnRunState.cursor` remains the only traversal cursor;
- worklist hints cannot narrow scope;
- authenticated completion gates remain unchanged;
- checkpoint/revisit semantics remain HARN-004/HARN-018 semantics;
- no filesystem/GitHub write surface is introduced;
- if the wrapper adds no behavior, record that as negative evidence rather than manufacturing a benefit.

Reimplementing the token loop in Deep Agents fails the experiment by construction.

### Gate C — clean-context HARN-006 reviewer

Test a narrow reviewer-worker port against the finalized HARN-006 API.

Assertions:

- serialized `DevelopmentReviewContext` is the only task payload;
- implementation conversation/research/plan narrative is absent;
- explicit tool and permission sets are empty or exactly allowlisted;
- default general-purpose subagent/inherited tools are not silently available;
- output is revalidated as exact `DevelopmentReviewReport`;
- context/head binding remains HARN-006 behavior;
- deterministic non-Deep-Agents reviewer remains supported.

Prefer deterministic/fake-model inspection of constructed configuration/context. A live model is not required to prove isolation.

### Gate D — HITL presentation semantics

Prototype only presentation/pause/resume of a fake sensitive effect.

Assertions:

- pending effect can pause/resume;
- approve/reject/edit payloads are observable;
- approval UI/interrupt alone cannot mark the effect authorized;
- exact authority remains an external HARN-009 responsibility;
- no real GitHub mutation occurs.

### Gate E — restart/budget boundary

Use deterministic fake workers/tools.

Compare:

- checkpoint identity;
- retry/step counting;
- bounded max-step/max-retry termination;
- interrupted call resume;
- whether framework checkpoint state becomes a competing durable source of truth.

At least one case must terminate by budget rather than succeed.

## Decision rubric

Score `explicit LangGraph`, `Deep Agents`, and `hybrid` on:

1. semantic fidelity to CUC contracts;
2. deterministic testability;
3. clean-context isolation;
4. checkpoint/resume transparency;
5. permission/approval composability;
6. provider/model neutrality;
7. dependency/API churn risk;
8. maintenance complexity;
9. long-horizon worker ergonomics.

Semantic fidelity and authorization safety are veto criteria.

## ADR outcome

Final ADR must choose exactly one:

- retain explicit LangGraph only;
- hybrid — deterministic CUC controllers with optional Deep Agents worker/reviewer/presentation adapters;
- adopt Deep Agents as primary development controller.

It must state separately:

- parsing runtime decision;
- development worker/reviewer decision;
- HARN-009 recommendation;
- HARN-010 recommendation;
- whether `deepagents` remains a production dependency.

## Independent adversarial review

Fresh review must attack:

- hidden prompt/skill/context inheritance;
- default GP subagent unexpectedly enabled;
- inherited filesystem/shell/MCP tools or permissions;
- HITL confused with authorization;
- model-directed planning replacing deterministic phase/token traversal;
- duplicate checkpoint/state authority;
- dependency retained despite a no-value/reject conclusion;
- cherry-picked happy paths without interruption/termination cases;
- weakening HARN-006 clean-context guarantees.

Every blocker enters a test-first RED/fix sub-loop before ADR finalization.

## Non-goals

- changing Ugaritic parsing rules or HARN-004/HARN-018 state;
- implementing HARN-009 authorization;
- implementing the full HARN-010 controller;
- selecting a production model/provider;
- adopting Deep Agents Code/CLI as a project dependency.