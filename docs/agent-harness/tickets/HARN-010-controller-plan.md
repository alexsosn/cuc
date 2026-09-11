# HARN-010 — bounded development-controller implementation plan

## Goal

Implement a framework-neutral bounded controller that executes the existing HARN-002 development state machine, uses HARN-006 for clean-context independent review, and routes every GitHub mutation through HARN-009.

The controller is orchestration and durability glue only; it is not a second domain state machine.

## Files

Planned production changes:

- `agent/harness/development_controller.py` — controller envelope, policy/budgets, adapters, audit, serialization, bounded dispatcher;
- `agent/harness/state_machine.py` — only if required, add generic execution-block / human-interrupt events so pauses do not masquerade as test results;
- `agent/harness/__init__.py` — expose stable controller contracts only after implementation is green.

Planned tests:

- `agent/tests/test_development_controller.py` — deterministic scenario suite;
- additional narrowly scoped tests only when adversarial review exposes a real missing invariant.

No parser/generated/reviewed data changes.

## Contracts

### `DevelopmentIssueContext`

Immutable issue input:

- repository (must be `alexsosn/cuc` for controller-owned issue mutation/finalization in this ticket);
- issue number;
- issue reference/URL;
- issue content digest;
- source evidence/provenance refs.

This context is part of serialized controller state and cannot be replaced after start.

### `ControllerPolicy`

Closed execution policy:

- `max_steps > 0`;
- `max_revision_cycles >= 0`;
- optional `max_cost` using non-negative finite numeric units;
- explicit `allow_no_feature_fallback`;
- HARN-009 `GitHubTaskPolicy`;
- finalization operation ID/action already declared by that policy.

No unlimited sentinel values.

### `ControllerUsage`

Durable counters:

- steps;
- revision cycles;
- accumulated cost.

Counters validate against the policy after restore.

### `ControllerAuditEntry`

Append-only deterministic record with:

- sequence;
- HARN-002 phase;
- action/stage;
- outcome;
- refs (artifact/change/test/review/effect IDs as applicable);
- concise reason.

Audit entries do not store chain-of-thought.

### `DevelopmentControllerState`

Contains:

- immutable issue context;
- policy;
- authoritative HARN-002 `RunState`;
- usage;
- HARN-009 `GitHubEffectJournal`;
- optional exact `HumanApprovalChallenge`;
- optional finalization receipt;
- explicit controller status/reason;
- audit entries.

Serialization is strict JSON and revalidates nested contracts.

Controller status is execution-level, separate from `RunPhase`, e.g. `running | paused | completed | failed-policy | outcome-unknown`.

## Minimal reducer extension

Add two HARN-002 events if tests prove they are needed:

- `ExecutionBlocked(reason)` — from any nonterminal, nonexceptional phase to BLOCKED with `resume_phase=current`;
- `HumanInterventionRequired(reason)` — same shape to AWAITING_HUMAN.

`ResumeRequested` remains the only reducer path back.

These events carry no test/eval outcome and therefore cannot forge gate evidence.

## Adapter surface

`DevelopmentControllerAdapters` is a set of narrow callbacks:

- `research(state, issue) -> ResearchArtifact`;
- `plan(state, issue) -> PlanArtifact`;
- `design_tests(state, issue) -> tuple[TestIntent, ...]`;
- `implement(state, issue, revision_index) -> ChangeSet`;
- `verify(state, issue) -> VerificationBatch`;
- `review_material(state, issue) -> ReviewMaterial`;
- `independent_reviewer` — existing HARN-006 contract;
- `build_finalization_request(state, issue) -> GitHubEffectRequest`.

`VerificationBatch` contains typed `TestResult`/`EvalResult` values only. Controller applies them through `TestRecorded`/`EvalRecorded`; it never converts model booleans into gate passes.

`ReviewMaterial` carries only base/head/diff/policy refs/rubric. The clean packet itself is built by HARN-006.

The HARN-009 gateway is supplied separately to the trusted controller constructor and is never a field on the model-facing adapter bundle.

## Dispatcher algorithm

Each advancement consumes one controller step before invoking an external callback. Fail closed if the next step would exceed policy.

Pseudo-flow:

