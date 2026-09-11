# HARN-010 — bounded development controller implementation plan

Issue: #11
Research: `HARN-010-bounded-development-controller-research.md`

## Goal

Add a deterministic, restartable, explicitly bounded controller layer over the existing `RunState` lifecycle without duplicating transition semantics or performing side effects.

## Acceptance interpretation

A successful implementation must provide:

1. a typed next-action vocabulary derived from `RunState.phase`;
2. a strict serializable controller checkpoint containing run binding, limits, counters, and pending-action recovery data;
3. one-step scheduling with immutable input/output values;
4. hard global / implementation / review bounds;
5. explicit stops for COMPLETE, BLOCKED, and AWAITING_HUMAN;
6. explicit resume through the existing `ResumeRequested` event, not controller inference;
7. deterministic restart behavior, including crash after action issuance but before state progress;
8. no GitHub/model/reviewer/shell/filesystem side effects in controller code.

## Proposed API

Create `agent/harness/development_controller.py`.

### `ControllerActionKind`

Closed enum: `RESEARCH`, `PLAN`, `DESIGN_TESTS`, `IMPLEMENT`, `VERIFY`, `REVIEW`.

### `ControllerStopReason`

Closed enum: `COMPLETE`, `BLOCKED`, `AWAITING_HUMAN`, `STEP_LIMIT_REACHED`, `IMPLEMENTATION_LIMIT_REACHED`, `REVIEW_LIMIT_REACHED`.

### `ControllerLimits`

Frozen value object with positive integer `max_steps`, `max_implementation_attempts`, and `max_review_attempts`. Strict `to_dict` / `from_dict`.

### `ControllerAction`

Frozen value object containing:

- action kind;
- source `RunPhase`;
- ordinal global step number;
- the SHA-256 fingerprint of the `RunState` snapshot for which the action was issued.

Strict serialization is needed because an action is persisted inside the checkpoint.

### `ControllerCheckpoint`

Frozen value object:

- `schema_version`;
- `run_id`;
- `limits`;
- `steps_used`;
- `implementation_attempts`;
- `review_attempts`;
- optional `pending_action`.

Strict versioned `to_dict` / `from_dict`; counters must be non-negative and may not exceed corresponding limits. A pending action must have `ordinal == steps_used`, and its kind/source phase must be a valid phase/action pair.

No mutable terminal bit is stored: terminal/paused status is derived from current `RunState` and limits. This avoids stale terminal state after legitimate resume.

### Run-state fingerprint

Use canonical JSON of `RunState.to_dict()` (`sort_keys=True`, compact separators, UTF-8) and SHA-256. The fingerprint is scheduler identity for an observed state snapshot; it is not a security signature.

### `ControllerDecision`

Frozen result with exactly one of `action` or `stop_reason`, plus the checkpoint to persist.

### `BoundedDevelopmentController.step(state, checkpoint)`

Pure single-step scheduling:

1. validate types and require `checkpoint.run_id == state.run_id`;
2. compute current state fingerprint;
3. if `pending_action` is bound to the same fingerprint, replay that exact action with the checkpoint unchanged;
4. otherwise acknowledge progress by clearing stale pending work logically;
5. if core phase is COMPLETE/BLOCKED/AWAITING_HUMAN, return the matching stop without consuming budget;
6. check global step budget;
7. map normal phase to one action;
8. check phase-specific implementation/review budget;
9. return one new action and a checkpoint with counters incremented once and that action stored as pending.

The function never calls `apply_event`; events remain evidence supplied by the host after actual work.

## Persistence protocol

The host must persist the returned checkpoint **before** executing a newly issued action. If it crashes afterward and reloads the same `RunState`, `step()` replays the pending action without incrementing counters. Existing effect boundaries remain responsible for idempotent/reconciled provider writes.

When action completion produces a different durable `RunState`, the next call recognizes the changed fingerprint and schedules from the new phase. If the changed state is paused or complete, the returned checkpoint clears pending work.

## TDD sequence

### RED 1 — deterministic scheduling

Tests for all six normal phases, stable phase/action mapping, run binding, immutable inputs, and one increment for a newly issued action.

### RED 2 — pending action restart semantics

Tests for:

- same state + returned checkpoint → exact action replay;
- replay does not increment any counter;
- serialization/restoration preserves replay behavior;
- changed core state acknowledges prior pending action and allows one next action.

### RED 3 — terminal / pause behavior

Tests for COMPLETE, BLOCKED, AWAITING_HUMAN and pending-action clearing after the core state changes to one of those phases.

### RED 4 — bounds

Tests for exact off-by-one behavior of global, implementation, and review limits; exceptional/core terminal phases take precedence over exhausted limits once progress has changed state.

### RED 5 — strict validation

Tests for cross-run checkpoint reuse, unknown fields/schema versions, bool-as-int, invalid limits/counters, malformed pending action, invalid phase/action pair, and impossible ordinal.

### RED 6 — existing reducer integration

Construct lifecycle snippets with `apply_event` to prove:

- verification failure routes to IMPLEMENT and a later new IMPLEMENT action consumes a second attempt;
- explicit `ResumeRequested` is required before scheduling resumes from a pause;
- review request-changes routes back to IMPLEMENT without controller-owned phase mutation.

## Implementation order

1. add tests only and commit RED state;
2. run agent test workflow and capture expected missing-module/API failure;
3. implement `development_controller.py` only;
4. run targeted/full agent tests;
5. inspect exact diff against research and plan;
6. perform logically independent adversarial review focusing on replay, tampering, off-by-one bounds, auto-resume, run mismatch, and hidden side effects;
7. turn every review blocker into a RED regression before fixing;
8. require green exact-head CI before merge.

## Non-goals

- no internal `while`/forever loop;
- no change to `RunState` or `state_machine.apply_event` unless a demonstrated core contract bug blocks HARN-010;
- no provider SDK calls or GitHub writes;
- no human-approval interpretation;
- no persistence backend;
- no parser/domain-specific policy.

## Adversarial questions for final review

- Can a boolean bypass integer limit validation?
- Is `max_steps=N` exactly N newly issued work actions?
- Does same-state restart replay rather than allocate N+1?
- Can a forged pending action claim a different phase/kind/ordinal?
- Can IMPLEMENT or REVIEW exceed phase-specific budgets while global budget remains?
- Does changed state clear pending action before terminal/pause decisions?
- Can checkpoint data from another run be replayed?
- Can unknown serialized fields silently alter future semantics?
- Does controller code call external effects or manufacture lifecycle events?
- Can an exceptional phase auto-resume because an action mapping falls through?
- Are immutable checkpoint inputs modified in place?
