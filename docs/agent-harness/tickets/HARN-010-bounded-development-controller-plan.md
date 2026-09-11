# HARN-010 — bounded development controller implementation plan

Issue: #11
Research: `HARN-010-bounded-development-controller-research.md`

## Goal

Add a deterministic, restartable, explicitly bounded controller layer over the existing `RunState` lifecycle without duplicating transition semantics or performing side effects.

## Acceptance interpretation

A successful implementation must provide:

1. a typed next-action vocabulary derived only from the current `RunState.phase`;
2. a strict serializable controller checkpoint containing run binding, limits, and counters;
3. one-step scheduling with immutable input/output values;
4. hard global / implementation / review bounds;
5. explicit stops for COMPLETE, BLOCKED, and AWAITING_HUMAN;
6. explicit resume through the existing state-machine `ResumeRequested` event, not controller inference;
7. deterministic restart behavior after checkpoint serialization;
8. no GitHub/model/reviewer/shell/filesystem side effects in controller code.

## Proposed API

Create `agent/harness/development_controller.py` with:

### `ControllerActionKind`

Closed enum:

- `RESEARCH`
- `PLAN`
- `DESIGN_TESTS`
- `IMPLEMENT`
- `VERIFY`
- `REVIEW`

### `ControllerStopReason`

Closed enum:

- `COMPLETE`
- `BLOCKED`
- `AWAITING_HUMAN`
- `STEP_LIMIT_REACHED`
- `IMPLEMENTATION_LIMIT_REACHED`
- `REVIEW_LIMIT_REACHED`

### `ControllerLimits`

Frozen value object with positive integer:

- `max_steps`
- `max_implementation_attempts`
- `max_review_attempts`

It supports strict `to_dict` / `from_dict` serialization.

### `ControllerCheckpoint`

Frozen value object:

- `schema_version`
- `run_id`
- `limits`
- `steps_used`
- `implementation_attempts`
- `review_attempts`

Strict versioned `to_dict` / `from_dict`; counters must be non-negative and may not exceed corresponding limits.

No mutable “terminal” bit is needed: terminal/paused status is derived from the current `RunState` and current limits on every call. This avoids a stale controller terminal flag after a legitimate core-state resume.

### `ControllerAction`

Frozen output containing:

- action kind;
- source `RunPhase`;
- ordinal scheduled step number.

### `ControllerDecision`

Frozen result with exactly one of:

- `action`, or
- `stop_reason`.

Also returns the next checkpoint. For stop decisions the checkpoint is value-equal to the input checkpoint.

### `BoundedDevelopmentController.step(state, checkpoint)`

Pure single-step scheduling:

1. validate `RunState` and `ControllerCheckpoint` types;
2. require checkpoint `run_id == state.run_id`;
3. if core phase is COMPLETE/BLOCKED/AWAITING_HUMAN, return the matching stop without consuming budget;
4. check global step budget;
5. map the phase to one action;
6. check phase-specific implementation/review budget;
7. return one action and a new checkpoint with exactly the relevant counters incremented.

The function never calls `apply_event`; events remain evidence supplied by the host after actual work. This separation prevents scheduling from pretending work completed.

## TDD sequence

### RED 1 — normal deterministic scheduling

Tests for all six normal phases, stable mapping, run binding, immutable inputs, one increment per call.

### RED 2 — terminal / pause behavior

Tests for COMPLETE, BLOCKED, AWAITING_HUMAN. No budgets consumed.

### RED 3 — bounds

Tests for:

- global step exhaustion;
- implementation-attempt exhaustion;
- review-attempt exhaustion;
- precedence: exceptional/core terminal phases stop without being masked by an exhausted global budget.

### RED 4 — restartability / validation

Tests for:

- checkpoint JSON round trip;
- same state + restored checkpoint → same decision;
- cross-run checkpoint reuse rejected;
- unknown fields / schema version rejected;
- bool-as-int rejected;
- invalid limits/counters rejected.

### RED 5 — integration with existing reducer

Construct a small lifecycle using existing `apply_event` semantics to prove:

- failed verification routes to IMPLEMENT and consumes a second implementation attempt when scheduled;
- explicit `ResumeRequested` is required before controller scheduling resumes from a pause;
- review request-changes routes back to IMPLEMENT without controller-owned phase mutation.

## Implementation order

1. add tests only and commit RED state;
2. run agent test workflow and capture expected failure because controller module/API is absent;
3. implement `development_controller.py` only;
4. run targeted/full agent tests;
5. inspect exact diff against this plan;
6. perform logically independent adversarial review focusing on serialization tampering, off-by-one bounds, auto-resume, run mismatch, and hidden side effects;
7. turn any review blocker into a RED regression before fixing;
8. require green exact-head CI before merge.

## Non-goals

- no automatic `while`/forever loop;
- no change to `RunState` or `state_machine.apply_event` unless a demonstrated contract bug blocks HARN-010;
- no provider SDK calls;
- no GitHub write execution;
- no human-approval interpretation;
- no persistence backend (the checkpoint is a strict persistable value; storage remains host-owned);
- no parser/domain-specific policy.

## Adversarial questions for final review

- Can a boolean bypass integer limit validation?
- Is `max_steps=N` exactly N issued work actions, not N+1?
- Can IMPLEMENT or REVIEW exceed their phase-specific budget while global budget remains?
- Does a paused/complete run consume or mutate counters?
- Can checkpoint data from another run be replayed?
- Can unknown serialized fields silently alter future semantics?
- Does restart change the selected action or counters?
- Does controller code call external effects or manufacture lifecycle events?
- Can an exceptional phase auto-resume because an action mapping falls through?
- Are prior immutable checkpoint values modified in place?