```text
while status == running:
  enforce step/cost/revision limits

  RESEARCH:
    research adapter -> ResearchRecorded

  PLAN:
    plan adapter -> PlanRecorded

  TEST_DESIGN:
    design_tests adapter -> TestsDeclared

  IMPLEMENT:
    if this is a retry after failure/review rejection:
      consume one revision cycle (before implementation callback)
    implement adapter -> ChangeRecorded

  VERIFY:
    verification adapter -> typed results
    apply each result through HARN-002
    if reducer returns IMPLEMENT/BLOCKED/AWAITING_HUMAN: stop/loop as appropriate
    when all required evidence is present: VerificationPassed

  REVIEW:
    HARN-006 build context -> independent reviewer -> validated report -> HARN-002 ReviewRecorded
    APPROVE => COMPLETE
    REQUEST_CHANGES => IMPLEMENT, bounded retry
    ESCALATE => AWAITING_HUMAN and pause

  COMPLETE:
    build finalization request
    execute only through HARN-009 gateway with controller-state checkpoint callback
    HumanApprovalRequired => persist exact challenge, enter HARN-002 AWAITING_HUMAN, pause
    GitHubEffectOutcomeUnknown => persist journal, enter HARN-002 BLOCKED, status outcome-unknown
    receipt => completed
```

The controller must never call finalization when `RunState.phase != COMPLETE`.

## Resume / human approval

`submit_human_approval(approval)` is a trusted-host API, not a model callback.

It requires:

- current exact pending challenge;
- matching operation ID and request digest;
- current `RunState.phase == AWAITING_HUMAN`;
- approval persisted into HARN-009 journal;
- challenge cleared;
- `ResumeRequested` applied through HARN-002.

A mismatched approval is rejected without changing state.

Generic scholarly/reviewer escalation may also put HARN-002 into AWAITING_HUMAN without a GitHub approval challenge; that resume path must be explicit and cannot manufacture an upstream approval.

## Budget semantics

### Steps

Every external adapter invocation and finalization attempt consumes one step. Pure validation/serialization does not.

At exhaustion, persist a policy stop reason and do not invoke the next adapter.

### Revision cycles

Initial IMPLEMENT is cycle 0. Returning to IMPLEMENT after:

- `TEST_FAILURE`;
- `REGRESSION`;
- `REQUEST_CHANGES`

requires a new revision cycle. Before invoking the next implementation adapter, increment and validate against `max_revision_cycles`.

### Cost

Adapters may return an optional non-negative finite `cost` alongside their typed value via a small `AdapterResult[T]` envelope. Cost is charged after the callback result is received and before its domain event is applied. If charging would exceed the configured budget, fail closed and do not accept/apply the returned artifact as progress.

This prevents an over-budget artifact from silently advancing durable state.

## No-feature fallback

Expose a separate trusted-host method to create/accept fallback work, not an automatic branch in the ordinary issue flow.

Allowed categories are closed:

- performance;
- stability;
- ergonomics;
- documentation;
- edge-cases.

If `allow_no_feature_fallback` is false, any fallback request fails before adapters run. Fallback does not rewrite an existing issue objective.

## TDD sequence

### RED 1 — controller absent / straight-through semantics

Tests require:

1. successful run reaches completed only after HARN-002 COMPLETE + finalization receipt;
2. objective test results are required before review;
3. independent review context is produced through HARN-006;
4. controller snapshot round-trips.

### RED 2 — bounded retries and pauses

Tests require:

5. one test failure returns to implementation and consumes a revision cycle;
6. repeated failure stops at revision limit with explicit reason;
7. review rejection uses the same bounded retry accounting;
8. execution block enters HARN-002 BLOCKED without fake test evidence;
9. reviewer escalation enters AWAITING_HUMAN.

### RED 3 — HARN-009 integration

Tests require:

10. fork finalization executes once and receipt replay does not redispatch;
11. upstream finalization creates exact `HumanApprovalChallenge` and dispatches zero writes;
12. mismatched trusted approval is rejected;
13. matching trusted approval resumes and executes once;
14. `GitHubEffectOutcomeUnknown` survives serialize/restore and cannot auto-retry;
15. raw write adapter is not present on `DevelopmentControllerAdapters`.

### RED 4 — budgets, provenance, fallback

Tests require:

16. step budget exhaustion stops before callback;
17. cost budget rejects over-budget result before applying its artifact;
18. HARN-017-style provenance refs survive round trip and review/finalization flow;
19. disabled fallback rejects; enabled policy accepts only closed categories;
20. stale head/executed evidence remains rejected through controller integration.

Prefer one initial comprehensive RED commit if test clarity remains good; otherwise preserve the groups as separate RED/fix commits.

## Review checklist

Independent review must attack:

- hidden second phase machine;
- off-by-one revision/step budget loops;
- model-created approvals;
- adapter access to raw gateway/transport;
- accepting artifacts after budget exceeded;
- finalization before COMPLETE;
- duplicate finalization after resume;
- uncertain GitHub write replay;
- reviewer-context leakage;
- stale head/executed revision acceptance;
- mutation of issue/provenance identity after restore;
- fallback objective drift;
- malformed serialized nested contracts/counters.

Every blocker becomes a test-only RED before its fix.
