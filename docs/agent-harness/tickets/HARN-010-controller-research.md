# HARN-010 — bounded development-controller research

## Scope

HARN-010 is the execution/orchestration layer for the GitHub-issue-driven development loop. It is **not** a parser runtime and must not own CUC token/column semantics.

Target flow:

`issue -> research -> plan -> tests/RED -> implement -> tests/evals -> clean-context independent review -> finalization`

The controller must be bounded, durable, reconstructable, and fail closed around GitHub side effects.

## Existing authoritative boundaries

### HARN-002: development domain state

`agent/harness/contracts.py` and `agent/harness/state_machine.py` already define the authoritative development state machine:

- `RunState` is the durable domain snapshot;
- `RunPhase` owns research/plan/test-design/implement/verify/review/complete plus blocked/awaiting-human states;
- `apply_event()` is the only phase-transition reducer;
- test/eval evidence is bound to the current change, proposed head, and executed revision;
- verification requires every declared test to have a current successful result and all current evals to be successful;
- review approval/request-changes/escalation already routes to COMPLETE/IMPLEMENT/AWAITING_HUMAN.

HARN-010 therefore must **not** create a parallel development phase machine. It should drive HARN-002 events and persist controller-only execution metadata separately.

One missing integration seam is a truthful way for the controller to pause for non-test execution blocks or side-effect approval. Fabricating `TestResult(BLOCKED_EXECUTION)` or `TestResult(HUMAN_ESCALATION)` when no test was attempted would conflate execution control with test evidence. If needed, the minimal safe extension is generic reducer events that enter BLOCKED/AWAITING_HUMAN from a resumable phase without inventing gate evidence.

### HARN-006: independent development review

`agent/harness/development_reviewer.py` already owns the clean-context review boundary:

- allowlisted task/change/diff/test/eval/policy/rubric context;
- deterministic context identity;
- exact head/executed-revision binding;
- reviewer identity separated from implementer identity;
- structured findings projected to the HARN-002 `ReviewResult` contract;
- review application returns through the HARN-002 reducer.

HARN-010 should construct this context only after HARN-002 reaches REVIEW and should never give the reviewer implementer chain-of-thought or a privileged GitHub adapter.

### HARN-009: GitHub side-effect boundary

`agent/harness/github_effects.py` is the only trusted write boundary:

- closed GitHub action vocabulary;
- exact fork `operation_id -> action` permissions;
- separately declared upstream operation IDs;
- request-digest-bound human approval for upstream writes;
- durable uncertainty checkpoint **before** dispatch;
- receipt-based replay without duplicate execution;
- ambiguous post-dispatch outcomes are quarantined as `GitHubEffectOutcomeUnknown`.

HARN-010 must not expose the raw write adapter or unrestricted GitHub transport to model-driven research/implementation helpers.

`HumanApproval` is an authenticated host input. The controller may validate and persist an approval supplied by trusted host code, but must never synthesize approval from model output, prompt text, or a generic task artifact.

### HARN-007: orchestration-framework decision

The accepted ADR keeps HARN-010 framework-neutral. Deep Agents may later be used behind these boundaries for open-ended helper work, but it must not own durable state, independent-review acceptance, or GitHub authorization.

## Controller responsibilities that are not yet represented by HARN-002

The controller needs a separate durable envelope around `RunState` containing only execution-control concerns:

1. **Issue identity/provenance**
   - fork repository and issue number/reference;
   - original issue/body digest or equivalent immutable provenance;
   - evidence/provenance refs propagated by HARN-017 when present.

2. **Bounds/policy**
   - maximum controller steps;
   - maximum implementation/review revision cycles;
   - optional cost budget in normalized units when adapters report cost;
   - explicit no-feature fallback policy;
   - GitHub task policy from HARN-009.

3. **Usage/counters**
   - steps consumed;
   - revision cycles consumed;
   - accumulated adapter-reported cost;
   - current terminal/pause reason.

4. **Side-effect durability**
   - `GitHubEffectJournal`;
   - exact pending `HumanApprovalChallenge`, if any;
   - finalization receipt/result reference;
   - ambiguous-write quarantine survives serialization.

5. **Auditability**
   - append-only controller audit entries with phase/action/outcome and artifact or operation refs;
   - serialized controller snapshot must be enough to reconstruct why execution stopped.

The envelope must not duplicate research/plan/change/test/eval/review artifacts already present in `RunState`.

## Adapter boundary

