# HARN-010 — canonical bounded development-controller plan

## Architecture

HARN-002 `RunState`/`apply_event()` is the sole phase state machine. HARN-010 adds a bounded host/orchestration state around it; it must never mutate HARN-002 phase/artifact fields directly when an HARN-002 event exists.

Use a framework-neutral controller. No LangGraph/Deep Agents dependency is required for this correctness-critical loop.

### Controller state

Persist a strict-versioned `DevelopmentControllerState` containing at minimum:

- baseline SHA/revision;
- canonical HARN-002 `RunState`;
- provenance refs from the issue/finding;
- accepted targeted RED evidence;
- pending implementation/change identity;
- ordered pending GitHub requests + next index;
- current candidate head SHA;
- evaluated/reviewed change identity;
- bounded counters: steps, revisions, verifications, reviews, GitHub writes, optional cost;
- structured stop/pause code + reason;
- ordered audit events.

The canonical `GitHubEffectJournal` is durable host state and must be serializable/restorable alongside the controller run. It may be stored in the controller snapshot or in a separate atomic store, but the gateway's pre-dispatch `checkpoint()` callback must persist it before adapter execution.

### Trusted host dependencies

Inject narrow ports for:

- research;
- plan;
- test declaration;
- real baseline RED execution;
- implementation/change production;
- targeted/full tests;
- evals;
- exact base→head diff;
- controller-state persistence;
- GitHub-journal persistence;
- canonical `GitHubEffectGateway`;
- HARN-006 `IndependentReviewer`.

The model/controller never receives the gateway's raw adapter, reconciler, or `HumanApprovalAuthority.register()` surface.

## GitHub operation contract

Implementation output may declare an ordered tuple of canonical `GitHubEffectRequest` values. `ChangeSet.operation_ids` must exactly match their ordered IDs.

Before calling `execute_write()`:

- operation/action must already be authorized by the task-local `GitHubTaskPolicy` held by the gateway;
- controller GitHub-write budget is checked;
- current durable journal is supplied;
- gateway checkpoint callback is the only path that persists uncertainty/receipt transitions.

After gateway success/replay:

- controller advances exactly one pending operation index;
- write budget increments exactly once for an externally executed mutation, not for a receipt replay;
- controller snapshot is persisted.

If a controller crash occurs after gateway receipt persistence but before controller cursor persistence, restart sees the old pending index + newer journal; the same request must replay from receipt without adapter dispatch, then consume the controller write budget exactly once and advance.

If journal is uncertain, normal execute remains blocked. A trusted host reconciliation path must run first; model code cannot clear uncertainty.

## Human approval

A `HumanApprovalRequired` challenge pauses the run as `needs-human` and stores only durable pause/request identity. Resume is permitted only after trusted host code has restored/registered the exact approval in the host-owned `HumanApprovalAuthority` and supplied a journal containing the matching approval value. The controller does not construct or register authority.

## TDD RED scenarios

Write tests before runtime implementation. At minimum:

### Flow / HARN-002 reducer
1. success path visits research → plan → test design → RED → implementation → fresh tests/evals → independent review → complete;
2. controller records phase changes through HARN-002 events rather than direct phase replacement;
3. research/plan/test/implementation port output bound to wrong identity is rejected.

### RED gate
4. implementation cannot run before real targeted baseline failure evidence;
5. green baseline, zero exit, zero failed tests, wrong baseline SHA, wrong intent, and regression-classified RED are rejected;
6. all required targeted intents must have accepted RED evidence.

### Revision / verification
7. failed candidate test returns through bounded revision path;
8. reviewer rejection returns through bounded revision path;
9. test/eval result with stale head or executed SHA is rejected;
10. successful verification/eval evidence is only accepted for current change/head.

### Bounds / termination
11. explicit ceilings stop revisions, verification executions, reviews, GitHub writes, cost and total steps;
12. blocked dependency/execution, policy block and needs-human have explicit structured reasons;
13. fallback category is denied unless explicitly enabled and then limited to performance/stability/ergonomics/documentation/edge-cases.

### GitHub durability / replay
14. duplicate receipt replay does not redispatch provider mutation;
15. crash after gateway receipt persistence but before controller snapshot replays safely, advances exactly once and cannot bypass GitHub write budget;
16. uncertain journal cannot auto-retry; trusted reconciliation required;
17. changed request under same operation ID is rejected;
18. model/controller cannot access a generic GitHub transport or approval registration route.

### Restart / checkpoint integrity
19. strict controller serialization round-trip preserves counters, pending operation order, RED/provenance and stop state;
20. malformed/tampered checkpoint fields fail closed;
21. a pending logical action replay does not consume a second controller step/revision/review budget merely because the process restarted.

### Independent review
22. review context contains exact base/head/diff, policy refs and evidence but excludes implementer private rationale;
23. reviewer cannot equal implementer identity when independence policy forbids it;
24. only accepted independent review may finalize.

## Implementation sequence

1. Research and plan committed on post-HARN-023 base. **Done before tests.**
2. Add canonical scenario tests with no `development_controller.py`; obtain exact RED where failures are only missing runtime/contracts.
3. Implement pure state/value contracts and bounded controller scheduler/orchestration using HARN-002 reducer.
4. Integrate canonical `github_effects.py` only; no compatibility import of `github_side_effects.py`.
5. Targeted then full suite GREEN; capture exact head + synthetic merge SHA.
6. Fresh logically-independent adversarial review using issue #11, #53, HARN-002/006/023 contracts and final diff.
7. Every blocking review finding becomes test-first RED before fix.
8. Final exact-head GREEN + fresh approval-equivalent review.
9. Mark ready, merge to `agent-harness-safety`, explicitly close #11 and #53 when acceptance is met.

## Review rejection criteria

Reject if any of these are possible:

- second development phase reducer/state machine substitutes for HARN-002;
- model text alone claims tests/evals passed;
- RED is not an executed baseline test failure;
- stale revision evidence is accepted;
- reviewer is not clean/independent;
- any retry loop lacks an explicit bound;
- GitHub request bypasses canonical `GitHubEffectGateway`;
- journal uncertainty can be cleared/retried by model-controlled code;
- controller can register its own `HumanApprovalAuthority` values;
- restart can duplicate a GitHub mutation or avoid budget accounting;
- upstream mutation occurs in tests/implementation work;
- parser scope/every-token semantics are duplicated inside this development controller.