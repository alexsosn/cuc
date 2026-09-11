# HARN-010 — bounded development controller research

Issue: #11

## Question

What is the smallest controller layer that can coordinate the already-implemented durable contracts, pure transition reducer, independent reviewer, and guarded GitHub effect boundary without creating a second state machine or an unbounded agent loop?

## Existing architecture

### Durable lifecycle is already owned by `RunState`

`agent/harness/contracts.py` defines the canonical phases:

`RESEARCH → PLAN → TEST_DESIGN → IMPLEMENT → VERIFY → REVIEW → COMPLETE`

with exceptional `BLOCKED` and `AWAITING_HUMAN` phases. `RunState` validates durable evidence and remains the source of truth for workflow state.

### Transition validity is already owned by `state_machine.apply_event`

`agent/harness/state_machine.py` is a pure reducer. It already enforces research-before-plan, plan-before-tests, fresh verification evidence, revision pinning, review routing, and explicit resume from exceptional states.

A controller that re-implements these rules would create two authorities for lifecycle semantics and eventually drift.

### Independent review is already a separate boundary

`agent/harness/development_reviewer.py` constructs an allowlisted clean review context, validates a structured review report, and projects the result onto the existing `ReviewResult` / `ReviewRecorded` transition. HARN-010 should schedule review, not reinterpret review evidence or dispositions.

### GitHub side effects are already guarded

HARN-009 introduced the closed GitHub capability boundary with trusted approval handling and replay-safe durable journaling. HARN-010 must not add a generic network/CLI escape hatch or duplicate side-effect authorization logic. Its job is to decide when an effectful step is eligible; execution remains delegated to guarded boundaries.

## Missing layer

The repository has lifecycle semantics but no bounded scheduler that answers deterministically what the host should do next and persists the control metadata needed to resume after process death.

The missing data are controller-specific rather than domain evidence:

- maximum number of scheduled work steps for one run;
- maximum implementation attempts;
- maximum review attempts;
- counters already consumed;
- terminal controller stop reason when a bound is exhausted.

These do not belong in `RunState`: they are orchestration policy, not evidence about the development task.

## Design decision

Implement HARN-010 as a **single-step deterministic controller** over `RunState` plus a serializable `ControllerCheckpoint`.

The controller does not run an internal `while` loop. Each call selects exactly one next action or one stop result. The trusted host performs that action, obtains a typed event/evidence object, feeds it through `apply_event`, persists the resulting `RunState` and checkpoint, then calls the controller again.

This provides a hard scheduling boundary: a caller cannot accidentally start an invisible unbounded agent loop inside the controller.

## Deterministic phase-to-action mapping

| Run phase | next controller decision |
| --- | --- |
| RESEARCH | action `RESEARCH` |
| PLAN | action `PLAN` |
| TEST_DESIGN | action `DESIGN_TESTS` |
| IMPLEMENT | action `IMPLEMENT` |
| VERIFY | action `VERIFY` |
| REVIEW | action `REVIEW` |
| BLOCKED | stop `BLOCKED` |
| AWAITING_HUMAN | stop `AWAITING_HUMAN` |
| COMPLETE | stop `COMPLETE` |

Resume is explicit rather than inferred: the host must first apply `ResumeRequested` after the blocking condition or human approval is actually resolved. The controller must never auto-resume an exceptional state.

## Bounds

Three independent positive integer limits are sufficient for this ticket:

1. `max_steps`: global safety cap across decisions that request work;
2. `max_implementation_attempts`: cap on IMPLEMENT actions;
3. `max_review_attempts`: cap on REVIEW actions.

Counters increment when an action is issued, not when the following event succeeds. This prevents repeated crashes/retries from obtaining unlimited work simply because no state transition was persisted afterward.

When a limit is exhausted, the controller returns a terminal `LIMIT_REACHED` decision and does not mutate `RunState`. Widening limits requires an explicit new checkpoint/policy operation outside `step()`.

## Restartability and serialization

`ControllerCheckpoint` must round-trip through a strict versioned JSON-compatible representation. A restored checkpoint plus restored `RunState` produces the same next decision as before process death.

The checkpoint contains orchestration facts and is bound to the run ID. Reusing a checkpoint for another run fails closed.

Unknown schema versions, unknown fields, booleans masquerading as integers, negative counters, zero/negative limits, counters greater than limits, and inconsistent terminal data must be rejected.

## Relationship to effect execution

The controller returns typed intent to perform a category of work, not a provider request. It does not call GitHub, a model, a shell, the reviewer, or the filesystem.

Consequences:

- no side effect occurs during `step()`;
- approvals cannot be bypassed through controller logic;
- testing the controller requires no live services;
- crash/restart behavior can be tested with value equality.

## Failure and retry semantics

Retries are already represented by the core reducer:

- failed test/eval → `IMPLEMENT`;
- review request-changes → `IMPLEMENT`;
- blocked execution → `BLOCKED` with `resume_phase`;
- reviewer escalation → `AWAITING_HUMAN` with `resume_phase`.

The controller only accounts for how many IMPLEMENT/REVIEW opportunities have been consumed. It must not manufacture events to force a retry.

## Rejected alternatives

### A second controller phase enum mirroring `RunPhase`

Rejected because it duplicates lifecycle state and makes recovery ambiguous when the two enums disagree.

### A controller-owned internal loop

Rejected because bounds become less observable, persistence must happen inside the loop, and interruption can replay work between hidden iterations.

### Embedding retry counters in `RunState`

Rejected because retry budgets are orchestration policy and would couple the domain contract to one controller implementation.

### Auto-resume from `AWAITING_HUMAN`

Rejected because approval/block resolution must be established by trusted host logic; a scheduler cannot infer it safely.

### Calling GitHub/reviewer/model adapters directly from the reducer

Rejected because it makes state transitions non-pure and weakens existing capability boundaries.

## TDD implications

Tests should cover:

- deterministic mapping for every normal phase;
- complete stop;
- blocked and human-pause stops with no counter consumption;
- global step bound;
- implementation retry bound after verification failure;
- review retry bound across request-changes cycles;
- checkpoint serialization/restart equivalence;
- run-ID mismatch rejection;
- invalid/tampered checkpoint rejection;
- no mutation of input `RunState` or prior checkpoint;
- explicit resume using existing `ResumeRequested`;
- one call issuing at most one action.

## Scope boundary

HARN-010 does not implement parser-specific workflow logic, a model runtime, shell execution, GitHub transport, reviewer prompting, release automation, or a long-running daemon. Those can be composed around the controller later while preserving the bounded single-step contract.