The controller should receive narrow callbacks rather than one generic omnipotent agent/tool object. Candidate adapters:

- research: `RunState -> ResearchArtifact`;
- plan: `RunState -> PlanArtifact`;
- test design: `RunState -> tuple[TestIntent, ...]`;
- implementation: `RunState -> ChangeSet`;
- verification executor: executes declared test/eval work and returns typed HARN-002 results;
- review material provider: supplies objective `base_sha`, `head_sha`, final diff, policy refs and rubric;
- independent reviewer: existing HARN-006 `IndependentReviewer`;
- finalization request builder: returns a typed HARN-009 `GitHubEffectRequest` for the already-approved HARN-002 COMPLETE state.

Only trusted host/controller code owns `GitHubEffectGateway` and its write adapter. Model-facing callbacks receive no gateway or raw adapter.

## Bounded-loop semantics

A controller invocation may advance multiple deterministic phases, but every iteration consumes a step budget. It must stop with an explicit terminal/pause disposition when:

- HARN-002 reaches COMPLETE and finalization has a durable receipt;
- an adapter reports/raises a classified block;
- human approval is required;
- GitHub effect outcome becomes uncertain;
- maximum steps is exhausted;
- maximum revision cycles is exhausted;
- cost budget is exhausted;
- a policy invariant is violated.

Review rejection and test/eval failure are retryable only while revision budget remains. They must never form an unbounded loop.

A revision cycle is consumed when work returns to IMPLEMENT after a current failed/regressed verification gate or a `REQUEST_CHANGES` review. Initial implementation does not count as a retry cycle.

## Failure classification

Keep objective gate semantics separate from controller execution status:

- `test-failure` / `regression`: real HARN-002 `TestResult`/`EvalResult` outcomes;
- `blocked-execution`: controller cannot execute a required external action and safely pauses;
- `needs-human`: explicit approval/escalation interrupt;
- `policy-block`: controller policy/budget/safety prevents continuation;
- `outcome-unknown`: a GitHub mutation may have occurred; automatic retry forbidden.

Every non-success stop must persist an explicit reason.

## No-feature fallback

The user-requested fallback (performance, stability, ergonomics, documentation, edge cases) must be opt-in controller policy, not an implicit agent behavior.

A minimal safe contract is an enum/closed set of fallback categories and a policy flag. The controller may accept a fallback task only when enabled and only from the closed allowed set. It must not silently rewrite the objective of a feature issue into fallback work.

## TDD attack surface

Before implementation, scenario tests should prove at least:

- straight-through success uses HARN-002 transitions and one clean-context review;
- test failure returns to implementation and consumes bounded revision budget;
- repeated failure terminates with explicit policy reason at the limit;
- review rejection follows the same bounded revision accounting;
- blocked execution persists BLOCKED + resume target without fake test evidence;
- human approval challenge persists AWAITING_HUMAN and resumes only after an exact trusted approval is supplied;
- approval for another operation/digest is rejected;
- GitHub uncertain outcome remains quarantined across serialize/restore;
- receipt replay does not dispatch the adapter twice;
- stale proposed/executed revision evidence cannot be accepted (delegate to HARN-002, with controller integration regression);
- cost/step budgets terminate safely;
- disabled no-feature fallback is rejected;
- controller snapshot round-trips with issue provenance, counters, HARN-002 state and GitHub journal intact;
- model/helper adapters cannot access the raw write adapter through controller API;
- HARN-017 provenance refs survive issue -> controller task context without becoming reviewer approval or hidden model context.

## Implementation direction

Create a stdlib-only `agent/harness/development_controller.py`.

Prefer explicit methods / one bounded `run()` dispatcher over introducing LangGraph/Deep Agents as a correctness dependency. Any later orchestration framework can call the same controller methods.

If HARN-002 needs generic pause events, add only those events to `state_machine.py`; do not add controller budgets, GitHub journals, or framework concerns to `RunState`.

## Independent review rubric

Reject if:

- controller bypasses `apply_event()` for normal phase changes;
- model callbacks can construct trusted human approval or receive raw GitHub write transport;
- test/eval pass can be claimed without typed executed HARN-002 evidence;
- review receives implementer conversational context;
- revision/step/cost retry can become unbounded;
- an ambiguous GitHub effect can auto-retry;
- finalization can run before HARN-002 COMPLETE;
- issue/provenance identity can silently change after start;
- no-feature fallback activates implicitly;
- parser/column scope logic appears in the development controller.
