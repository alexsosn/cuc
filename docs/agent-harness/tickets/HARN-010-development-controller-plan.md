# HARN-010 — bounded development controller implementation plan

## Architecture

Implement `agent/harness/development_controller.py` as a framework-neutral deterministic orchestration layer around existing HARN contracts. It does not replace HARN-002 `RunState`/`apply_event`; it owns only controller-specific gates, budgets, persistence, and port invocation.

### Durable controller envelope

`DevelopmentControllerState` contains:

- schema version;
- exact baseline SHA;
- embedded HARN-002 `RunState`;
- pre-change `RedGateEvidence` keyed by targeted test intent;
- implementation/revision attempt count;
- verification execution count;
- review attempt count;
- GitHub side-effect count;
- accumulated cost units;
- audit events/evidence refs;
- optional explicit terminal code/reason.

It supports strict `to_dict` / `from_dict` / canonical JSON round-trip. State is persisted after every accepted transition/counter mutation.

### Policy

`DevelopmentControllerPolicy` makes bounds explicit:

- `max_revision_attempts`;
- `max_verification_executions`;
- `max_review_attempts`;
- `max_github_writes`;
- optional `max_cost_units`;
- `require_red` (default true);
- `allow_no_feature_fallback` (default false);
- `production_mode` (default true).

No controller loop uses an unbounded `while True`; every step consumes a phase or a bounded counter.

### Ports

Inject deterministic/testable ports rather than framework-specific agents:

- research;
- planning;
- test declaration;
- pre-change RED execution;
- implementation;
- post-change test execution;
- optional eval execution;
- final diff loader;
- HARN-006 `IndependentReviewer`;
- HARN-009 `GuardedGitHubSideEffects` for every GitHub write;
- controller-state persistence.

Model-facing implementation/planning ports may propose structured artifacts/intents but receive neither raw GitHub transport nor approval-registry mutation authority.

### Implementation result

An implementation port returns a structured result containing:

- HARN-002 `ChangeSet`;
- exact proposed `head_sha`;
- optional HARN-009 GitHub operation intents;
- cost units consumed;
- evidence refs.

Any proposed GitHub operation ID must be declared by the returned `ChangeSet`; the controller dispatches it only through `GuardedGitHubSideEffects` and checks GitHub-write budget before each operation.

## TDD/RED semantics

After HARN-002 `TestsDeclared` moves the core state to `IMPLEMENT`, the controller still blocks the implementation port until RED evidence exists.

For the first implementation attempt when `require_red` is true:

- every `TestKind.TARGETED` intent is executed against the exact baseline SHA;
- each targeted command must return a real `TEST_FAILURE` with non-zero exit and at least one failed test;
- no generated/model assertion can substitute for execution evidence;
- regression intents are not required to fail at baseline;
- baseline SHA mismatch is rejected.

The RED evidence is durable and not re-run after reviewer-driven revisions unless test declarations change (which HARN-002 currently does not allow in-run).

## Step semantics

A `step()` call performs at most one logical controller action and persists its result. `run_until_stop()` repeatedly calls `step()` only up to a caller/policy-derived finite step ceiling.

Expected phase actions:

1. `RESEARCH`: call research port -> `ResearchRecorded`.
2. `PLAN`: call planning port -> `PlanRecorded`.
3. `TEST_DESIGN`: call test-design port -> `TestsDeclared`.
4. `IMPLEMENT` with missing RED: execute one missing targeted RED probe and persist it; when RED gate complete, a later step may implement.
5. `IMPLEMENT` with RED satisfied: check revision/cost budgets; call implement port; dispatch declared GitHub intents through HARN-009; record `ChangeRecorded`.
6. `VERIFY`: run one missing current test/eval at a time. Any real failure is routed through HARN-002 back to `IMPLEMENT`. When all required current tests/evals succeed, emit `VerificationPassed`.
7. `REVIEW`: build HARN-006 clean review context from verified evidence + exact final diff, call `run_independent_development_review`, then route its core review through HARN-002. APPROVE completes; REQUEST_CHANGES returns to bounded implementation; ESCALATE waits for human.
8. `BLOCKED` / `AWAITING_HUMAN` / `COMPLETE`: no autonomous action unless explicit resume is supplied where legal.

## Budgets and stop reasons

Before a port call, check the relevant limit. Exhaustion creates a persisted terminal/blocked controller reason such as:

- `revision-budget-exhausted`;
- `verification-budget-exhausted`;
- `review-budget-exhausted`;
- `github-write-budget-exhausted`;
- `cost-budget-exhausted`;
- `blocked-execution`;
- `policy-block`;
- `needs-human`;
- `complete`.

Terminal reasons are data, not prose-only logs.

## HARN-009 production wiring

Close follow-up #51 as part of HARN-010. Production mode must reject a side-effect capability whose operation journal is not durably configured. The controller code must import/use `github_side_effects.GuardedGitHubSideEffects`, not legacy `github_effects.GitHubEffectGateway`.

The host remains responsible for constructing/restoring the trusted approval registry and deterministic reconciliation-capable adapter. The controller never receives approval registration or raw provider transport.

If a GitHub execution raises `ApprovalRequired`, controller state becomes explicit `AWAITING_HUMAN` before any further autonomous phase action. Resume must retry the same operation ID; HARN-009 supplies replay/reconciliation safety.

## Scenario RED tests before implementation

1. happy path enforces research -> plan -> test declaration -> real RED -> implementation -> fresh tests -> clean review -> complete;
2. implementation port is never called before RED;
3. fake/model-only RED evidence or baseline mismatch is rejected;
4. repeated test failure terminates at revision budget rather than looping;
5. reviewer rejection causes a fresh bounded implementation/verification/review cycle;
6. blocked dependency produces explicit blocked reason;
7. stale head/executed revision mismatch cannot advance to review;
8. cost/revision/test/review/GitHub-write budgets stop before the over-budget port call;
9. approval-required side effect pauses in `AWAITING_HUMAN`; resume keeps the same operation identity and does not duplicate an already completed effect;
10. state serialization/restart continues from the exact next gate without repeating research/plan/RED/completed effects;
11. HARN-006 reviewer receives only its allowlisted clean context, not controller scratch/audit history;
12. production mode rejects in-memory HARN-009 journal wiring;
13. controller source does not import/use legacy `GitHubEffectGateway`;
14. every terminal state/reason round-trips through persisted state;
15. no-feature fallback cannot activate unless policy explicitly enables it.

## Review strategy

After full-suite GREEN, perform a logically independent adversarial review from issue #11 + final diff + CI evidence only. Attack at least:

- off-by-one retry/budget loops;
- fabricated RED/test/review success;
- stale revision evidence;
- restart duplication;
- GitHub write bypass through legacy gateway/raw adapter;
- forged human approval/resume;
- state persistence failure windows;
- hidden unbounded execution path;
- issue/provenance drift;
- fallback scope expansion.

Every blocker gets a new RED regression before the fix, followed by full suite and fresh re-review.
